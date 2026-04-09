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
from apps.tickets.models import SLAPause, Ticket, TicketActivity, TicketAssignment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom signals
# ---------------------------------------------------------------------------

ticket_created = Signal()   # sender=Ticket, instance, created_by
ticket_assigned = Signal()  # sender=Ticket, instance, assignee, assigned_by
sla_first_response_breached = Signal()  # sender=Ticket, instance, responded_at, due_at
ticket_closed = Signal()    # sender=Ticket, instance, payload (dict with resolution metrics)
sla_resolution_breached = Signal()  # sender=Ticket, instance, closed_at, due_at


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
    instance._pipeline_stage_changed = (
        previous.pipeline_stage_id != instance.pipeline_stage_id
    )
    instance._old_pipeline_stage_name = (
        previous.pipeline_stage.name if previous.pipeline_stage else None
    )
    instance._old_pauses_sla = getattr(previous.status, "pauses_sla", False) if previous.status else False

    # ---- Status lifecycle timestamps ----
    if previous.status_id == instance.status_id:
        return  # Status unchanged; no timestamp logic needed.

    now = timezone.now()

    if instance.status and instance.status.is_closed:
        if not instance.resolved_at:
            instance.resolved_at = now
        if not instance.closed_at:
            instance.closed_at = now

        # Check SLA resolution breach at closure (covers direct save paths
        # that bypass the service layer).
        if not instance.sla_resolution_breached and instance.sla_resolution_due:
            closed_at = instance.closed_at or now
            if closed_at > instance.sla_resolution_due:
                instance.sla_resolution_breached = True
                logger.info(
                    "Resolution SLA breached for Ticket #%s in pre_save "
                    "(closed: %s, due: %s).",
                    instance.number, closed_at, instance.sla_resolution_due,
                )

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
# SLA pause/resume on status change
# ---------------------------------------------------------------------------


@receiver(post_save, sender=Ticket)
def handle_sla_pause_on_status_change(sender, instance, created, **kwargs):
    """
    When a ticket's status changes:
    - If the new status has ``pauses_sla=True`` and the old didn't, create
      an open ``SLAPause`` record and log an SLA_PAUSED timeline event.
    - If the old status had ``pauses_sla=True`` and the new doesn't, close
      the open ``SLAPause`` record and log an SLA_RESUMED timeline event.
    """
    if created:
        return
    if not getattr(instance, "_status_changed", False):
        return

    old_pauses = getattr(instance, "_old_pauses_sla", False)
    new_pauses = getattr(instance.status, "pauses_sla", False) if instance.status else False

    if old_pauses == new_pauses:
        return  # No change in SLA pause state

    now = timezone.now()

    if new_pauses and not old_pauses:
        # Status changed TO a pausing status → create SLA pause
        SLAPause(
            tenant=instance.tenant,
            ticket=instance,
            paused_at=now,
            reason=SLAPause.Reason.WAITING_ON_CUSTOMER,
        ).save()

        # Denormalize pause timestamp on ticket for fast queries
        instance.sla_paused_at = now
        instance.save(update_fields=["sla_paused_at", "updated_at"])

        from apps.tickets.services import _create_ticket_activity

        _create_ticket_activity(
            instance,
            actor=None,
            event=TicketActivity.Event.SLA_PAUSED,
            message=f"SLA clock paused (status: {instance.status.name})",
            metadata={"status": instance.status.name, "reason": "waiting_on_customer"},
        )

        logger.info(
            "SLA paused for Ticket #%s (status: %s).",
            instance.number,
            instance.status.name,
        )

    elif old_pauses and not new_pauses:
        # Status changed FROM a pausing status → resume SLA
        _resume_sla_pause(instance, now, reason="status_change")


def _resume_sla_pause(ticket, now=None, reason="status_change"):
    """
    Close the most recent open SLAPause for *ticket*, shift SLA deadlines
    forward by the business-hours-adjusted pause duration, and log a
    timeline event.

    Called from status-change signal and from inbound email reply processing.
    """
    if now is None:
        now = timezone.now()

    open_pause = (
        SLAPause.unscoped
        .filter(ticket=ticket, resumed_at__isnull=True)
        .order_by("-paused_at")
        .first()
    )
    if open_pause is None:
        return

    open_pause.resumed_at = now
    open_pause.save(update_fields=["resumed_at", "updated_at"])

    duration_min = int(open_pause.duration_minutes)

    # Shift SLA deadlines forward by the paused duration (business-hours aware)
    _shift_sla_deadlines(ticket, open_pause.paused_at, now)

    # Clear the denormalized pause timestamp
    ticket.sla_paused_at = None
    ticket.save(update_fields=["sla_paused_at", "updated_at"])

    from apps.tickets.services import _create_ticket_activity

    _create_ticket_activity(
        ticket,
        actor=None,
        event=TicketActivity.Event.SLA_RESUMED,
        message=f"SLA clock resumed after {duration_min} min pause ({reason})",
        metadata={
            "pause_id": str(open_pause.pk),
            "duration_minutes": duration_min,
            "reason": reason,
        },
    )

    logger.info(
        "SLA resumed for Ticket #%s after %d min pause (%s).",
        ticket.number,
        duration_min,
        reason,
    )


def _shift_sla_deadlines(ticket, pause_start, pause_end):
    """
    Shift ``sla_first_response_due`` and ``sla_resolution_due`` forward by
    the business-hours-adjusted pause duration.

    If business hours are configured for the tenant, only business minutes
    within the pause window count. Otherwise wall-clock duration is used.
    This ensures weekend/holiday pauses don't inflate the shift.
    """
    from apps.tickets.sla import add_business_minutes, elapsed_business_minutes

    tenant = ticket.tenant

    # Calculate business-hours-adjusted pause duration
    pause_business_minutes = elapsed_business_minutes(pause_start, pause_end, tenant)

    if pause_business_minutes <= 0:
        return  # Pause fell entirely outside business hours

    if not ticket.sla_first_response_due and not ticket.sla_resolution_due:
        logger.warning(
            "SLA pause shift skipped for Ticket #%s — no SLA deadlines set.",
            ticket.number,
        )
        return

    update_fields = []

    # Snapshot before values for audit logging
    old_response_due = ticket.sla_first_response_due
    old_resolution_due = ticket.sla_resolution_due

    if ticket.sla_first_response_due:
        ticket.sla_first_response_due = add_business_minutes(
            ticket.sla_first_response_due, pause_business_minutes, tenant,
        )
        update_fields.append("sla_first_response_due")

    if ticket.sla_resolution_due:
        ticket.sla_resolution_due = add_business_minutes(
            ticket.sla_resolution_due, pause_business_minutes, tenant,
        )
        update_fields.append("sla_resolution_due")

    if update_fields:
        update_fields.append("updated_at")
        ticket.save(update_fields=update_fields)

        from apps.tickets.services import log_sla_change
        log_sla_change(ticket, old_response_due, old_resolution_due, "pause_resume")
        logger.info(
            "SLA deadlines shifted by %.1f business minutes for Ticket #%s.",
            pause_business_minutes,
            ticket.number,
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


# ---------------------------------------------------------------------------
# Kanban sync -- move card when pipeline stage changes
# ---------------------------------------------------------------------------


@receiver(post_save, sender=Ticket)
def sync_kanban_card_on_pipeline_stage_change(sender, instance, created, **kwargs):
    """
    When a ticket's pipeline_stage changes, move its kanban card to the
    column whose name matches the new stage name (case-insensitive).
    """
    if created:
        return

    if not getattr(instance, "_pipeline_stage_changed", False):
        return

    if not instance.pipeline_stage:
        return

    try:
        from apps.kanban.models import CardPosition, Column

        ticket_ct = ContentType.objects.get_for_model(Ticket)
        new_stage_name = instance.pipeline_stage.name

        cards = CardPosition.objects.filter(
            content_type=ticket_ct,
            object_id=instance.pk,
        ).select_related("column", "column__board")

        for card in cards:
            board = card.column.board
            # Match column by name (case-insensitive)
            target_column = Column.objects.filter(
                board=board,
                name__iexact=new_stage_name,
            ).first()

            if target_column and target_column.id != card.column_id:
                new_order = target_column.cards.count()
                card.column = target_column
                card.order = new_order
                card.save(update_fields=["column", "order"])
                logger.info(
                    "Kanban card for Ticket #%s moved to column '%s' "
                    "(pipeline stage: %s).",
                    instance.number,
                    target_column.name,
                    new_stage_name,
                )
    except Exception as exc:
        logger.warning(
            "Failed to sync kanban card on pipeline stage change for "
            "Ticket #%s: %s",
            instance.number,
            exc,
        )


# ---------------------------------------------------------------------------
# Post-closure: KB suggestion flag (Phase 5)
# ---------------------------------------------------------------------------


@receiver(ticket_closed)
def check_kb_article_coverage(sender, instance, payload, **kwargs):
    """
    After a ticket is closed, check if its category has fewer than 3
    published KB articles. If so, flag the ticket for KB article creation.

    Uses ``category__name__iexact`` to avoid case-sensitivity mismatches
    between the ticket's category CharField and KB Category model names.
    """
    try:
        category = instance.category
        if not category:
            return

        tenant = instance.tenant

        from apps.knowledge.models import Article

        published_count = (
            Article.unscoped
            .filter(
                tenant=tenant,
                category__name__iexact=category,
                status="published",
            )
            .count()
        )

        if published_count < 3:
            Ticket.unscoped.filter(pk=instance.pk).update(needs_kb_article=True)
            logger.info(
                "Ticket #%s flagged for KB article (category '%s' has %d articles).",
                instance.number,
                category,
                published_count,
            )
    except Exception:
        logger.exception(
            "Failed KB coverage check for ticket %s.", instance.pk,
        )


# ---------------------------------------------------------------------------
# SLA policy edit → propagate deadline changes to affected tickets
# ---------------------------------------------------------------------------


@receiver(post_save, sender="tickets.SLAPolicy")
def propagate_sla_policy_change(sender, instance, created, **kwargs):
    """
    When an SLAPolicy is saved (not created), recalculate SLA deadlines for
    all open tickets using that policy and write audit logs.

    Dispatches to a Celery task when >50 tickets are affected to avoid
    blocking the request/response cycle.
    """
    if created:
        return  # New policy — no existing tickets to update

    try:
        affected_ids = list(
            Ticket.unscoped
            .filter(
                sla_policy=instance,
                status__is_closed=False,
            )
            .values_list("pk", flat=True)
        )

        if not affected_ids:
            return

        policy_pk = str(instance.pk)
        tenant_pk = str(instance.tenant_id)

        if len(affected_ids) > 50:
            from django.db import transaction
            from apps.tickets.tasks import propagate_sla_policy_change_task

            ticket_ids = [str(pk) for pk in affected_ids]
            transaction.on_commit(
                lambda: propagate_sla_policy_change_task.delay(
                    policy_pk, tenant_pk, ticket_ids,
                )
            )
            logger.info(
                "Queued async SLA propagation for %d tickets (policy %s).",
                len(affected_ids), instance.name,
            )
        else:
            _apply_policy_to_tickets(instance, affected_ids)

    except Exception:
        logger.exception(
            "Failed to propagate SLA policy change for policy %s.",
            instance.pk,
        )


def _apply_policy_to_tickets(policy, ticket_ids):
    """
    Recalculate SLA deadlines for a list of tickets based on a changed policy.
    Writes structured audit logs for each ticket.
    """
    from apps.tickets.sla import add_business_minutes
    from apps.tickets.services import log_sla_change

    now = timezone.now()

    for ticket in Ticket.unscoped.filter(pk__in=ticket_ids).select_related("tenant").iterator(chunk_size=200):
        old_response_due = ticket.sla_first_response_due
        old_resolution_due = ticket.sla_resolution_due

        ticket.sla_first_response_due = add_business_minutes(
            ticket.created_at, policy.first_response_minutes, ticket.tenant,
        )
        ticket.sla_resolution_due = add_business_minutes(
            ticket.created_at, policy.resolution_minutes, ticket.tenant,
        )

        try:
            ticket.save(update_fields=[
                "sla_first_response_due", "sla_resolution_due", "updated_at",
            ])
            log_sla_change(ticket, old_response_due, old_resolution_due, "policy_edit")
        except Exception:
            logger.exception(
                "Failed to update SLA for Ticket #%s on policy change.",
                ticket.number,
            )
