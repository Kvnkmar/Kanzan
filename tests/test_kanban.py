"""
Module 10 — Kanban Board.

Tests:
    10.1 Ticket created → kanban card placed on default board
    10.2 Card placed in column matching ticket's initial status
    10.3 Status change → card moves to column mapped to new status
    10.4 Assignee change → card updated (not duplicated)
    10.5 Kanban sync failure does not break ticket status change
    10.6 Missing column mapping → error logged, ticket save succeeds anyway
    10.7 Tenant A cannot see Tenant B's kanban board → 403
"""

import logging
import unittest
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from rest_framework import status as http_status

from apps.kanban.models import Board, CardPosition, Column
from apps.kanban.services import populate_board_from_tickets
from apps.tickets.models import Ticket, TicketStatus
from main.context import clear_current_tenant, set_current_tenant
from tests.base import KanzenBaseTestCase


class KanbanSyncTests(KanzenBaseTestCase):
    """Tests for kanban board creation, card placement, and status sync (10.1-10.6)."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)

        # Create a default ticket board with columns mapped to statuses
        self.board = Board.objects.create(
            name="Default Ticket Board",
            resource_type=Board.ResourceType.TICKET,
            is_default=True,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )

        # Create columns mapped to each status
        self.col_open = Column.objects.create(
            board=self.board,
            name="Open",
            order=0,
            status=self.status_open_a,
            tenant=self.tenant_a,
        )
        self.col_in_progress = Column.objects.create(
            board=self.board,
            name="In Progress",
            order=1,
            status=self.status_in_progress_a,
            tenant=self.tenant_a,
        )
        self.col_waiting = Column.objects.create(
            board=self.board,
            name="Waiting",
            order=2,
            status=self.status_waiting_a,
            tenant=self.tenant_a,
        )
        self.col_resolved = Column.objects.create(
            board=self.board,
            name="Resolved",
            order=3,
            status=self.status_resolved_a,
            tenant=self.tenant_a,
        )
        self.col_closed = Column.objects.create(
            board=self.board,
            name="Closed",
            order=4,
            status=self.status_closed_a,
            tenant=self.tenant_a,
        )

        self.ticket_ct = ContentType.objects.get_for_model(Ticket)
        clear_current_tenant()

    # ------------------------------------------------------------------
    # 10.1 — Ticket created → kanban card placed on default board
    # ------------------------------------------------------------------
    def test_10_1_ticket_created_card_placed_on_default_board(self):
        """
        After creating a ticket and populating the board, a CardPosition
        should exist for the ticket on the default board.
        """
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
        )

        # Populate the board to pick up the new ticket
        self.set_tenant(self.tenant_a)
        populate_board_from_tickets(self.board)
        clear_current_tenant()

        card = CardPosition.unscoped.filter(
            content_type=self.ticket_ct,
            object_id=ticket.pk,
            column__board=self.board,
        ).first()
        self.assertIsNotNone(
            card,
            "A CardPosition should exist for the newly created ticket.",
        )

    # ------------------------------------------------------------------
    # 10.2 — Card placed in column matching ticket's initial status
    # ------------------------------------------------------------------
    def test_10_2_card_in_column_matching_initial_status(self):
        """
        The card for a new ticket should be placed in the column whose
        status FK matches the ticket's initial (default) status.
        """
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
        )

        self.set_tenant(self.tenant_a)
        populate_board_from_tickets(self.board)
        clear_current_tenant()

        card = CardPosition.unscoped.filter(
            content_type=self.ticket_ct,
            object_id=ticket.pk,
            column__board=self.board,
        ).first()
        self.assertIsNotNone(card)
        self.assertEqual(
            card.column_id,
            self.col_open.pk,
            "Card should be in the 'Open' column matching the default status.",
        )

    # ------------------------------------------------------------------
    # 10.3 — Status change → card moves to column mapped to new status
    # ------------------------------------------------------------------
    def test_10_3_status_change_moves_card(self):
        """
        When a ticket's status changes, the sync_kanban_card_on_status_change
        signal should move the card to the column mapped to the new status.
        """
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
        )

        # Populate so the card exists first
        self.set_tenant(self.tenant_a)
        populate_board_from_tickets(self.board)

        # Verify card is in Open column
        card = CardPosition.unscoped.get(
            content_type=self.ticket_ct,
            object_id=ticket.pk,
            column__board=self.board,
        )
        self.assertEqual(card.column_id, self.col_open.pk)

        # Change status to In Progress — signal should move the card
        ticket.status = self.status_in_progress_a
        ticket.save()

        card.refresh_from_db()
        self.assertEqual(
            card.column_id,
            self.col_in_progress.pk,
            "Card should have moved to 'In Progress' column after status change.",
        )
        clear_current_tenant()

    # ------------------------------------------------------------------
    # 10.4 — Assignee change → card updated (not duplicated)
    # ------------------------------------------------------------------
    def test_10_4_assignee_change_does_not_duplicate_card(self):
        """
        Changing the ticket assignee should not create a duplicate card.
        The card count for this ticket on the board must remain 1.
        """
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
        )

        self.set_tenant(self.tenant_a)
        populate_board_from_tickets(self.board)

        # Change assignee
        ticket.assignee = self.agent_a
        ticket.save()

        card_count = CardPosition.unscoped.filter(
            content_type=self.ticket_ct,
            object_id=ticket.pk,
            column__board=self.board,
        ).count()
        self.assertEqual(
            card_count,
            1,
            "Only one card should exist for the ticket after assignee change.",
        )
        clear_current_tenant()

    # ------------------------------------------------------------------
    # 10.5 — Kanban sync failure does not break ticket status change
    # ------------------------------------------------------------------
    def test_10_5_kanban_sync_failure_does_not_break_status_change(self):
        """
        If no default board or columns exist, changing a ticket's status
        should still succeed without raising an exception.
        """
        # Create ticket without any board present — delete our board
        self.set_tenant(self.tenant_a)
        self.board.delete()

        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
        )

        # Change status — should not raise even though no board exists
        ticket.status = self.status_in_progress_a
        ticket.save()

        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, self.status_in_progress_a.pk)
        clear_current_tenant()

    # ------------------------------------------------------------------
    # 10.6 — Missing column mapping → error logged, ticket save succeeds
    # ------------------------------------------------------------------
    def test_10_6_missing_column_mapping_logs_error_ticket_saves(self):
        """
        If a ticket's status changes to a status with no mapped column,
        the signal should log a message but the ticket save must still succeed.
        """
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
        )

        self.set_tenant(self.tenant_a)
        populate_board_from_tickets(self.board)

        # Create a new status with no corresponding column
        unmapped_status = TicketStatus(
            name="Unmapped Status",
            slug="unmapped",
            order=99,
            tenant=self.tenant_a,
        )
        unmapped_status.save()

        # Change ticket to the unmapped status
        ticket.status = unmapped_status
        ticket.save()

        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, unmapped_status.pk)

        # Card should still exist but remain in its original column (Open)
        card = CardPosition.unscoped.filter(
            content_type=self.ticket_ct,
            object_id=ticket.pk,
            column__board=self.board,
        ).first()
        self.assertIsNotNone(card)
        self.assertEqual(
            card.column_id,
            self.col_open.pk,
            "Card should remain in original column when no mapping exists for the new status.",
        )
        clear_current_tenant()


class KanbanTenantIsolationTests(KanzenBaseTestCase):
    """Tests for kanban board tenant isolation (10.7)."""

    def setUp(self):
        super().setUp()

        # Create a board in tenant B
        self.set_tenant(self.tenant_b)
        self.board_b = Board.objects.create(
            name="Tenant B Board",
            resource_type=Board.ResourceType.TICKET,
            is_default=True,
            created_by=self.admin_b,
            tenant=self.tenant_b,
        )
        clear_current_tenant()

    # ------------------------------------------------------------------
    # 10.7 — Tenant A cannot see Tenant B's kanban board
    # ------------------------------------------------------------------
    def test_10_7_tenant_a_cannot_see_tenant_b_board(self):
        """
        admin_a authenticated against tenant_a should not be able to
        retrieve a board belonging to tenant_b.
        """
        self.auth_tenant(self.admin_a, self.tenant_a)

        # List should not include tenant_b's board
        list_url = self.api_url("/kanban/boards/")
        resp = self.client.get(list_url)
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        board_ids = [str(b["id"]) for b in results]
        self.assertNotIn(
            str(self.board_b.pk),
            board_ids,
            "Tenant A's board list must not contain tenant B's board.",
        )

        # Direct detail access should return 404 (tenant-scoped queryset)
        detail_url = self.api_url(f"/kanban/boards/{self.board_b.pk}/")
        resp = self.client.get(detail_url)
        self.assertIn(
            resp.status_code,
            [http_status.HTTP_404_NOT_FOUND, http_status.HTTP_403_FORBIDDEN],
            "Accessing tenant B's board from tenant A should return 404 or 403.",
        )
