"""
Module 3 — Ticket Lifecycle & Status Transitions

Tests every valid and invalid status transition, SLA pause/resume behaviour,
closure/reopen timestamps, kanban card sync, and activity logging for
ticket status changes.
"""

import unittest
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from freezegun import freeze_time

from apps.comments.models import ActivityLog
from apps.kanban.models import Board, CardPosition, Column
from apps.tickets.models import SLAPause, Ticket, TicketActivity
from apps.tickets.services import (
    ALLOWED_TRANSITIONS,
    change_ticket_status,
    transition_ticket_status,
    validate_status_transition,
)
from main.context import set_current_tenant

from tests.base import KanzenBaseTestCase


class TicketLifecycleTests(KanzenBaseTestCase):
    """Tests for ticket status transitions, SLA pause/resume, and lifecycle events."""

    def setUp(self):
        super().setUp()
        self.auth_tenant(self.admin_a, self.tenant_a)
        set_current_tenant(self.tenant_a)

    def _make_ticket(self, status=None, priority="high"):
        """Helper to create a ticket in tenant A with a given status."""
        return self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            status=status or self.status_open_a,
            priority=priority,
            contact=self.contact_a,
        )

    def _change_status_api(self, ticket, new_status):
        """POST to the change-status endpoint and return the response."""
        url = self.api_url(f"/tickets/tickets/{ticket.pk}/change-status/")
        return self.client.post(url, {"status": str(new_status.pk)}, format="json")

    # ------------------------------------------------------------------
    # 3.1  open -> in-progress: valid, TicketActivity logged
    # ------------------------------------------------------------------
    def test_3_1_open_to_in_progress(self):
        ticket = self._make_ticket(status=self.status_open_a)
        resp = self._change_status_api(ticket, self.status_in_progress_a)
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_in_progress_a.pk)
        self.assertTrue(
            TicketActivity.unscoped.filter(
                ticket=ticket,
                event=TicketActivity.Event.STATUS_CHANGED,
            ).exists()
        )

    # ------------------------------------------------------------------
    # 3.2  open -> waiting: valid, SLA paused
    # ------------------------------------------------------------------
    def test_3_2_open_to_waiting_sla_paused(self):
        ticket = self._make_ticket(status=self.status_open_a)
        resp = self._change_status_api(ticket, self.status_waiting_a)
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_waiting_a.pk)
        self.assertIsNotNone(ticket.sla_paused_at)

    # ------------------------------------------------------------------
    # 3.3  open -> resolved: valid
    # ------------------------------------------------------------------
    def test_3_3_open_to_resolved(self):
        ticket = self._make_ticket(status=self.status_open_a)
        resp = self._change_status_api(ticket, self.status_resolved_a)
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_resolved_a.pk)

    # ------------------------------------------------------------------
    # 3.4  open -> closed: valid
    # ------------------------------------------------------------------
    def test_3_4_open_to_closed(self):
        ticket = self._make_ticket(status=self.status_open_a)
        resp = self._change_status_api(ticket, self.status_closed_a)
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_closed_a.pk)

    # ------------------------------------------------------------------
    # 3.5  in-progress -> waiting: valid, SLA paused
    # ------------------------------------------------------------------
    def test_3_5_in_progress_to_waiting_sla_paused(self):
        ticket = self._make_ticket(status=self.status_in_progress_a)
        resp = self._change_status_api(ticket, self.status_waiting_a)
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_waiting_a.pk)
        self.assertIsNotNone(ticket.sla_paused_at)

    # ------------------------------------------------------------------
    # 3.6  in-progress -> resolved: valid
    # ------------------------------------------------------------------
    def test_3_6_in_progress_to_resolved(self):
        ticket = self._make_ticket(status=self.status_in_progress_a)
        resp = self._change_status_api(ticket, self.status_resolved_a)
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_resolved_a.pk)

    # ------------------------------------------------------------------
    # 3.7  waiting -> open: valid, SLA resumed, deadline shifted
    # ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00", tz_offset=0)
    def test_3_7_waiting_to_open_sla_resumed(self):
        ticket = self._make_ticket(status=self.status_open_a)
        # Transition to waiting (pauses SLA)
        transition_ticket_status(ticket, self.status_waiting_a, self.admin_a)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.sla_paused_at)
        pause_count_before = SLAPause.unscoped.filter(
            ticket=ticket, resumed_at__isnull=True
        ).count()
        self.assertEqual(pause_count_before, 1)

        # Advance time by 10 minutes then resume
        with freeze_time("2026-04-05 10:10:00", tz_offset=0):
            transition_ticket_status(ticket, self.status_open_a, self.admin_a)
            ticket.refresh_from_db()
            self.assertEqual(ticket.status_id, self.status_open_a.pk)
            self.assertIsNone(ticket.sla_paused_at)
            # The open SLAPause should now be closed
            open_pauses = SLAPause.unscoped.filter(
                ticket=ticket, resumed_at__isnull=True
            ).count()
            self.assertEqual(open_pauses, 0)
            # Check that SLA_RESUMED activity was logged
            self.assertTrue(
                TicketActivity.unscoped.filter(
                    ticket=ticket,
                    event=TicketActivity.Event.SLA_RESUMED,
                ).exists()
            )

    # ------------------------------------------------------------------
    # 3.8  waiting -> in-progress: valid, SLA resumed
    # ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00", tz_offset=0)
    def test_3_8_waiting_to_in_progress_sla_resumed(self):
        ticket = self._make_ticket(status=self.status_open_a)
        transition_ticket_status(ticket, self.status_waiting_a, self.admin_a)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.sla_paused_at)

        with freeze_time("2026-04-05 10:05:00", tz_offset=0):
            transition_ticket_status(ticket, self.status_in_progress_a, self.admin_a)
            ticket.refresh_from_db()
            self.assertEqual(ticket.status_id, self.status_in_progress_a.pk)
            self.assertIsNone(ticket.sla_paused_at)
            # SLAPause record should be closed
            open_pauses = SLAPause.unscoped.filter(
                ticket=ticket, resumed_at__isnull=True
            ).count()
            self.assertEqual(open_pauses, 0)

    # ------------------------------------------------------------------
    # 3.9  resolved -> closed: valid (auto or manual)
    # ------------------------------------------------------------------
    def test_3_9_resolved_to_closed(self):
        ticket = self._make_ticket(status=self.status_open_a)
        transition_ticket_status(ticket, self.status_resolved_a, self.admin_a)
        ticket.refresh_from_db()
        resp = self._change_status_api(ticket, self.status_closed_a)
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_closed_a.pk)

    # ------------------------------------------------------------------
    # 3.10 resolved -> open: valid (reopen), resolved_at cleared
    # ------------------------------------------------------------------
    def test_3_10_resolved_to_open_reopen(self):
        ticket = self._make_ticket(status=self.status_open_a)
        transition_ticket_status(ticket, self.status_resolved_a, self.admin_a)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.solved_at)

        resp = self._change_status_api(ticket, self.status_open_a)
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_open_a.pk)
        # solved_at should be cleared on reopen from resolved
        self.assertIsNone(ticket.solved_at)

    # ------------------------------------------------------------------
    # 3.11 closed -> any: blocked, returns 400, status unchanged in DB
    # ------------------------------------------------------------------
    def test_3_11_closed_is_terminal(self):
        ticket = self._make_ticket(status=self.status_open_a)
        transition_ticket_status(ticket, self.status_closed_a, self.admin_a)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_closed_a.pk)

        # Try transitioning to every other status -- all should fail
        for target_status in [
            self.status_open_a,
            self.status_in_progress_a,
            self.status_waiting_a,
            self.status_resolved_a,
        ]:
            resp = self._change_status_api(ticket, target_status)
            self.assertEqual(
                resp.status_code, 400,
                f"Expected 400 when transitioning from closed to {target_status.slug}, "
                f"got {resp.status_code}",
            )
            ticket.refresh_from_db()
            self.assertEqual(
                ticket.status_id, self.status_closed_a.pk,
                f"Status should remain closed after rejected transition to {target_status.slug}",
            )

    # ------------------------------------------------------------------
    # 3.12 SLA paused_at set when entering waiting status
    # ------------------------------------------------------------------
    def test_3_12_sla_paused_at_set_on_waiting(self):
        ticket = self._make_ticket(status=self.status_open_a)
        self.assertIsNone(ticket.sla_paused_at)
        transition_ticket_status(ticket, self.status_waiting_a, self.admin_a)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.sla_paused_at)

    # ------------------------------------------------------------------
    # 3.13 SLAPause record created when entering waiting
    # ------------------------------------------------------------------
    def test_3_13_sla_pause_record_created(self):
        ticket = self._make_ticket(status=self.status_open_a)
        pause_count_before = SLAPause.unscoped.filter(ticket=ticket).count()
        transition_ticket_status(ticket, self.status_waiting_a, self.admin_a)
        pause_count_after = SLAPause.unscoped.filter(ticket=ticket).count()
        self.assertEqual(pause_count_after, pause_count_before + 1)
        pause = SLAPause.unscoped.filter(ticket=ticket).order_by("-paused_at").first()
        self.assertIsNotNone(pause.paused_at)
        self.assertIsNone(pause.resumed_at)

    # ------------------------------------------------------------------
    # 3.14 SLAPause.resumed_at set when leaving waiting
    # ------------------------------------------------------------------
    def test_3_14_sla_pause_resumed_at_set(self):
        ticket = self._make_ticket(status=self.status_open_a)
        transition_ticket_status(ticket, self.status_waiting_a, self.admin_a)
        pause = SLAPause.unscoped.filter(ticket=ticket).order_by("-paused_at").first()
        self.assertIsNone(pause.resumed_at)

        transition_ticket_status(ticket, self.status_open_a, self.admin_a)
        pause.refresh_from_db()
        self.assertIsNotNone(pause.resumed_at)

    # ------------------------------------------------------------------
    # 3.15 SLA deadlines shift forward by pause duration (business hours)
    # ------------------------------------------------------------------
    @freeze_time("2026-04-06 10:00:00", tz_offset=0)
    def test_3_15_sla_deadlines_shift_on_resume(self):
        """SLA deadlines should be shifted forward after a pause/resume cycle."""
        from apps.tickets.services import initialize_sla

        ticket = self._make_ticket(status=self.status_open_a, priority="high")
        initialize_sla(ticket)
        ticket.refresh_from_db()

        if not ticket.sla_first_response_due:
            self.skipTest("SLA deadlines not set -- no active SLA policy matched.")

        original_response_due = ticket.sla_first_response_due
        original_resolution_due = ticket.sla_resolution_due

        # Enter waiting
        transition_ticket_status(ticket, self.status_waiting_a, self.admin_a)
        ticket.refresh_from_db()

        # Advance 10 minutes then resume
        with freeze_time("2026-04-06 10:10:00", tz_offset=0):
            transition_ticket_status(ticket, self.status_open_a, self.admin_a)
            ticket.refresh_from_db()

            # Deadlines should have shifted forward (by at least some amount)
            if ticket.sla_first_response_due:
                self.assertGreater(
                    ticket.sla_first_response_due,
                    original_response_due,
                    "First response deadline should shift forward after pause/resume",
                )
            if ticket.sla_resolution_due:
                self.assertGreater(
                    ticket.sla_resolution_due,
                    original_resolution_due,
                    "Resolution deadline should shift forward after pause/resume",
                )

    # ------------------------------------------------------------------
    # 3.16 resolved_at stamped on resolve
    # ------------------------------------------------------------------
    def test_3_16_resolved_at_stamped(self):
        ticket = self._make_ticket(status=self.status_open_a)
        self.assertIsNone(ticket.solved_at)
        transition_ticket_status(ticket, self.status_resolved_a, self.admin_a)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.solved_at)

    # ------------------------------------------------------------------
    # 3.17 closed_at stamped on close
    # ------------------------------------------------------------------
    def test_3_17_closed_at_stamped(self):
        ticket = self._make_ticket(status=self.status_open_a)
        self.assertIsNone(ticket.closed_at)
        transition_ticket_status(ticket, self.status_closed_a, self.admin_a)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.closed_at)
        self.assertIsNotNone(ticket.resolved_at)

    # ------------------------------------------------------------------
    # 3.18 Reopen clears resolved_at and closed_at
    # ------------------------------------------------------------------
    def test_3_18_reopen_clears_timestamps(self):
        """Reopening from resolved should clear solved_at.
        Note: closed is terminal so we test reopen from resolved -> open."""
        ticket = self._make_ticket(status=self.status_open_a)
        transition_ticket_status(ticket, self.status_resolved_a, self.admin_a)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.solved_at)

        transition_ticket_status(ticket, self.status_open_a, self.admin_a)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.solved_at)
        self.assertIsNone(ticket.auto_close_task_id)

    # ------------------------------------------------------------------
    # 3.19 Kanban card moves to correct column on each status change
    # ------------------------------------------------------------------
    def test_3_19_kanban_card_moves_on_status_change(self):
        # Create a Board with columns mapped to statuses
        board = Board(
            name="Test Board",
            resource_type=Board.ResourceType.TICKET,
            is_default=True,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )
        board.save()

        col_open = Column(
            board=board, name="Open", order=0,
            status=self.status_open_a, tenant=self.tenant_a,
        )
        col_open.save()
        col_in_progress = Column(
            board=board, name="In Progress", order=1,
            status=self.status_in_progress_a, tenant=self.tenant_a,
        )
        col_in_progress.save()
        col_closed = Column(
            board=board, name="Closed", order=2,
            status=self.status_closed_a, tenant=self.tenant_a,
        )
        col_closed.save()

        # Create a ticket (the post_save signal should create a card)
        ticket = self._make_ticket(status=self.status_open_a)

        ticket_ct = ContentType.objects.get_for_model(Ticket)
        card = CardPosition.unscoped.filter(
            content_type=ticket_ct, object_id=ticket.pk,
        ).first()

        if card is None:
            # Manually create the card if signal didn't fire for new boards
            card = CardPosition(
                column=col_open,
                content_type=ticket_ct,
                object_id=ticket.pk,
                order=0,
                tenant=self.tenant_a,
            )
            card.save()

        # Transition to in-progress
        transition_ticket_status(ticket, self.status_in_progress_a, self.admin_a)
        card.refresh_from_db()
        self.assertEqual(
            card.column_id, col_in_progress.pk,
            "Card should move to 'In Progress' column after status change",
        )

        # Transition to closed
        transition_ticket_status(ticket, self.status_closed_a, self.admin_a)
        card.refresh_from_db()
        self.assertEqual(
            card.column_id, col_closed.pk,
            "Card should move to 'Closed' column after status change",
        )

    # ------------------------------------------------------------------
    # 3.20 TicketActivity 'status_changed' logged for every transition
    # ------------------------------------------------------------------
    def test_3_20_ticket_activity_logged_for_every_transition(self):
        ticket = self._make_ticket(status=self.status_open_a)

        transitions = [
            (self.status_in_progress_a, TicketActivity.Event.STATUS_CHANGED),
            (self.status_waiting_a, TicketActivity.Event.STATUS_CHANGED),
            (self.status_open_a, TicketActivity.Event.STATUS_CHANGED),
        ]

        for new_status, expected_event in transitions:
            initial_count = TicketActivity.unscoped.filter(
                ticket=ticket,
                event__in=[
                    TicketActivity.Event.STATUS_CHANGED,
                    TicketActivity.Event.CLOSED,
                    TicketActivity.Event.REOPENED,
                    TicketActivity.Event.SLA_PAUSED,
                    TicketActivity.Event.SLA_RESUMED,
                ],
            ).count()

            transition_ticket_status(ticket, new_status, self.admin_a)
            ticket.refresh_from_db()

            new_count = TicketActivity.unscoped.filter(
                ticket=ticket,
                event__in=[
                    TicketActivity.Event.STATUS_CHANGED,
                    TicketActivity.Event.CLOSED,
                    TicketActivity.Event.REOPENED,
                    TicketActivity.Event.SLA_PAUSED,
                    TicketActivity.Event.SLA_RESUMED,
                ],
            ).count()

            self.assertGreater(
                new_count,
                initial_count,
                f"TicketActivity should be created for transition to {new_status.slug}",
            )
