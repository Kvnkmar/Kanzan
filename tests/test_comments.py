"""
Module 6 — Comments & Internal Notes Tests (12 tests)

Tests for public/internal comments, first response tracking,
dual-write logging, threading, cross-tenant isolation, and read tracking.
"""

import unittest

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from freezegun import freeze_time

from apps.comments.models import ActivityLog, Comment, CommentRead
from apps.tickets.models import Ticket, TicketActivity
from apps.tickets.services import initialize_sla, record_first_response
from tests.base import KanzenBaseTestCase


class TestPublicAndInternalComments(KanzenBaseTestCase):
    """6.1–6.2 — Public comments and internal notes visibility."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.agent_a,
        )

    def test_6_1_agent_posts_public_comment(self):
        """Agent posts public comment -> 201, visible in list."""
        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url(f"/tickets/tickets/{self.ticket.pk}/comments/")

        response = self.client.post(url, {
            "body": "Hello, we are working on your issue.",
            "is_internal": False,
        })
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data["is_internal"])

        # Verify comment exists in the GET list
        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)
        comments = get_response.data.get("results", get_response.data)
        bodies = [c["body"] for c in comments]
        self.assertIn("Hello, we are working on your issue.", bodies)

    def test_6_2_internal_note_not_visible_to_agent(self):
        """Internal note posted by admin is not visible to agents (hierarchy > 20)."""
        # Admin posts an internal note
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(f"/tickets/tickets/{self.ticket.pk}/comments/")

        response = self.client.post(url, {
            "body": "Internal: check with engineering team.",
            "is_internal": True,
        })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["is_internal"])

        # Agent should NOT see internal notes (hierarchy_level=30 > 20)
        # Note: The view code filters out is_internal for hierarchy > 20
        # But since the agent is the assignee, they can see the ticket comments
        # The key question is whether is_internal comments are excluded
        self.auth_tenant(self.agent_a, self.tenant_a)
        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)
        comments = get_response.data.get("results", get_response.data)
        internal_comments = [c for c in comments if c.get("is_internal", False)]
        self.assertEqual(len(internal_comments), 0)


class TestFirstResponseTracking(KanzenBaseTestCase):
    """6.3–6.4 — First response stamp on ticket via comment."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)

    @freeze_time("2026-04-05 10:00:00", tz_offset=0)
    def test_6_3_first_agent_reply_stamps_first_responded_at(self):
        """First public comment by a non-creator agent stamps first_responded_at."""
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.agent_a,
        )
        self.assertIsNone(ticket.first_responded_at)

        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url(f"/tickets/tickets/{ticket.pk}/comments/")
        response = self.client.post(url, {
            "body": "Looking into this now.",
            "is_internal": False,
        })
        self.assertEqual(response.status_code, 201)

        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.first_responded_at)

    @freeze_time("2026-04-05 10:00:00", tz_offset=0)
    def test_6_4_second_reply_does_not_overwrite_first_responded_at(self):
        """Second public comment should NOT overwrite first_responded_at."""
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.agent_a,
        )

        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url(f"/tickets/tickets/{ticket.pk}/comments/")

        # First reply
        self.client.post(url, {
            "body": "First reply.",
            "is_internal": False,
        })
        ticket.refresh_from_db()
        first_ts = ticket.first_responded_at
        self.assertIsNotNone(first_ts)

        # Second reply at a later time
        with freeze_time("2026-04-05 10:10:00", tz_offset=0):
            self.client.post(url, {
                "body": "Second reply.",
                "is_internal": False,
            })
            ticket.refresh_from_db()
            self.assertEqual(ticket.first_responded_at, first_ts)


class TestCommentDualWrite(KanzenBaseTestCase):
    """6.5 — Comment creates entries in both ActivityLog and TicketActivity."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.agent_a,
        )

    def test_6_5_comment_dual_writes_to_logs(self):
        """Posting a comment should write to both ActivityLog and TicketActivity."""
        ticket_ct = ContentType.objects.get_for_model(Ticket)

        activity_log_before = ActivityLog.unscoped.filter(
            content_type=ticket_ct,
            object_id=self.ticket.pk,
            action=ActivityLog.Action.COMMENTED,
        ).count()

        ticket_activity_before = TicketActivity.unscoped.filter(
            ticket=self.ticket,
            event=TicketActivity.Event.COMMENTED,
        ).count()

        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url(f"/tickets/tickets/{self.ticket.pk}/comments/")
        response = self.client.post(url, {
            "body": "This should be dual-logged.",
            "is_internal": False,
        })
        self.assertEqual(response.status_code, 201)

        # ActivityLog should have a COMMENTED entry
        activity_log_after = ActivityLog.unscoped.filter(
            content_type=ticket_ct,
            object_id=self.ticket.pk,
            action=ActivityLog.Action.COMMENTED,
        ).count()
        self.assertEqual(activity_log_after, activity_log_before + 1)

        # TicketActivity should have a COMMENTED entry
        ticket_activity_after = TicketActivity.unscoped.filter(
            ticket=self.ticket,
            event=TicketActivity.Event.COMMENTED,
        ).count()
        self.assertEqual(ticket_activity_after, ticket_activity_before + 1)


class TestThreadedReplies(KanzenBaseTestCase):
    """6.6 — Threaded reply with parent FK."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.agent_a,
        )

    def test_6_6_threaded_reply_parent_stored(self):
        """Comment with parent set stores parent_id correctly."""
        self.auth_tenant(self.agent_a, self.tenant_a)

        # Create parent comment via the generic comments endpoint
        ticket_ct = ContentType.objects.get_for_model(Ticket)
        comment_url = self.api_url("/comments/comments/")

        parent_resp = self.client.post(comment_url, {
            "content_type": "tickets.ticket",
            "object_id": str(self.ticket.pk),
            "body": "Parent comment.",
            "is_internal": False,
        })
        self.assertEqual(parent_resp.status_code, 201)
        parent_id = parent_resp.data["id"]

        # Create reply with parent
        reply_resp = self.client.post(comment_url, {
            "content_type": "tickets.ticket",
            "object_id": str(self.ticket.pk),
            "body": "This is a threaded reply.",
            "is_internal": False,
            "parent": parent_id,
        })
        self.assertEqual(reply_resp.status_code, 201)
        self.assertEqual(str(reply_resp.data["parent"]), str(parent_id))

        # Verify in the database
        self.set_tenant(self.tenant_a)
        reply_comment = Comment.objects.get(pk=reply_resp.data["id"])
        self.assertEqual(str(reply_comment.parent_id), str(parent_id))


class TestCommentPermissions(KanzenBaseTestCase):
    """6.7–6.8 — Permission checks for comments."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.agent_a,
        )

    def test_6_7_viewer_cannot_post_comment(self):
        """Viewer role (hierarchy_level=40) cannot post comments.
        The viewer can see the ticket (if they created it or are assigned),
        but posting comments may be restricted. If the endpoint returns
        201 for viewers, this is a potential access control gap."""
        self.auth_tenant(self.viewer_a, self.tenant_a)

        # Viewer is not the creator or assignee of this ticket
        # So they should not have access to comment on it
        url = self.api_url(f"/tickets/tickets/{self.ticket.pk}/comments/")
        response = self.client.post(url, {
            "body": "Viewer comment attempt.",
            "is_internal": False,
        })

        # Viewer should be denied access (403 or 404 since they
        # can't see the ticket either)
        self.assertIn(response.status_code, [403, 404])

    def test_6_8_cross_tenant_agent_cannot_comment(self):
        """Agent from tenant B cannot comment on tenant A ticket."""
        self.auth_tenant(self.agent_b, self.tenant_b)

        # Try to access tenant A ticket from tenant B context
        url = self.api_url(f"/tickets/tickets/{self.ticket.pk}/comments/")
        response = self.client.post(url, {
            "body": "Cross-tenant comment attempt.",
            "is_internal": False,
        })

        # Should be 403 or 404 (ticket does not exist in tenant B scope)
        self.assertIn(response.status_code, [403, 404])


class TestCommentReadTracking(KanzenBaseTestCase):
    """6.9–6.12 — Comment read tracking and unread counts."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.agent_a,
        )

    def test_6_9_mark_read_endpoint(self):
        """POST /api/v1/comments/comments/{id}/mark-read/ marks comment as read."""
        # Create a comment first
        self.auth_tenant(self.admin_a, self.tenant_a)
        ticket_url = self.api_url(f"/tickets/tickets/{self.ticket.pk}/comments/")
        resp = self.client.post(ticket_url, {
            "body": "Comment to mark read.",
            "is_internal": False,
        })
        self.assertEqual(resp.status_code, 201)
        comment_id = resp.data["id"]

        # Now mark it as read by a different user
        self.auth_tenant(self.agent_a, self.tenant_a)
        mark_url = self.api_url(f"/comments/comments/{comment_id}/mark-read/")
        mark_resp = self.client.post(mark_url)
        self.assertEqual(mark_resp.status_code, 200)

        # Verify CommentRead record exists
        read_exists = CommentRead.objects.filter(
            comment_id=comment_id,
            user=self.agent_a,
        ).exists()
        self.assertTrue(read_exists)

    def test_6_9_mark_read_idempotent(self):
        """Marking the same comment as read twice should not error."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        ticket_url = self.api_url(f"/tickets/tickets/{self.ticket.pk}/comments/")
        resp = self.client.post(ticket_url, {
            "body": "Idempotent read test.",
            "is_internal": False,
        })
        comment_id = resp.data["id"]

        self.auth_tenant(self.agent_a, self.tenant_a)
        mark_url = self.api_url(f"/comments/comments/{comment_id}/mark-read/")

        # Mark read twice
        first_resp = self.client.post(mark_url)
        self.assertEqual(first_resp.status_code, 200)
        second_resp = self.client.post(mark_url)
        self.assertEqual(second_resp.status_code, 200)

        # Should still have only one CommentRead record
        read_count = CommentRead.objects.filter(
            comment_id=comment_id,
            user=self.agent_a,
        ).count()
        self.assertEqual(read_count, 1)

    @unittest.skip("Not implemented: mark-all-read endpoint for comments")
    def test_6_10_mark_all_read(self):
        """mark-all-read marks all ticket comments read for requesting user."""
        pass

    @unittest.skip("Not implemented: mark-all-read endpoint for comments")
    def test_6_11_unread_count_drops_after_mark_all_read(self):
        """Unread count should drop to zero after mark-all-read."""
        pass

    @unittest.skip("Not implemented: unread badge count endpoint for comments")
    def test_6_12_author_own_comments_not_counted_in_unread(self):
        """Author's own comments should not be counted in unread badge."""
        pass
