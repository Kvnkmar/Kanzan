"""
Regression tests for two targets:

1. apps/kanban/services.py::move_card
   - Cross-status move on a non-personal board routes through
     apps.tickets.services.change_ticket_status (ticket status actually
     changes + a TicketActivity/ActivityLog audit row is written).
   - Cross-status move on a PERSONAL board does NOT write back to ticket status.
   - Reorder within a column updates positions.

2. apps/notifications/services.py::send_notification + INTERNAL_ONLY_TYPES
   - A NotificationPreference email opt-out suppresses email but still creates
     the in-app Notification row.
   - An INTERNAL_ONLY_TYPES type never emails even with prefs on.
   - A normal type with no preference row creates the Notification and attempts
     email.
"""

from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.comments.models import ActivityLog
from apps.kanban.models import Board, CardPosition, Column
from apps.kanban.services import move_card
from apps.notifications.models import (
    Notification,
    NotificationPreference,
    NotificationType,
)
from apps.notifications.services import INTERNAL_ONLY_TYPES, send_notification
from apps.tickets.models import Ticket, TicketActivity, TicketStatus
from conftest import MembershipFactory, TicketFactory, UserFactory
from main.context import tenant_context


# ---------------------------------------------------------------------------
# Kanban move_card
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMoveCard:
    """Tests for apps.kanban.services.move_card."""

    def _setup_board(self, tenant, *, is_personal=False, created_by=None):
        """Create a ticket board with two status-mapped columns and a ticket card.

        Returns (ticket, card, col_open, col_progress).
        """
        with tenant_context(tenant):
            open_status = TicketStatus.objects.create(
                tenant=tenant, name="Open", slug="open", order=0, is_default=True
            )
            progress_status = TicketStatus.objects.create(
                tenant=tenant, name="In Progress", slug="in-progress", order=1
            )

            board = Board.objects.create(
                tenant=tenant,
                name="Board",
                resource_type=Board.ResourceType.TICKET,
                is_default=not is_personal,
                is_personal=is_personal,
                created_by=created_by,
            )
            col_open = Column.objects.create(
                tenant=tenant, board=board, name="Open", order=0, status=open_status
            )
            col_progress = Column.objects.create(
                tenant=tenant,
                board=board,
                name="In Progress",
                order=1,
                status=progress_status,
            )

            ticket = TicketFactory(
                tenant=tenant, status=open_status, created_by=created_by
            )

            ticket_ct = ContentType.objects.get_for_model(Ticket)
            # A default (non-personal) board auto-creates a card for the ticket
            # via the create_kanban_card_on_ticket_save signal. Reuse it if
            # present to avoid a UNIQUE collision; otherwise create one.
            card = CardPosition.objects.filter(
                column__board=board,
                content_type=ticket_ct,
                object_id=ticket.id,
            ).first()
            if card is None:
                card = CardPosition.objects.create(
                    tenant=tenant,
                    column=col_open,
                    content_type=ticket_ct,
                    object_id=ticket.id,
                    order=0,
                )
            elif card.column_id != col_open.id:
                card.column = col_open
                card.save(update_fields=["column"])
        return ticket, card, col_open, col_progress

    def test_cross_status_move_changes_ticket_status_and_writes_audit(
        self, tenant, admin_user
    ):
        """Moving a Ticket card to a column with a different status FK routes
        through change_ticket_status: the ticket status actually changes and a
        TicketActivity + ActivityLog audit row is written."""
        ticket, card, _col_open, col_progress = self._setup_board(
            tenant, is_personal=False, created_by=admin_user
        )

        progress_status_id = col_progress.status_id
        assert ticket.status_id != progress_status_id

        with tenant_context(tenant):
            move_card(card, col_progress, position=0, actor=admin_user)

            ticket.refresh_from_db()
            assert ticket.status_id == progress_status_id, (
                "Ticket status should have changed to the target column's status."
            )

            # TicketActivity timeline row written for the status change.
            assert TicketActivity.objects.filter(
                ticket=ticket,
                event=TicketActivity.Event.STATUS_CHANGED,
            ).exists(), "A TicketActivity STATUS_CHANGED row should be written."

            # ActivityLog audit row written.
            assert ActivityLog.unscoped.filter(
                tenant=tenant,
                action=ActivityLog.Action.STATUS_CHANGED,
            ).exists(), "An ActivityLog STATUS_CHANGED audit row should be written."

    def test_personal_board_cross_status_move_does_not_write_back(
        self, tenant, admin_user
    ):
        """On a personal board, a cross-status move must NOT change the ticket
        status (personal boards are independent views)."""
        ticket, card, _col_open, col_progress = self._setup_board(
            tenant, is_personal=True, created_by=admin_user
        )

        original_status_id = ticket.status_id
        assert original_status_id != col_progress.status_id

        with tenant_context(tenant):
            move_card(card, col_progress, position=0, actor=admin_user)

            ticket.refresh_from_db()
            assert ticket.status_id == original_status_id, (
                "Personal board moves must not write back to ticket status."
            )

            # Card itself did move columns even though status is untouched.
            card.refresh_from_db()
            assert card.column_id == col_progress.id

            # No status-change audit rows from this move.
            assert not TicketActivity.objects.filter(
                ticket=ticket,
                event=TicketActivity.Event.STATUS_CHANGED,
            ).exists(), "Personal board move must not write a status-change activity."

    def test_reorder_within_column_updates_positions(self, tenant, admin_user):
        """Reordering a card within the same column shifts surrounding cards so
        order values stay contiguous."""
        with tenant_context(tenant):
            open_status = TicketStatus.objects.create(
                tenant=tenant, name="Open", slug="open", order=0, is_default=True
            )
            board = Board.objects.create(
                tenant=tenant,
                name="Board",
                resource_type=Board.ResourceType.TICKET,
                created_by=admin_user,
            )
            # status=None so move_card never tries a ticket writeback here.
            column = Column.objects.create(
                tenant=tenant, board=board, name="Col", order=0, status=None
            )
            ticket_ct = ContentType.objects.get_for_model(Ticket)

            cards = []
            for i in range(3):
                t = TicketFactory(
                    tenant=tenant, status=open_status, created_by=admin_user
                )
                cards.append(
                    CardPosition.objects.create(
                        tenant=tenant,
                        column=column,
                        content_type=ticket_ct,
                        object_id=t.id,
                        order=i,
                    )
                )

            # Move the first card (order 0) to the last position (order 2).
            move_card(cards[0], column, position=2, actor=admin_user)

            for c in cards:
                c.refresh_from_db()

            # Orders must remain a contiguous 0,1,2 permutation with no dupes.
            orders = sorted(c.order for c in cards)
            assert orders == [0, 1, 2], f"Orders must stay contiguous, got {orders}"
            # The moved card is now at position 2; the other two shifted up.
            assert cards[0].order == 2
            assert cards[1].order == 0
            assert cards[2].order == 1


# ---------------------------------------------------------------------------
# Notifications send_notification / INTERNAL_ONLY_TYPES
# ---------------------------------------------------------------------------

EMAIL_TASK_PATH = "apps.notifications.tasks.send_notification_email"


@pytest.mark.django_db
class TestSendNotification:
    """Tests for apps.notifications.services.send_notification."""

    def _recipient(self, tenant):
        with tenant_context(tenant):
            user = UserFactory()
            MembershipFactory(user=user, tenant=tenant)
        return user

    def test_email_optout_suppresses_email_but_creates_in_app_row(
        self, tenant, django_capture_on_commit_callbacks
    ):
        """A NotificationPreference with email=False suppresses the email
        channel but the in-app Notification row is still persisted."""
        recipient = self._recipient(tenant)

        with tenant_context(tenant):
            NotificationPreference.objects.create(
                tenant=tenant,
                user=recipient,
                notification_type=NotificationType.TICKET_ASSIGNED,
                in_app=True,
                email=False,
            )

            with patch(EMAIL_TASK_PATH) as mock_task:
                with django_capture_on_commit_callbacks(execute=True):
                    notification = send_notification(
                        tenant=tenant,
                        recipient=recipient,
                        notification_type=NotificationType.TICKET_ASSIGNED,
                        title="Assigned to you",
                    )

            # In-app row created regardless of email opt-out.
            assert Notification.unscoped.filter(pk=notification.pk).exists()
            assert notification.type == NotificationType.TICKET_ASSIGNED

            # Email channel suppressed.
            mock_task.delay.assert_not_called()

    def test_internal_only_type_never_emails_even_with_prefs_on(
        self, tenant, django_capture_on_commit_callbacks
    ):
        """An INTERNAL_ONLY_TYPES notification (REMINDER_DUE) must never email,
        even when the user's preference explicitly enables email."""
        assert NotificationType.REMINDER_DUE in INTERNAL_ONLY_TYPES

        recipient = self._recipient(tenant)

        with tenant_context(tenant):
            NotificationPreference.objects.create(
                tenant=tenant,
                user=recipient,
                notification_type=NotificationType.REMINDER_DUE,
                in_app=True,
                email=True,
            )

            with patch(EMAIL_TASK_PATH) as mock_task:
                with django_capture_on_commit_callbacks(execute=True):
                    notification = send_notification(
                        tenant=tenant,
                        recipient=recipient,
                        notification_type=NotificationType.REMINDER_DUE,
                        title="Reminder due",
                    )

            assert Notification.unscoped.filter(pk=notification.pk).exists()
            mock_task.delay.assert_not_called()

    def test_normal_type_no_pref_creates_row_and_attempts_email(
        self, tenant, django_capture_on_commit_callbacks
    ):
        """A non-internal type with no preference row defaults to both channels:
        the Notification row is created AND the email task is enqueued."""
        recipient = self._recipient(tenant)

        with tenant_context(tenant):
            # No NotificationPreference row exists for this (user, type).
            assert not NotificationPreference.unscoped.filter(
                user=recipient,
                tenant=tenant,
                notification_type=NotificationType.TICKET_COMMENT,
            ).exists()

            with patch(EMAIL_TASK_PATH) as mock_task:
                with django_capture_on_commit_callbacks(execute=True):
                    notification = send_notification(
                        tenant=tenant,
                        recipient=recipient,
                        notification_type=NotificationType.TICKET_COMMENT,
                        title="New comment",
                    )

            assert Notification.unscoped.filter(pk=notification.pk).exists()
            mock_task.delay.assert_called_once_with(str(notification.pk))
