"""
Module 11 — Audit Logging (Dual-Write)

Tests verifying that every ticket mutation writes to BOTH ActivityLog
(audit/compliance) and TicketActivity (ticket timeline), that dedup
mechanisms work correctly, and that both APIs behave as expected.
"""

import unittest

from django.contrib.contenttypes.models import ContentType

from apps.comments.models import ActivityLog
from apps.tickets.models import Ticket, TicketActivity
from tests.base import KanzenBaseTestCase


class TestAuditDualWrite(KanzenBaseTestCase):
    """Dual-write audit logging tests."""

    def setUp(self):
        super().setUp()
        self.ticket_ct = ContentType.objects.get_for_model(Ticket)

    # ------------------------------------------------------------------
    # 11.1 Every ticket mutation writes to BOTH ActivityLog and TicketActivity
    # ------------------------------------------------------------------
    def test_ticket_creation_writes_to_both_logs(self):
        """Creating a ticket via API produces entries in both log systems."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url("/tickets/tickets/")
        data = {
            "subject": "Dual-write test ticket",
            "description": "Testing dual-write on create",
            "priority": "medium",
            "status": str(self.status_open_a.pk),
        }
        resp = self.client.post(url, data, format="json")
        self.assertIn(resp.status_code, [200, 201], resp.content)

        ticket_id = resp.data["id"]

        # TicketActivity should have a 'created' event
        ta_count = TicketActivity.unscoped.filter(
            ticket_id=ticket_id,
            event=TicketActivity.Event.CREATED,
        ).count()
        self.assertGreaterEqual(ta_count, 1, "TicketActivity 'created' event missing")

        # ActivityLog should have a 'created' entry for this ticket
        al_count = ActivityLog.unscoped.filter(
            content_type=self.ticket_ct,
            object_id=ticket_id,
            action=ActivityLog.Action.CREATED,
        ).count()
        self.assertGreaterEqual(al_count, 1, "ActivityLog 'created' entry missing")

    # ------------------------------------------------------------------
    # 11.2 No double-write when ViewSet and signal both fire
    # ------------------------------------------------------------------
    def test_no_double_write_on_update(self):
        """
        Updating a tracked ticket field (priority) via API should produce
        exactly ONE ActivityLog entry for the change, not two
        (ViewSet + signal dedup via _skip_signal_logging).
        """
        self.auth_tenant(self.admin_a, self.tenant_a)

        # Create a ticket first
        ticket = self.create_ticket(self.tenant_a, self.admin_a, subject="Original")

        # Clear any creation-related logs
        ActivityLog.unscoped.filter(
            content_type=self.ticket_ct,
            object_id=ticket.pk,
        ).delete()
        TicketActivity.unscoped.filter(ticket=ticket).delete()

        # Update the ticket priority via PATCH (a tracked field that goes
        # through the service layer's dual-write path)
        url = self.api_url(f"/tickets/tickets/{ticket.pk}/")
        resp = self.client.patch(url, {"priority": "high"}, format="json")
        self.assertIn(resp.status_code, [200, 202], resp.content)

        # Expect exactly ONE ActivityLog entry for the priority change
        al_entries = ActivityLog.unscoped.filter(
            content_type=self.ticket_ct,
            object_id=ticket.pk,
        ).exclude(action=ActivityLog.Action.CREATED)
        self.assertEqual(
            al_entries.count(), 1,
            f"Expected 1 ActivityLog entry for priority change, got {al_entries.count()}"
        )

    # ------------------------------------------------------------------
    # 11.3 2-second dedup window catches edge case double-logs
    # ------------------------------------------------------------------
    @unittest.skip(
        "Not implemented: Testing the 2-second dedup window requires "
        "time.sleep() or time mocking which makes the test slow/fragile."
    )
    def test_two_second_dedup_window(self):
        """
        Two rapid saves within 2 seconds should produce only one log
        entry from the signal handler.
        """
        pass

    # ------------------------------------------------------------------
    # 11.4 TicketActivity covers all expected event types
    # ------------------------------------------------------------------
    def test_ticket_activity_event_choices(self):
        """TicketActivity.Event contains all expected event type strings."""
        expected_events = {
            "created", "assigned", "unassigned", "status_changed",
            "priority_changed", "commented", "internal_note", "closed",
            "reopened", "escalated", "escalated_manual",
            "attachment_added", "attachment_removed",
            "sla_paused", "sla_resumed", "auto_closed",
            "csat_received", "first_response",
            "pipeline_stage_changed", "email_linked", "email_actioned",
        }
        actual_events = {choice[0] for choice in TicketActivity.Event.choices}
        missing = expected_events - actual_events
        self.assertFalse(
            missing,
            f"TicketActivity.Event is missing event types: {missing}"
        )

    # ------------------------------------------------------------------
    # 11.5 ActivityLog is append-only (no update/delete via API)
    # ------------------------------------------------------------------
    def test_activity_log_is_read_only_api(self):
        """PUT, PATCH, DELETE on ActivityLog entries must return 405."""
        self.auth_tenant(self.admin_a, self.tenant_a)

        # Create a log entry directly
        self.set_tenant(self.tenant_a)
        log = ActivityLog.objects.create(
            content_type=self.ticket_ct,
            object_id=self.contact_a.pk,  # dummy object_id
            actor=self.admin_a,
            action=ActivityLog.Action.CREATED,
            description="Test audit entry",
        )

        detail_url = self.api_url(f"/comments/activity-logs/{log.pk}/")

        # PUT should be 405
        resp_put = self.client.put(
            detail_url,
            {"action": "updated", "description": "Modified"},
            format="json",
        )
        self.assertEqual(
            resp_put.status_code, 405,
            f"PUT on ActivityLog should return 405, got {resp_put.status_code}"
        )

        # PATCH should be 405
        resp_patch = self.client.patch(
            detail_url,
            {"description": "Modified"},
            format="json",
        )
        self.assertEqual(
            resp_patch.status_code, 405,
            f"PATCH on ActivityLog should return 405, got {resp_patch.status_code}"
        )

        # DELETE should be 405
        resp_delete = self.client.delete(detail_url)
        self.assertEqual(
            resp_delete.status_code, 405,
            f"DELETE on ActivityLog should return 405, got {resp_delete.status_code}"
        )

    # ------------------------------------------------------------------
    # 11.6 TicketActivity timeline endpoint returns events in correct order
    # ------------------------------------------------------------------
    def test_timeline_ordering(self):
        """
        GET /api/v1/tickets/tickets/{id}/timeline/ returns events
        ordered by -created_at (newest first).
        """
        self.auth_tenant(self.admin_a, self.tenant_a)

        ticket = self.create_ticket(self.tenant_a, self.admin_a, subject="Timeline")

        # Create multiple activities with distinct messages
        self.set_tenant(self.tenant_a)
        for i in range(3):
            TicketActivity.objects.create(
                ticket=ticket,
                actor=self.admin_a,
                event=TicketActivity.Event.COMMENTED,
                message=f"Activity {i}",
            )

        url = self.api_url(f"/tickets/tickets/{ticket.pk}/timeline/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)

        results = resp.data.get("results", resp.data)
        self.assertGreaterEqual(len(results), 3)

        # Verify descending order by created_at
        timestamps = [r["created_at"] for r in results]
        self.assertEqual(
            timestamps,
            sorted(timestamps, reverse=True),
            "Timeline events should be ordered newest-first (-created_at)"
        )

    # ------------------------------------------------------------------
    # 11.7 ActivityLog audit endpoint returns entries scoped to tenant
    # ------------------------------------------------------------------
    def test_activity_log_scoped_to_tenant(self):
        """
        GET /api/v1/comments/activity-logs/ as admin_a should only
        return entries belonging to tenant_a, not tenant_b.
        """
        # Create activity logs in both tenants
        self.set_tenant(self.tenant_a)
        log_a = ActivityLog.objects.create(
            content_type=self.ticket_ct,
            object_id=self.contact_a.pk,
            actor=self.admin_a,
            action=ActivityLog.Action.CREATED,
            description="Tenant A log",
        )

        self.set_tenant(self.tenant_b)
        log_b = ActivityLog.objects.create(
            content_type=self.ticket_ct,
            object_id=self.contact_a.pk,  # same object_id is fine
            actor=self.admin_b,
            action=ActivityLog.Action.CREATED,
            description="Tenant B log",
        )

        # Query as admin_a with tenant_a subdomain
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url("/comments/activity-logs/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)

        results = resp.data.get("results", resp.data)
        result_ids = [r["id"] for r in results]

        self.assertIn(
            str(log_a.pk), [str(x) for x in result_ids],
            "Tenant A log should be visible to admin_a"
        )
        self.assertNotIn(
            str(log_b.pk), [str(x) for x in result_ids],
            "Tenant B log should NOT be visible to admin_a"
        )
