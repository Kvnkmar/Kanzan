"""
Django signals for the tickets app.

Responsibilities:
- Emit ``ticket_created`` and ``ticket_assigned`` custom signals for other
  apps (notifications, webhooks, analytics) to subscribe to.
- Automatically populate ``resolved_at`` and ``closed_at`` timestamps when a
  ticket transitions to a closed/resolved status.
- Clear those timestamps if the ticket is re-opened.
"""

import datetime
import logging

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save, pre_save
from django.dispatch import Signal, receiver
from django.utils import timezone

from apps.comments.models import ActivityLog
from apps.comments.services import log_activity
from apps.tickets.models import Ticket, TicketAssignment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom signals
# ---------------------------------------------------------------------------

ticket_created = Signal()   # sender=Ticket, instance, created_by
ticket_assigned = Signal()  # sender=Ticket, instance, assignee, assigned_by


# ---------------------------------------------------------------------------
# Ticket lifecycle (pre_save)
# ---------------------------------------------------------------------------


@receiver(pre_save, sender=Ticket)
def handle_ticket_status_change(sender, instance, **kwargs):
    """
    When a ticket's status changes:
    - If the new status is closed/resolved, stamp ``resolved_at`` and
      ``closed_at`` (only if not already set).
    - If the new status is NOT closed, clear those timestamps (re-open).

    Also stores old field values on the instance so the post_save signal
    ``log_ticket_activity`` can detect and log changes.
    """
    if instance.pk is None:
        # New ticket -- nothing to compare against.
        return

    try:
        previous = Ticket.unscoped.get(pk=instance.pk)
    except Ticket.DoesNotExist:
        return

    # ---- Store old field values for activity logging (post_save) ----
    instance._old_status_name = previous.status.name if previous.status else None
    instance._old_priority = previous.priority
    instance._old_assignee_id = previous.assignee_id
    instance._old_assignee_name = (
        previous.assignee.get_full_name() if previous.assignee else None
    )
    instance._status_changed = previous.status_id != instance.status_id
    instance._priority_changed = previous.priority != instance.priority
    instance._assignee_changed = previous.assignee_id != instance.assignee_id

    # ---- Status lifecycle timestamps ----
    if previous.status_id == instance.status_id:
        return  # Status unchanged; no timestamp logic needed.

    now = timezone.now()

    if instance.status and instance.status.is_closed:
        if not instance.resolved_at:
            instance.resolved_at = now
        if not instance.closed_at:
            instance.closed_at = now
        logger.info(
            "Ticket #%s moved to closed status '%s'.",
            instance.number,
            instance.status,
        )
    else:
        # Re-opened -- clear resolution timestamps.
        instance.resolved_at = None
        instance.closed_at = None
        logger.info(
            "Ticket #%s re-opened to status '%s'.",
            instance.number,
            instance.status,
        )


# ---------------------------------------------------------------------------
# Ticket post_save -- fire custom signals
# ---------------------------------------------------------------------------


@receiver(post_save, sender=Ticket)
def fire_ticket_created_signal(sender, instance, created, **kwargs):
    """Emit ``ticket_created`` when a brand-new ticket is saved."""
    if created:
        ticket_created.send(
            sender=Ticket,
            instance=instance,
            created_by=instance.created_by,
        )
        logger.info("Signal ticket_created fired for Ticket #%s.", instance.number)


@receiver(post_save, sender=Ticket)
def fire_ticket_assigned_signal(sender, instance, created, **kwargs):
    """
    Emit ``ticket_assigned`` whenever a ticket is saved with an assignee.

    On creation this fires if an assignee was provided. On updates it fires
    when the ``assignee`` field has changed (detected via ``update_fields``
    hint when available).
    """
    if created and instance.assignee_id:
        ticket_assigned.send(
            sender=Ticket,
            instance=instance,
            tenant=instance.tenant,
            assignee=instance.assignee,
            assigned_by=instance.created_by,
        )
        logger.info(
            "Signal ticket_assigned fired for new Ticket #%s -> %s.",
            instance.number,
            instance.assignee,
        )
        return

    # For updates, rely on update_fields hint from save(update_fields=[...]).
    update_fields = kwargs.get("update_fields")
    if update_fields and "assignee" in update_fields and instance.assignee_id:
        ticket_assigned.send(
            sender=Ticket,
            instance=instance,
            tenant=instance.tenant,
            assignee=instance.assignee,
            assigned_by=None,  # Caller should use TicketAssignment for audit.
        )
        logger.info(
            "Signal ticket_assigned fired for Ticket #%s -> %s.",
            instance.number,
            instance.assignee,
        )


# ---------------------------------------------------------------------------
# Activity logging (post_save)
# ---------------------------------------------------------------------------


def _activity_already_logged(instance, action: str) -> bool:
    """
    Return True if an ActivityLog for this ticket with the same action was
    created in the last 2 seconds.  This prevents duplicate logging when
    both the ViewSet and the signal fire for the same save.
    """
    recent = ActivityLog.objects.filter(
        content_type=ContentType.objects.get_for_model(Ticket),
        object_id=instance.pk,
        action=action,
        created_at__gte=timezone.now() - datetime.timedelta(seconds=2),
    ).exists()
    return recent


@receiver(post_save, sender=Ticket)
def log_ticket_activity(sender, instance, created, **kwargs):
    """
    Log activity for ticket creation and field changes that happen outside
    the API (e.g. kanban drag-and-drop, admin, shell).

    Uses a 5-second deduplication window so the ViewSet's own logging is
    not duplicated.
    """
    if created:
        # Ticket creation logging is handled by the service layer
        # (create_ticket_activity) called from ViewSet.perform_create.
        # The signal no longer logs creation to avoid duplicates.
        # Non-API paths (admin, shell) should call create_ticket_activity().
        return

    # Skip if the ViewSet is handling logging (prevents duplicates)
    if getattr(instance, "_skip_signal_logging", False):
        return

    # -- Status change --
    if getattr(instance, "_status_changed", False):
        action = ActivityLog.Action.STATUS_CHANGED
        if not _activity_already_logged(instance, action):
            new_status_name = instance.status.name if instance.status else None
            log_activity(
                tenant=instance.tenant,
                actor=None,  # System-level; ViewSet logging carries the real actor.
                content_object=instance,
                action=action,
                description=(
                    f"Ticket #{instance.number} status changed from "
                    f"'{instance._old_status_name}' to '{new_status_name}'"
                ),
                changes={
                    "status": [instance._old_status_name, new_status_name],
                },
            )

    # -- Priority change --
    if getattr(instance, "_priority_changed", False):
        action = ActivityLog.Action.FIELD_CHANGED
        if not _activity_already_logged(instance, action):
            log_activity(
                tenant=instance.tenant,
                actor=None,
                content_object=instance,
                action=action,
                description=(
                    f"Ticket #{instance.number} priority changed from "
                    f"'{instance._old_priority}' to '{instance.priority}'"
                ),
                changes={
                    "priority": [instance._old_priority, instance.priority],
                },
            )

    # -- Assignee change --
    if getattr(instance, "_assignee_changed", False):
        action = ActivityLog.Action.ASSIGNED
        if not _activity_already_logged(instance, action):
            new_assignee_name = (
                instance.assignee.get_full_name() if instance.assignee else None
            )
            log_activity(
                tenant=instance.tenant,
                actor=None,
                content_object=instance,
                action=action,
                description=(
                    f"Ticket #{instance.number} assigned from "
                    f"'{instance._old_assignee_name}' to '{new_assignee_name}'"
                ),
                changes={
                    "assignee": [
                        str(instance._old_assignee_id) if instance._old_assignee_id else None,
                        str(instance.assignee_id) if instance.assignee_id else None,
                    ],
                },
            )


# ---------------------------------------------------------------------------
# Kanban sync -- create card when ticket is created or assigned
# ---------------------------------------------------------------------------


@receiver(post_save, sender=Ticket)
def create_kanban_card_on_ticket_save(sender, instance, created, **kwargs):
    """
    Ensure every ticket has a card on the default ticket kanban board,
    placed in the column matching its current status.

    Fires on ticket creation and when the assignee changes.
    """
    if not created:
        # For updates, only act when the assignee actually changed
        assignee_changed = getattr(instance, "_assignee_changed", False)
        if not assignee_changed:
            update_fields = kwargs.get("update_fields")
            if not update_fields or "assignee" not in update_fields:
                return

    try:
        from apps.kanban.models import Board, CardPosition, Column

        ticket_ct = ContentType.objects.get_for_model(Ticket)

        # Find the default ticket board for this tenant
        board = Board.objects.filter(
            tenant=instance.tenant,
            resource_type=Board.ResourceType.TICKET,
            is_default=True,
        ).first()

        if not board:
            return

        # Check if a card already exists on this board
        existing = CardPosition.objects.filter(
            content_type=ticket_ct,
            object_id=instance.pk,
            column__board=board,
        ).exists()

        if existing:
            return

        # Find the column mapped to the ticket's current status
        target_column = Column.objects.filter(
            board=board,
            status=instance.status,
        ).first()

        # Fallback: first column on the board
        if not target_column:
            target_column = Column.objects.filter(board=board).order_by("order").first()

        if not target_column:
            return

        new_order = target_column.cards.count()
        CardPosition.objects.create(
            column=target_column,
            content_type=ticket_ct,
            object_id=instance.pk,
            order=new_order,
        )
        logger.info(
            "Created kanban card for Ticket #%s in column '%s' on board '%s'.",
            instance.number,
            target_column.name,
            board.name,
        )
    except Exception as exc:
        logger.warning(
            "Failed to create kanban card for Ticket #%s: %s", instance.number, exc
        )


# ---------------------------------------------------------------------------
# Kanban sync -- move card when ticket status changes
# ---------------------------------------------------------------------------


@receiver(post_save, sender=Ticket)
def sync_kanban_card_on_status_change(sender, instance, created, **kwargs):
    """
    When a ticket's status changes, move its kanban card to the column
    mapped to the new status.
    """
    if created:
        return

    update_fields = kwargs.get("update_fields")
    if update_fields and "status" not in update_fields:
        return

    try:
        from apps.kanban.models import CardPosition, Column

        ticket_ct = ContentType.objects.get_for_model(Ticket)

        # Find all card positions for this ticket
        cards = CardPosition.objects.filter(
            content_type=ticket_ct,
            object_id=instance.pk,
        ).select_related("column", "column__board")

        for card in cards:
            board = card.column.board
            # Find the target column on the same board mapped to the new status
            target_column = Column.objects.filter(
                board=board,
                status=instance.status,
            ).first()

            if target_column and target_column.id != card.column_id:
                # Move card to the target column
                new_order = target_column.cards.count()
                card.column = target_column
                card.order = new_order
                card.save(update_fields=["column", "order"])
                logger.info(
                    "Kanban card for Ticket #%s moved to column '%s'.",
                    instance.number,
                    target_column.name,
                )
    except Exception as exc:
        logger.warning("Failed to sync kanban card for Ticket #%s: %s", instance.number, exc)
