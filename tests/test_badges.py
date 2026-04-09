"""
Module 14 — Nav Badge Counts (12 tests)

Covers: GET /api/v1/nav/badge-counts/ response structure, count accuracy
for tickets/emails/calendar/messages, capping at 99, tenant isolation,
and role-scoped counts.
"""

import uuid
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from freezegun import freeze_time

from apps.comments.models import Comment, CommentRead
from apps.inbound_email.models import InboundEmail
from apps.tickets.models import Ticket, TicketStatus
from main.context import set_current_tenant

from tests.base import KanzenBaseTestCase


BADGE_URL = "/api/v1/nav/badge-counts/"


class BadgeCountBaseTests(KanzenBaseTestCase):
    """Tests for the sidebar badge-count endpoint."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.auth_tenant(self.admin_a, self.tenant_a)

    def _get_badges(self):
        return self.client.get(BADGE_URL)

    def _create_inbound_email(self, tenant, inbox_status="pending", **kwargs):
        """Helper to create an InboundEmail record."""
        defaults = {
            "tenant": tenant,
            "message_id": f"{uuid.uuid4()}@test.com",
            "sender_email": "sender@example.com",
            "recipient_email": "support@example.com",
            "subject": "Test email",
            "body_text": "Test body",
            "inbox_status": inbox_status,
        }
        defaults.update(kwargs)
        return InboundEmail.objects.create(**defaults)

    # 14.1 Returns all 4 keys
    def test_14_01_returns_all_four_keys(self):
        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        for key in ("tickets", "calendar", "messages", "emails"):
            self.assertIn(key, resp.data, f"Missing key: {key}")

    # 14.2 tickets count = open + in-progress (not resolved/closed)
    def test_14_02_tickets_count_open_and_in_progress(self):
        # Create tickets in different statuses
        self.create_ticket(status=self.status_open_a)
        self.create_ticket(status=self.status_in_progress_a)
        self.create_ticket(status=self.status_resolved_a)
        self.create_ticket(status=self.status_closed_a)

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        # open + in-progress + waiting = 3 (not resolved, not closed)
        # Note: waiting is also not closed/resolved
        tickets_count = resp.data["tickets"]
        # At least the open and in-progress should be counted
        self.assertGreaterEqual(tickets_count, 2)
        # Resolved and closed should NOT be counted — so less than total
        self.assertLess(tickets_count, 4)

    # 14.3 emails count = pending + linked InboundEmails only
    def test_14_03_emails_count_pending_and_linked(self):
        self._create_inbound_email(self.tenant_a, inbox_status="pending")
        self._create_inbound_email(self.tenant_a, inbox_status="linked")
        self._create_inbound_email(self.tenant_a, inbox_status="actioned")
        self._create_inbound_email(self.tenant_a, inbox_status="ignored")

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["emails"], 2)

    # 14.4 calendar count = activities due today or overdue, not completed
    def test_14_04_calendar_count(self):
        """Calendar count depends on CRM Activity model; skip if not available."""
        try:
            from apps.crm.models import Activity
        except ImportError:
            self.skipTest("CRM Activity model not available")

        now = timezone.now()

        # Overdue, incomplete activity assigned to current user
        Activity.objects.create(
            activity_type="task",
            subject="Overdue task",
            due_at=now - timedelta(hours=2),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )
        # Due today, incomplete
        Activity.objects.create(
            activity_type="call",
            subject="Due today",
            due_at=now + timedelta(hours=1),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )
        # Completed (should not count)
        Activity.objects.create(
            activity_type="meeting",
            subject="Done meeting",
            due_at=now - timedelta(hours=1),
            completed_at=now,
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )
        # Due tomorrow (should not count)
        Activity.objects.create(
            activity_type="email",
            subject="Future task",
            due_at=now + timedelta(days=2),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["calendar"], 2)

    # 14.5 messages count = unread comments on assigned tickets
    def test_14_05_messages_count_unread_comments(self):
        ticket = self.create_ticket(
            status=self.status_open_a,
            assignee=self.admin_a,
        )
        ticket_ct = ContentType.objects.get_for_model(Ticket)

        # Comment by agent (not admin_a) — should count as unread
        self.set_tenant(self.tenant_a)
        comment = Comment.objects.create(
            content_type=ticket_ct,
            object_id=ticket.pk,
            body="Need help with this",
            author=self.agent_a,
            is_internal=False,
            tenant=self.tenant_a,
        )

        # Own comment — should NOT count
        Comment.objects.create(
            content_type=ticket_ct,
            object_id=ticket.pk,
            body="My own comment",
            author=self.admin_a,
            is_internal=False,
            tenant=self.tenant_a,
        )

        # Internal note by agent — should NOT count (is_internal=True)
        Comment.objects.create(
            content_type=ticket_ct,
            object_id=ticket.pk,
            body="Internal note",
            author=self.agent_a,
            is_internal=True,
            tenant=self.tenant_a,
        )

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["messages"], 1)

    # 14.6 All counts are integers, never null
    def test_14_06_counts_are_integers_never_null(self):
        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        for key in ("tickets", "calendar", "messages", "emails"):
            val = resp.data[key]
            self.assertIsNotNone(val, f"{key} should not be null")
            self.assertIsInstance(val, int, f"{key} should be int, got {type(val)}")

    # 14.7 Count returns 99 when actual count > 99
    def test_14_07_count_capped_at_99(self):
        # Create 101 open tickets
        tickets = [
            Ticket(
                subject=f"Ticket {i}",
                description="Bulk",
                status=self.status_open_a,
                created_by=self.admin_a,
                tenant=self.tenant_a,
            )
            for i in range(101)
        ]
        # Use bulk_create — numbers are assigned in save() so we need
        # to set them manually for bulk creation
        for i, t in enumerate(tickets, start=9000):
            t.number = i
        Ticket.unscoped.bulk_create(tickets)

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["tickets"], 99)

    # 14.8 Actioning an email → emails badge count decreases
    def test_14_08_actioning_email_decreases_count(self):
        email = self._create_inbound_email(self.tenant_a, inbox_status="pending")

        resp = self._get_badges()
        initial_count = resp.data["emails"]
        self.assertGreaterEqual(initial_count, 1)

        # Action the email (change from pending to actioned)
        email.inbox_status = InboundEmail.InboxStatus.ACTIONED
        email.save(update_fields=["inbox_status"])

        resp = self._get_badges()
        self.assertEqual(resp.data["emails"], initial_count - 1)

    # 14.9 Marking comments read → messages badge count decreases
    def test_14_09_marking_comments_read_decreases_count(self):
        ticket = self.create_ticket(
            status=self.status_open_a,
            assignee=self.admin_a,
        )
        ticket_ct = ContentType.objects.get_for_model(Ticket)

        self.set_tenant(self.tenant_a)
        comment = Comment.objects.create(
            content_type=ticket_ct,
            object_id=ticket.pk,
            body="Unread comment",
            author=self.agent_a,
            is_internal=False,
            tenant=self.tenant_a,
        )

        resp = self._get_badges()
        self.assertEqual(resp.data["messages"], 1)

        # Mark as read
        CommentRead.objects.create(
            comment=comment,
            user=self.admin_a,
        )

        resp = self._get_badges()
        self.assertEqual(resp.data["messages"], 0)

    # 14.10 Completing an activity → calendar badge count decreases
    def test_14_10_completing_activity_decreases_calendar(self):
        try:
            from apps.crm.models import Activity
        except ImportError:
            self.skipTest("CRM Activity model not available")

        now = timezone.now()
        activity = Activity.objects.create(
            activity_type="task",
            subject="Due task",
            due_at=now - timedelta(hours=1),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )

        resp = self._get_badges()
        initial_cal = resp.data["calendar"]
        self.assertGreaterEqual(initial_cal, 1)

        activity.completed_at = now
        activity.save(update_fields=["completed_at"])

        resp = self._get_badges()
        self.assertEqual(resp.data["calendar"], initial_cal - 1)


class BadgeTenantIsolationTests(KanzenBaseTestCase):
    """Tests for tenant scoping and role-based badge counts."""

    def setUp(self):
        super().setUp()

    def _get_badges(self):
        return self.client.get(BADGE_URL)

    # 14.11 Counts are tenant-scoped
    def test_14_11_counts_tenant_scoped(self):
        # Create tickets in tenant A
        self.set_tenant(self.tenant_a)
        self.create_ticket(status=self.status_open_a)
        self.create_ticket(status=self.status_open_a)

        # Create ticket in tenant B
        self.set_tenant(self.tenant_b)
        status_open_b = TicketStatus.unscoped.get(
            tenant=self.tenant_b, is_default=True,
        )
        self.create_ticket(
            tenant=self.tenant_b,
            user=self.admin_b,
            status=status_open_b,
        )

        # Tenant A should see only their tickets
        self.auth_tenant(self.admin_a, self.tenant_a)
        resp = self._get_badges()
        count_a = resp.data["tickets"]

        # Tenant B should see only their tickets
        self.auth_tenant(self.admin_b, self.tenant_b)
        resp = self._get_badges()
        count_b = resp.data["tickets"]

        # They should differ (A has 2, B has 1)
        self.assertGreaterEqual(count_a, 2)
        self.assertEqual(count_b, 1)

    # 14.12 Agent gets role-scoped counts (own tickets only)
    def test_14_12_agent_gets_role_scoped_counts(self):
        self.set_tenant(self.tenant_a)

        # Ticket assigned to agent
        self.create_ticket(
            status=self.status_open_a,
            assignee=self.agent_a,
        )
        # Ticket created by agent (no assignee)
        self.create_ticket(
            status=self.status_open_a,
            user=self.agent_a,
        )
        # Ticket NOT related to agent at all
        self.create_ticket(
            status=self.status_open_a,
            assignee=self.admin_a,
        )

        # Admin sees all open tickets
        self.auth_tenant(self.admin_a, self.tenant_a)
        resp = self._get_badges()
        admin_ticket_count = resp.data["tickets"]

        # Agent should see fewer tickets (only own)
        self.auth_tenant(self.agent_a, self.tenant_a)
        resp = self._get_badges()
        agent_ticket_count = resp.data["tickets"]

        self.assertGreater(admin_ticket_count, agent_ticket_count)
        # Agent should see at least the 2 tickets they're related to
        self.assertGreaterEqual(agent_ticket_count, 2)
