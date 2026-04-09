"""
Service-layer functions for ticket operations.

Every mutation that should be audited goes through this layer. Each function
performs the domain operation AND writes to both logging systems atomically:

1. ActivityLog (audit / compliance) -- "WHO did WHAT?"
   Polymorphic, immutable, stores diffs and IP. Used by auditors.

2. TicketActivity (ticket timeline) -- "WHAT happened to THIS ticket?"
   Ticket-specific, human-readable. Displayed in the ticket detail UI.

WHY two separate logs?
- Different consumers: auditors query across all entities; agents look at one
  ticket's history.
- Different schemas: audit logs need structured diffs, IP addresses, and
  content-type polymorphism. Timelines need human-readable messages.
- Different retention: audit logs may have legal retention requirements;
  timeline entries can be pruned with the ticket.
- Different access control: audit logs are admin-only; timelines are visible
  to anyone who can view the ticket.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.comments.models import ActivityLog
from apps.comments.services import log_activity
from apps.tickets.models import Ticket, TicketActivity, TicketAssignment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contact-aware TicketActivity helper
# ---------------------------------------------------------------------------


def _create_ticket_activity(ticket, *, event, actor=None, message="", metadata=None):
    """
    Create a TicketActivity with automatic contact population and
    ContactEvent mirroring for the unified contact timeline.

    All TicketActivity creation in this module should go through this
    helper so that the contact FK is always set and the ContactEvent
    log stays in sync.
    """
    if metadata is None:
        metadata = {}

    contact = ticket.contact if ticket.contact_id else None

    activity = TicketActivity.objects.create(
        tenant=ticket.tenant,
        ticket=ticket,
        contact=contact,
        actor=actor,
        event=event,
        message=message,
        metadata=metadata,
    )

    # Mirror to the unified contact timeline
    if contact:
        try:
            from apps.contacts.services import log_contact_event

            log_contact_event(
                contact=contact,
                event_type=event,
                description=message,
                source="ticket",
                actor=actor,
                metadata={
                    "ticket_id": str(ticket.pk),
                    "ticket_number": ticket.number,
                    **metadata,
                },
            )
        except Exception:
            logger.exception(
                "Failed to log ContactEvent for Ticket #%s, contact %s",
                ticket.number,
                contact.pk,
            )

    return activity


# ---------------------------------------------------------------------------
# Ticket creation
# ---------------------------------------------------------------------------


def initialize_sla(ticket):
    """
    Attach the matching SLA policy and compute response/resolution deadlines
    based on the ticket's priority.  Called after a ticket is first saved.

    No-op when no active SLA policy exists for the priority.
    """
    from apps.tickets.models import SLAPolicy
    from apps.tickets.sla import add_business_minutes

    tenant = ticket.tenant
    policy = (
        SLAPolicy.unscoped
        .filter(tenant=tenant, priority=ticket.priority, is_active=True)
        .first()
    )
    if policy is None:
        return

    now = timezone.now()
    ticket.sla_policy = policy
    ticket.sla_first_response_due = add_business_minutes(
        now, policy.first_response_minutes, tenant,
    )
    ticket.sla_resolution_due = add_business_minutes(
        now, policy.resolution_minutes, tenant,
    )
    ticket.save(update_fields=[
        "sla_policy", "sla_first_response_due", "sla_resolution_due", "updated_at",
    ])
    logger.info(
        "SLA initialized for Ticket #%s (policy: %s, response due: %s, resolution due: %s).",
        ticket.number,
        policy.name,
        ticket.sla_first_response_due,
        ticket.sla_resolution_due,
    )


def log_sla_change(ticket, old_response_due, old_resolution_due, triggered_by, actor=None):
    """
    Write a structured SLA audit entry to both ActivityLog and TicketActivity
    whenever SLA deadlines change on an existing ticket.

    Args:
        ticket: The Ticket instance (with new deadline values already set).
        old_response_due: Previous sla_first_response_due (datetime or None).
        old_resolution_due: Previous sla_resolution_due (datetime or None).
        triggered_by: One of "policy_edit", "escalation", "pause_resume", "manual".
        actor: Optional User who triggered the change.

    Skipped silently on error so callers are never blocked.
    """
    def _fmt(dt):
        return dt.isoformat() if dt else None

    new_response_due = ticket.sla_first_response_due
    new_resolution_due = ticket.sla_resolution_due

    # Skip if nothing actually changed
    if old_response_due == new_response_due and old_resolution_due == new_resolution_due:
        return

    changes = {
        "sla_first_response_due": {"before": _fmt(old_response_due), "after": _fmt(new_response_due)},
        "sla_resolution_due": {"before": _fmt(old_resolution_due), "after": _fmt(new_resolution_due)},
        "triggered_by": triggered_by,
    }

    old_resp_str = old_response_due.strftime("%H:%M %b %d") if old_response_due else "none"
    new_resp_str = new_response_due.strftime("%H:%M %b %d") if new_response_due else "none"
    old_res_str = old_resolution_due.strftime("%H:%M %b %d") if old_resolution_due else "none"
    new_res_str = new_resolution_due.strftime("%H:%M %b %d") if new_resolution_due else "none"

    msg = (
        f"SLA deadlines updated: first response {old_resp_str} → {new_resp_str}, "
        f"resolution {old_res_str} → {new_res_str}"
    )

    try:
        log_activity(
            tenant=ticket.tenant,
            actor=actor,
            content_object=ticket,
            action=ActivityLog.Action.SLA_UPDATED,
            description=msg,
            changes=changes,
        )
        _create_ticket_activity(
            ticket,
            actor=actor,
            event=TicketActivity.Event.STATUS_CHANGED,
            message=msg,
            metadata=changes,
        )
    except Exception:
        logger.exception(
            "Failed to write SLA audit log for Ticket #%s (triggered_by=%s).",
            ticket.number, triggered_by,
        )


def create_ticket_activity(ticket, actor, request=None):
    """
    Record creation in both logs. Called after the ticket is saved.
    """
    tenant = ticket.tenant

    # 1. Audit log (ActivityLog)
    log_activity(
        tenant=tenant,
        actor=actor,
        content_object=ticket,
        action=ActivityLog.Action.CREATED,
        description=f"Created ticket #{ticket.number}: {ticket.subject}",
        request=request,
    )

    # 2. Ticket timeline (TicketActivity)
    _create_ticket_activity(
        ticket,
        actor=actor,
        event=TicketActivity.Event.CREATED,
        message=f"Ticket created: {ticket.subject}",
    )


# ---------------------------------------------------------------------------
# Ticket assignment
# ---------------------------------------------------------------------------


@transaction.atomic
def assign_ticket(ticket, assignee, actor, request=None, note=""):
    """
    Assign or reassign a ticket.

    Performs all of the following atomically:
    1. Validate assignee is an active tenant member.
    2. Update ticket.assignee, assigned_by, assigned_at.
    3. Auto-transition to "In Progress" status if currently on the default
       (open) status and the ticket is being assigned for the first time.
    4. Create TicketAssignment record (immutable assignment history).
    5. Write ONE ActivityLog entry (audit: WHO assigned WHOM).
    6. Write ONE TicketActivity entry (timeline: "Assigned to X").

    Args:
        ticket: The Ticket instance to assign.
        assignee: The User to assign the ticket to.
        actor: The User performing the assignment.
        request: Optional HTTP request (for IP logging).
        note: Optional note for the assignment.

    Returns:
        The updated Ticket instance.

    Raises:
        ValueError: If the assignee is not an active member of the ticket's
            tenant.
    """
    from apps.accounts.models import TenantMembership

    tenant = ticket.tenant

    # 1. Validate assignee is an active tenant member
    if not TenantMembership.objects.filter(
        user=assignee, tenant=tenant, is_active=True
    ).exists():
        raise ValueError("Assignee is not an active member of this tenant.")

    previous_assignee = ticket.assignee
    prev_name = (
        previous_assignee.get_full_name() or str(previous_assignee)
        if previous_assignee
        else None
    )
    new_name = assignee.get_full_name() or str(assignee)

    # 2. Update the ticket (denormalized assignment fields)
    now = timezone.now()
    ticket.assignee = assignee
    ticket.assigned_by = actor
    ticket.assigned_at = now
    update_fields = ["assignee", "assigned_by", "assigned_at", "updated_at"]

    # 3. Auto-transition to "In Progress" if on default (open) status
    #    Controlled by TenantSettings.auto_transition_on_assign (default True).
    auto_status_msg = None
    tenant_settings = getattr(tenant, "settings", None)
    should_transition = (
        tenant_settings is None
        or tenant_settings.auto_transition_on_assign
    )
    if should_transition and ticket.status and ticket.status.is_default and not ticket.status.is_closed:
        from apps.tickets.models import TicketStatus

        in_progress = (
            TicketStatus.objects.filter(
                tenant=tenant, slug="in-progress",
            ).first()
        )
        if in_progress and in_progress.pk != ticket.status_id:
            old_status_name = ticket.status.name
            ticket.status = in_progress
            update_fields.append("status")
            auto_status_msg = (
                f"Status changed from {old_status_name} to {in_progress.name}"
            )

    ticket.save(update_fields=update_fields)

    # NOTE: record_first_response() is intentionally NOT called here.
    # Assignment is an internal action, not a customer-facing reply.
    # First response is stamped only when an outbound comment or email
    # is sent (see views.py comments action and send_email action).

    # 4. Assignment history record (immutable audit trail)
    TicketAssignment.objects.create(
        ticket=ticket,
        assigned_to=assignee,
        assigned_by=actor,
        note=note,
        tenant=tenant,
    )

    # 5. Audit log (ActivityLog) -- WHO did WHAT
    log_activity(
        tenant=tenant,
        actor=actor,
        content_object=ticket,
        action=ActivityLog.Action.ASSIGNED,
        description=f"Assigned to {new_name}",
        changes={"assignee": [prev_name, new_name]},
        request=request,
    )

    # 6. Ticket timeline (TicketActivity) -- WHAT happened to this ticket
    _create_ticket_activity(
        ticket,
        actor=actor,
        event=TicketActivity.Event.ASSIGNED,
        message=f"Assigned to {new_name}" if new_name else "Unassigned",
        metadata={
            "previous_assignee": prev_name,
            "new_assignee": new_name,
        },
    )

    # 6b. Auto-status timeline entry (if status changed)
    if auto_status_msg:
        log_activity(
            tenant=tenant,
            actor=actor,
            content_object=ticket,
            action=ActivityLog.Action.STATUS_CHANGED,
            description=auto_status_msg,
            changes={
                "status": [
                    auto_status_msg.split(" from ")[1].split(" to ")[0],
                    ticket.status.name,
                ],
            },
            request=request,
        )
        _create_ticket_activity(
            ticket,
            actor=actor,
            event=TicketActivity.Event.STATUS_CHANGED,
            message=auto_status_msg,
            metadata={
                "old_status": auto_status_msg.split(" from ")[1].split(" to ")[0],
                "new_status": ticket.status.name,
                "reason": "Auto-transitioned on assignment",
            },
        )

    logger.info(
        "Ticket #%s assigned from %s to %s by %s",
        ticket.number,
        prev_name,
        new_name,
        actor,
    )

    return ticket


# ---------------------------------------------------------------------------
# Status transition enforcement
# ---------------------------------------------------------------------------

# Allowed transitions keyed by TicketStatus.slug.
# Statuses not in this map are unrestricted (custom tenant statuses).
ALLOWED_TRANSITIONS = {
    "open": ["in-progress", "waiting", "resolved", "closed"],
    "in-progress": ["open", "waiting", "resolved", "closed"],
    "waiting": ["open", "in-progress"],
    "resolved": ["closed", "open"],
    "closed": [],  # Terminal — no transitions out
}


def validate_status_transition(ticket, new_status):
    """
    Raise ``ValidationError`` if the transition from the ticket's current
    status to *new_status* is not allowed by the transition map.

    Statuses whose slug is not in ``ALLOWED_TRANSITIONS`` are unrestricted
    (custom tenant statuses can transition freely).
    """
    from django.core.exceptions import ValidationError

    old_status = ticket.status
    if old_status is None:
        return  # New ticket — any status is fine

    if old_status.pk == new_status.pk:
        return  # No-op — not a transition

    old_slug = old_status.slug
    new_slug = new_status.slug

    allowed = ALLOWED_TRANSITIONS.get(old_slug)
    if allowed is None:
        return  # Old status not in map → unrestricted

    # Terminal status: empty allowed list means no transitions out
    if not allowed:
        raise ValidationError(
            f"Cannot transition from '{old_status.name}' — "
            f"'{old_slug}' is a terminal status."
        )

    if new_slug not in allowed:
        allowed_display = ", ".join(allowed)
        raise ValidationError(
            f"Cannot transition from '{old_status.name}' to '{new_status.name}'. "
            f"Allowed transitions from '{old_slug}': {allowed_display}."
        )


def transition_ticket_status(ticket, new_status, actor, request=None):
    """
    Validate and execute a status transition.

    This is the single entry point for status changes from both the API
    and internal services. It enforces the transition map, then delegates
    to ``change_ticket_status()`` for the actual save and dual-write
    logging.

    Raises:
        ValidationError: If the transition is not allowed.

    Returns:
        The updated Ticket instance.
    """
    validate_status_transition(ticket, new_status)
    return change_ticket_status(ticket, new_status, actor, request=request)


def resume_from_wait(ticket, actor, request=None):
    """
    Transition a ticket out of a ``pauses_sla=True`` status, restoring
    the status it had before entering the wait state.

    Falls back to the tenant's "open" status when ``pre_wait_status`` is
    null, deleted, or not a valid transition target.

    No-op if the ticket is not currently on a pauses_sla status.

    Returns:
        The updated Ticket instance, or the unchanged instance if no-op.
    """
    if not ticket.status or not getattr(ticket.status, "pauses_sla", False):
        return ticket

    from apps.tickets.models import TicketStatus

    target = ticket.pre_wait_status

    # Validate the snapshot is still usable
    if target is not None:
        try:
            # Re-fetch to ensure it hasn't been deleted
            target = TicketStatus.objects.get(pk=target.pk)
        except TicketStatus.DoesNotExist:
            target = None

    # Guard: don't restore to a closed or pauses_sla status
    if target is not None and (target.is_closed or target.pauses_sla):
        target = None

    # Fallback: tenant's "open" status
    if target is None:
        target = TicketStatus.objects.filter(slug="open").first()

    if target is None or target.pk == ticket.status_id:
        return ticket

    return change_ticket_status(ticket, target, actor, request=request)


# ---------------------------------------------------------------------------
# Status change
# ---------------------------------------------------------------------------


@transaction.atomic
def change_ticket_status(ticket, new_status, actor, request=None):
    """
    Change a ticket's status and write both log entries.

    Handles closed/reopened detection: if the new status has ``is_closed=True``,
    records a CLOSED event; if moving from closed to open, records REOPENED.

    Also records ``status_changed_at`` and ``status_changed_by`` on every
    transition for audit purposes.

    Phase 4 hooks:
    - Transition TO "resolved": set ``solved_at``, schedule auto-close task
      and CSAT survey email via ``transaction.on_commit()``.
    - Transition FROM "resolved": revoke the auto-close task (best-effort),
      clear ``solved_at`` and ``auto_close_task_id``.
    - Transition TO ``is_closed=True``: check resolution SLA breach, emit
      ``ticket_closed`` signal via ``transaction.on_commit()``.

    Args:
        ticket: The Ticket instance.
        new_status: The new TicketStatus instance.
        actor: The User performing the change.
        request: Optional HTTP request (for IP logging).

    Returns:
        The updated Ticket instance.
    """
    old_status = ticket.status
    old_status_name = old_status.name if old_status else None
    old_slug = old_status.slug if old_status else None
    new_status_name = new_status.name
    new_slug = new_status.slug
    tenant = ticket.tenant

    if old_status and old_status.pk == new_status.pk:
        return ticket  # No change

    now = timezone.now()

    # 1. Update the ticket (pre_save signal handles resolved_at/closed_at)
    update_fields = ["status", "status_changed_at", "status_changed_by", "updated_at"]
    ticket.status = new_status
    ticket.status_changed_at = now
    ticket.status_changed_by = actor

    # --- Pre-wait status snapshot/restore ---
    old_pauses = getattr(old_status, "pauses_sla", False) if old_status else False
    new_pauses = getattr(new_status, "pauses_sla", False)

    if new_pauses and not old_pauses:
        # Entering a wait state — snapshot the previous status
        ticket.pre_wait_status = old_status
        update_fields.append("pre_wait_status")
    elif old_pauses and not new_pauses:
        # Leaving a wait state — clear the snapshot (it's been consumed
        # or the agent chose a different target explicitly)
        ticket.pre_wait_status = None
        update_fields.append("pre_wait_status")

    # --- Phase 4: Transition TO "resolved" ---
    if new_slug == "resolved" and old_slug != "resolved":
        ticket.solved_at = now
        update_fields.append("solved_at")

    # --- Phase 4: Transition FROM "resolved" (reopen) ---
    if old_slug == "resolved" and new_slug != "resolved":
        _cancel_auto_close_task(ticket)
        ticket.solved_at = None
        ticket.auto_close_task_id = None
        update_fields.extend(["solved_at", "auto_close_task_id"])

    # --- Closure timestamps (resolved_at / closed_at) ---
    # The pre_save signal sets these on the instance, but since we use
    # save(update_fields=...) we must include them explicitly.
    was_closed = old_status.is_closed if old_status else False
    now_closed = new_status.is_closed

    if now_closed and not was_closed:
        if not ticket.resolved_at:
            ticket.resolved_at = now
        if not ticket.closed_at:
            ticket.closed_at = now
        update_fields.extend(["resolved_at", "closed_at"])
    elif was_closed and not now_closed:
        ticket.resolved_at = None
        ticket.closed_at = None
        update_fields.extend(["resolved_at", "closed_at"])

    # --- Phase 5: Resolution breach check at closure ---
    if now_closed and not was_closed:
        _check_resolution_breach(ticket, now)
        if ticket.sla_resolution_breached:
            update_fields.append("sla_resolution_breached")

    ticket.save(update_fields=update_fields)

    # Determine the timeline event type
    if now_closed and not was_closed:
        timeline_event = TicketActivity.Event.CLOSED
        audit_action = ActivityLog.Action.CLOSED
        timeline_msg = f"Ticket closed ({new_status_name})"
    elif was_closed and not now_closed:
        timeline_event = TicketActivity.Event.REOPENED
        audit_action = ActivityLog.Action.REOPENED
        timeline_msg = f"Ticket reopened ({new_status_name})"
    else:
        timeline_event = TicketActivity.Event.STATUS_CHANGED
        audit_action = ActivityLog.Action.STATUS_CHANGED
        timeline_msg = f"Status changed from {old_status_name} to {new_status_name}"

    # 2. Audit log (ActivityLog)
    log_activity(
        tenant=tenant,
        actor=actor,
        content_object=ticket,
        action=audit_action,
        description=timeline_msg,
        changes={"status": [old_status_name, new_status_name]},
        request=request,
    )

    # 3. Ticket timeline (TicketActivity)
    _create_ticket_activity(
        ticket,
        actor=actor,
        event=timeline_event,
        message=timeline_msg,
        metadata={
            "old_status": old_status_name,
            "new_status": new_status_name,
        },
    )

    # --- Phase 4: Schedule auto-close + CSAT after commit ---
    if new_slug == "resolved" and old_slug != "resolved":
        ticket_pk = str(ticket.pk)
        tenant_pk = str(tenant.pk)

        def _schedule_resolved_tasks():
            _schedule_auto_close(ticket_pk, tenant_pk)
            _schedule_csat_survey(ticket_pk, tenant_pk)

        transaction.on_commit(_schedule_resolved_tasks)

    # --- Phase 5: Emit ticket_closed signal after commit ---
    if now_closed and not was_closed:
        _ticket_ref = {
            "pk": str(ticket.pk),
            "tenant_pk": str(tenant.pk),
            "resolution_time_seconds": (
                (now - ticket.created_at).total_seconds()
            ),
            "first_response_time_seconds": (
                (ticket.first_responded_at - ticket.created_at).total_seconds()
                if ticket.first_responded_at else None
            ),
            "sla_first_response_breached": ticket.sla_response_breached,
            "sla_resolution_breached": ticket.sla_resolution_breached,
            "csat_rating": ticket.csat_rating,
            "assignee_id": str(ticket.assignee_id) if ticket.assignee_id else None,
            "team_id": str(ticket.queue_id) if ticket.queue_id else None,
            "category": ticket.category,
            "priority": ticket.priority,
            "channel": ticket.channel,
            "tenant_id": str(tenant.pk),
        }

        def _emit_closed_signal():
            from apps.tickets.signals import ticket_closed

            try:
                # Re-fetch to get committed state
                fresh = Ticket.unscoped.get(pk=_ticket_ref["pk"])
                ticket_closed.send(
                    sender=Ticket,
                    instance=fresh,
                    payload=_ticket_ref,
                )
            except Exception:
                logger.exception(
                    "Failed to emit ticket_closed signal for ticket %s",
                    _ticket_ref["pk"],
                )

        transaction.on_commit(_emit_closed_signal)

    # --- Phase 5: Emit sla_resolution_breached signal after commit ---
    if now_closed and not was_closed and ticket.sla_resolution_breached:
        _breach_ticket_pk = str(ticket.pk)

        def _emit_breach_signal():
            from apps.tickets.signals import sla_resolution_breached

            try:
                fresh = Ticket.unscoped.get(pk=_breach_ticket_pk)
                sla_resolution_breached.send(
                    sender=Ticket,
                    instance=fresh,
                    closed_at=fresh.closed_at,
                    due_at=fresh.sla_resolution_due,
                )
            except Exception:
                logger.exception(
                    "Failed to emit sla_resolution_breached signal for ticket %s",
                    _breach_ticket_pk,
                )

        transaction.on_commit(_emit_breach_signal)

    return ticket


def _check_resolution_breach(ticket, now):
    """
    At closure, compare closed_at against sla_resolution_due.
    If breached, set sla_resolution_breached=True on the ticket instance
    (caller must include it in update_fields).
    """
    if ticket.sla_resolution_breached:
        return  # Already flagged
    if not ticket.sla_resolution_due:
        return  # No SLA deadline

    # closed_at is set by the pre_save signal; use `now` as fallback
    closed_at = ticket.closed_at or now
    if closed_at > ticket.sla_resolution_due:
        ticket.sla_resolution_breached = True
        logger.info(
            "Resolution SLA breached for Ticket #%s (closed: %s, due: %s).",
            ticket.number, closed_at, ticket.sla_resolution_due,
        )


def _cancel_auto_close_task(ticket):
    """
    Best-effort revoke of the pending auto-close Celery task.
    The task body's idempotency check is the authoritative guard.
    """
    task_id = ticket.auto_close_task_id
    if not task_id:
        return

    try:
        from main.celery import app

        app.control.revoke(task_id)
        logger.info(
            "Revoked auto-close task %s for Ticket #%s.",
            task_id, ticket.number,
        )
    except Exception:
        logger.warning(
            "Failed to revoke auto-close task %s for Ticket #%s (best-effort).",
            task_id, ticket.number,
        )


def _schedule_auto_close(ticket_pk, tenant_pk):
    """Schedule the auto-close task for a resolved ticket."""
    import datetime as dt

    from apps.tenants.models import Tenant
    from apps.tickets.models import Ticket
    from apps.tickets.tasks import auto_close_ticket

    try:
        tenant = Tenant.objects.select_related("settings").get(pk=tenant_pk)
        days = getattr(tenant.settings, "auto_close_days", 5) if hasattr(tenant, "settings") else 5

        eta = timezone.now() + dt.timedelta(days=days)
        result = auto_close_ticket.apply_async(
            args=[ticket_pk],
            eta=eta,
        )

        # Store the task ID on the ticket
        Ticket.unscoped.filter(pk=ticket_pk).update(
            auto_close_task_id=result.id,
        )
        logger.info(
            "Scheduled auto-close task %s for ticket %s (eta=%s, days=%d).",
            result.id, ticket_pk, eta, days,
        )
    except Exception:
        logger.exception(
            "Failed to schedule auto-close task for ticket %s.", ticket_pk,
        )


def _schedule_csat_survey(ticket_pk, tenant_pk):
    """Schedule the CSAT survey email for a resolved ticket."""
    import datetime as dt

    from apps.tenants.models import Tenant
    from apps.tickets.tasks import send_csat_survey_email

    try:
        tenant = Tenant.objects.select_related("settings").get(pk=tenant_pk)
        delay_min = (
            getattr(tenant.settings, "csat_delay_minutes", 60)
            if hasattr(tenant, "settings") else 60
        )

        eta = timezone.now() + dt.timedelta(minutes=delay_min)
        send_csat_survey_email.apply_async(
            args=[ticket_pk, tenant_pk],
            eta=eta,
        )
        logger.info(
            "Scheduled CSAT survey for ticket %s (eta=%s, delay=%dmin).",
            ticket_pk, eta, delay_min,
        )
    except Exception:
        logger.exception(
            "Failed to schedule CSAT survey for ticket %s.", ticket_pk,
        )


# ---------------------------------------------------------------------------
# Close ticket
# ---------------------------------------------------------------------------


@transaction.atomic
def close_ticket(ticket, actor, request=None):
    """
    Close a ticket by transitioning it to the tenant's closed status.

    Finds the first TicketStatus with ``is_closed=True`` for the ticket's
    tenant and delegates to ``change_ticket_status()``. If the ticket is
    already closed this is a no-op.

    Args:
        ticket: The Ticket instance to close.
        actor: The User closing the ticket.
        request: Optional HTTP request (for IP logging).

    Returns:
        The updated Ticket instance.

    Raises:
        ValueError: If no closed status exists for this tenant.
    """
    if ticket.status and ticket.status.is_closed:
        return ticket  # Already closed

    from apps.tickets.models import TicketStatus

    closed_status = (
        TicketStatus.objects.filter(tenant=ticket.tenant, is_closed=True)
        .order_by("order")
        .first()
    )
    if closed_status is None:
        raise ValueError(
            "No closed status configured for this tenant. "
            "Create a TicketStatus with is_closed=True."
        )

    return transition_ticket_status(ticket, closed_status, actor, request=request)


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


@transaction.atomic
def escalate_ticket(ticket, actor, reason, assignee=None, queue=None, request=None):
    """
    Escalate a ticket to a different agent or queue.

    Performs all of the following atomically:
    1. Increment ``escalation_count`` and set ``escalated_at``.
    2. Reassign the ticket if *assignee* is provided.
    3. Change the queue if *queue* is provided.
    4. Post an internal Comment with the escalation reason.
    5. Re-run SLA attachment if the new context has a different SLA policy.
    6. Write to both logging systems.

    Args:
        ticket: The Ticket instance to escalate.
        actor: The User performing the escalation.
        reason: Text explanation for the escalation (required).
        assignee: Optional User to reassign the ticket to.
        queue: Optional Queue to move the ticket to.
        request: Optional HTTP request (for IP logging).

    Returns:
        The updated Ticket instance.

    Raises:
        ValueError: If the assignee is not an active tenant member.
    """
    from django.contrib.contenttypes.models import ContentType

    from apps.comments.models import Comment

    tenant = ticket.tenant
    now = timezone.now()

    # 1. Update escalation counters
    ticket.escalation_count = (ticket.escalation_count or 0) + 1
    ticket.escalated_at = now
    update_fields = ["escalation_count", "escalated_at", "updated_at"]

    # 2. Reassign if requested
    old_assignee = ticket.assignee
    if assignee is not None:
        from apps.accounts.models import TenantMembership

        if not TenantMembership.objects.filter(
            user=assignee, tenant=tenant, is_active=True,
        ).exists():
            raise ValueError("Assignee is not an active member of this tenant.")

        ticket.assignee = assignee
        ticket.assigned_by = actor
        ticket.assigned_at = now
        update_fields.extend(["assignee", "assigned_by", "assigned_at"])

        # Create TicketAssignment audit record
        TicketAssignment.objects.create(
            ticket=ticket,
            assigned_to=assignee,
            assigned_by=actor,
            note=f"Escalation: {reason}",
            tenant=tenant,
        )

    # 3. Change queue if requested
    old_queue = ticket.queue
    if queue is not None:
        ticket.queue = queue
        update_fields.append("queue")

    ticket.save(update_fields=update_fields)

    # 4. Post internal comment with escalation reason
    ticket_ct = ContentType.objects.get_for_model(Ticket)
    Comment(
        content_type=ticket_ct,
        object_id=ticket.pk,
        author=actor,
        body=f"**Escalation (#{ticket.escalation_count}):** {reason}",
        is_internal=True,
        tenant=tenant,
    ).save()

    # 5. Extend SLA deadlines (does NOT reset them from scratch)
    _extend_sla_on_escalation(ticket, now, actor=actor)

    # NOTE: record_first_response() is intentionally NOT called here.
    # Escalation is an internal action, not a customer-facing reply.

    # 6. Dual-write logging
    assignee_msg = ""
    if assignee is not None:
        old_name = old_assignee.get_full_name() if old_assignee else "Unassigned"
        new_name = assignee.get_full_name()
        assignee_msg = f" Reassigned from {old_name} to {new_name}."

    queue_msg = ""
    if queue is not None:
        old_q = old_queue.name if old_queue else "None"
        queue_msg = f" Queue changed from {old_q} to {queue.name}."

    msg = (
        f"Escalated (#{ticket.escalation_count}). "
        f"Reason: {reason}.{assignee_msg}{queue_msg}"
    )

    log_activity(
        tenant=tenant,
        actor=actor,
        content_object=ticket,
        action=ActivityLog.Action.FIELD_CHANGED,
        description=msg,
        changes={
            "escalation_count": [ticket.escalation_count - 1, ticket.escalation_count],
        },
        request=request,
    )

    _create_ticket_activity(
        ticket,
        actor=actor,
        event=TicketActivity.Event.ESCALATED_MANUAL,
        message=msg,
        metadata={
            "reason": reason,
            "escalation_number": ticket.escalation_count,
            "old_assignee": str(old_assignee.pk) if old_assignee else None,
            "new_assignee": str(assignee.pk) if assignee else None,
            "old_queue": str(old_queue.pk) if old_queue else None,
            "new_queue": str(queue.pk) if queue else None,
        },
    )

    logger.info(
        "Ticket #%s escalated (#%d) by %s: %s",
        ticket.number,
        ticket.escalation_count,
        actor,
        reason,
    )

    return ticket


def _extend_sla_on_escalation(ticket, now, actor=None):
    """
    After escalation, extend SLA deadlines instead of resetting them.

    Strategy per deadline:
    - If time remains: extend by 50 % of the remaining time, clamped to
      [15 business minutes, full-policy-duration business minutes].
    - If already breached (deadline in the past): set to now + 15 business
      minutes as a grace window.
    - If no matching SLAPolicy exists for the ticket's priority: no-op.

    Also attaches a new policy if the priority maps to a different one,
    stamps ``sla_extended_at``, and writes dual logs with old/new deadlines
    and the minutes added.
    """
    from apps.tickets.models import SLAPolicy
    from apps.tickets.sla import add_business_minutes

    MIN_EXTENSION_MINUTES = 15
    EXTENSION_RATIO = 0.5

    tenant = ticket.tenant

    new_policy = (
        SLAPolicy.unscoped
        .filter(tenant=tenant, priority=ticket.priority, is_active=True)
        .first()
    )
    if new_policy is None:
        return  # No SLA for this priority — nothing to extend

    update_fields = []

    # Attach new policy if different
    if ticket.sla_policy_id != new_policy.pk:
        ticket.sla_policy = new_policy
        update_fields.append("sla_policy")

    # --- Helper: compute the extended deadline for one field ---
    def _extend_deadline(current_due, policy_minutes):
        if current_due is None:
            # No deadline was ever set — initialise from now
            return add_business_minutes(now, policy_minutes, tenant), policy_minutes

        remaining_seconds = (current_due - now).total_seconds()

        if remaining_seconds <= 0:
            # Already breached — grant a minimum grace window
            new_due = add_business_minutes(now, MIN_EXTENSION_MINUTES, tenant)
            added = MIN_EXTENSION_MINUTES
        else:
            remaining_minutes = remaining_seconds / 60
            raw_extension = remaining_minutes * EXTENSION_RATIO
            # Clamp to [MIN_EXTENSION_MINUTES, policy_minutes]
            clamped = max(MIN_EXTENSION_MINUTES, min(raw_extension, policy_minutes))
            new_due = add_business_minutes(current_due, clamped, tenant)
            added = round(clamped, 1)

        return new_due, added

    # --- Extend first-response deadline ---
    old_response_due = ticket.sla_first_response_due
    new_response_due, response_added = _extend_deadline(
        old_response_due, new_policy.first_response_minutes,
    )
    ticket.sla_first_response_due = new_response_due
    update_fields.append("sla_first_response_due")

    # --- Extend resolution deadline ---
    old_resolution_due = ticket.sla_resolution_due
    new_resolution_due, resolution_added = _extend_deadline(
        old_resolution_due, new_policy.resolution_minutes,
    )
    ticket.sla_resolution_due = new_resolution_due
    update_fields.append("sla_resolution_due")

    # --- Stamp extension timestamp ---
    ticket.sla_extended_at = now
    update_fields.append("sla_extended_at")

    update_fields.append("updated_at")
    ticket.save(update_fields=update_fields)

    # Structured SLA audit log
    log_sla_change(ticket, old_response_due, old_resolution_due, "escalation", actor=actor)

    logger.info(
        "SLA extended for Ticket #%s on escalation "
        "(policy: %s, response +%.1f min → %s, resolution +%.1f min → %s).",
        ticket.number,
        new_policy.name,
        response_added, new_response_due,
        resolution_added, new_resolution_due,
    )


# ---------------------------------------------------------------------------
# Priority change
# ---------------------------------------------------------------------------


@transaction.atomic
def change_ticket_priority(ticket, new_priority, actor, request=None):
    """
    Change a ticket's priority and write both log entries.
    """
    old_priority = ticket.priority
    old_display = ticket.get_priority_display()
    tenant = ticket.tenant

    if old_priority == new_priority:
        return ticket

    ticket.priority = new_priority
    ticket.save(update_fields=["priority", "updated_at"])

    new_display = ticket.get_priority_display()

    # Audit log
    log_activity(
        tenant=tenant,
        actor=actor,
        content_object=ticket,
        action=ActivityLog.Action.FIELD_CHANGED,
        description=f"Changed priority from {old_display} to {new_display}",
        changes={"priority": [old_display, new_display]},
        request=request,
    )

    # Ticket timeline
    _create_ticket_activity(
        ticket,
        actor=actor,
        event=TicketActivity.Event.PRIORITY_CHANGED,
        message=f"Priority changed from {old_display} to {new_display}",
        metadata={
            "old_priority": old_priority,
            "new_priority": new_priority,
        },
    )

    return ticket


# ---------------------------------------------------------------------------
# Comment logging (timeline only -- audit log handled by CommentViewSet)
# ---------------------------------------------------------------------------


def log_ticket_comment(ticket, actor, is_internal=False):
    """
    Record a comment on the ticket timeline.
    The audit log entry is written separately by the comment system.
    """
    event = (
        TicketActivity.Event.INTERNAL_NOTE
        if is_internal
        else TicketActivity.Event.COMMENTED
    )
    label = "internal note" if is_internal else "comment"

    _create_ticket_activity(
        ticket,
        actor=actor,
        event=event,
        message=f"Added a {label}",
    )


# ---------------------------------------------------------------------------
# First response tracking
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Bulk update
# ---------------------------------------------------------------------------


def bulk_update_tickets(tickets, action, params, user, request=None):
    """
    Execute a bulk action on a queryset of tickets with activity logging.

    Each ticket is handled independently so one failure does not roll back
    successful updates. Individual service functions (assign_ticket, etc.)
    use their own ``@transaction.atomic`` blocks.

    Supported actions: assign, change_status, change_priority, add_tag, delete.
    """
    count = 0
    details = []

    for ticket in tickets:
        try:
            if action == "assign":
                from django.contrib.auth import get_user_model

                User = get_user_model()
                assignee = User.objects.get(pk=params["user_id"])
                assign_ticket(ticket, assignee, user, request=request)

            elif action == "change_status":
                from apps.tickets.models import TicketStatus

                new_status = TicketStatus.objects.get(pk=params["status_id"])
                change_ticket_status(ticket, new_status, user, request=request)

            elif action == "change_priority":
                change_ticket_priority(ticket, params["priority"], user, request=request)

            elif action == "add_tag":
                tags = params.get("tags", [])
                existing_tags = ticket.tags or []
                ticket.tags = list(set(existing_tags + tags))
                ticket.save(update_fields=["tags", "updated_at"])
                log_activity(
                    tenant=ticket.tenant,
                    actor=user,
                    content_object=ticket,
                    action=ActivityLog.Action.FIELD_CHANGED,
                    description=f"Added tags: {', '.join(tags)}",
                    changes={"tags": [existing_tags, ticket.tags]},
                    request=request,
                )

            elif action == "delete":
                ticket_number = ticket.number
                ticket.delete()
                details.append(f"Deleted ticket #{ticket_number}")
                count += 1
                continue

            else:
                raise ValueError(f"Unknown action: {action}")

            count += 1
            details.append(f"Updated ticket #{ticket.number}")

        except Exception as e:
            logger.exception(
                "Bulk action '%s' failed for ticket #%s", action, ticket.number,
            )
            details.append(f"Failed to update ticket #{ticket.number}: {type(e).__name__}")

    return {"count": count, "details": details}


def record_first_response(ticket, actor):
    """
    Stamp ``first_responded_at`` on the ticket if not already set.

    This must ONLY be called when a customer-facing outbound action occurs:
    - An external (non-internal) comment by a non-creator agent
    - An outbound email sent on behalf of an agent

    Assignment and escalation are internal actions and do NOT count.

    Uses an atomic UPDATE with a WHERE filter to avoid race conditions
    where two concurrent responses both see ``first_responded_at IS NULL``.

    After stamping, writes dual logs (TicketActivity + ActivityLog) for the
    "first reply sent" event, then checks SLA breach.

    No model field changes are required — ``first_responded_at`` already
    exists on Ticket.
    """
    if ticket.first_responded_at is not None:
        return
    if actor and actor.pk == ticket.created_by_id:
        return

    from apps.tickets.models import Ticket

    now = timezone.now()
    updated = Ticket.unscoped.filter(
        pk=ticket.pk,
        first_responded_at__isnull=True,
    ).update(
        first_responded_at=now,
        updated_at=now,
    )
    if updated:
        ticket.refresh_from_db(fields=[
            "first_responded_at", "sla_first_response_due",
            "sla_response_breached", "updated_at",
        ])

        # Dual-write: timeline + audit log for "first reply sent"
        actor_name = actor.get_full_name() or str(actor) if actor else "System"
        msg = f"First response sent by {actor_name}"

        _create_ticket_activity(
            ticket,
            actor=actor,
            event=TicketActivity.Event.FIRST_RESPONSE,
            message=msg,
            metadata={"responded_at": now.isoformat()},
        )
        log_activity(
            tenant=ticket.tenant,
            actor=actor,
            content_object=ticket,
            action=ActivityLog.Action.FIELD_CHANGED,
            description=msg,
            changes={"first_responded_at": [None, now.isoformat()]},
        )

        _check_first_response_breach(ticket)


def _check_first_response_breach(ticket):
    """
    Compare ``first_responded_at`` against ``sla_first_response_due``.

    If the response was late, set ``sla_response_breached = True`` (via
    atomic UPDATE to avoid races) and fire the
    ``sla_first_response_breached`` signal.
    """
    if ticket.sla_response_breached:
        return  # Already flagged
    if not ticket.sla_first_response_due:
        return  # No SLA deadline set
    if not ticket.first_responded_at:
        return  # Should not happen, but guard

    if ticket.first_responded_at > ticket.sla_first_response_due:
        from apps.tickets.models import Ticket

        flagged = Ticket.unscoped.filter(
            pk=ticket.pk,
            sla_response_breached=False,
        ).update(
            sla_response_breached=True,
            updated_at=timezone.now(),
        )
        if flagged:
            ticket.refresh_from_db(fields=["sla_response_breached", "updated_at"])

            from apps.tickets.signals import sla_first_response_breached

            sla_first_response_breached.send(
                sender=Ticket,
                instance=ticket,
                responded_at=ticket.first_responded_at,
                due_at=ticket.sla_first_response_due,
            )
            logger.info(
                "First-response SLA breached for Ticket #%s "
                "(responded: %s, due: %s).",
                ticket.number,
                ticket.first_responded_at,
                ticket.sla_first_response_due,
            )


# ---------------------------------------------------------------------------
# Ticket merge
# ---------------------------------------------------------------------------


@transaction.atomic
def merge_tickets(primary, secondary, actor, request=None):
    """
    Merge *secondary* into *primary*.

    Moves all comments, timeline entries, and attachments from the
    secondary ticket to the primary. Creates a ``duplicate_of`` link,
    closes the secondary, and writes dual audit logs on the primary.

    SLA records are NOT transferred — the primary keeps its own.

    Both tickets are locked with ``select_for_update()`` for the
    duration of the transaction to prevent concurrent modifications.

    Args:
        primary: The Ticket that will absorb the secondary's data.
        secondary: The Ticket that will be closed after merge.
        actor: The User performing the merge.
        request: Optional HTTP request (for IP logging).

    Returns:
        The updated primary Ticket instance.

    Raises:
        ValueError: If tickets belong to different tenants or are the same.
    """
    from django.contrib.contenttypes.models import ContentType

    from apps.attachments.models import Attachment
    from apps.comments.models import Comment
    from apps.tickets.models import TicketLink, TicketStatus

    if primary.pk == secondary.pk:
        raise ValueError("Cannot merge a ticket into itself.")
    if primary.tenant_id != secondary.tenant_id:
        raise ValueError("Cannot merge tickets from different tenants.")

    # Lock both tickets to prevent concurrent modifications
    Ticket.unscoped.select_for_update().filter(
        pk__in=[primary.pk, secondary.pk],
    ).exists()

    tenant = primary.tenant
    ticket_ct = ContentType.objects.get_for_model(Ticket)

    # 1. Move comments from secondary → primary
    moved_comments = Comment.unscoped.filter(
        content_type=ticket_ct,
        object_id=secondary.pk,
    ).update(object_id=primary.pk)

    # 2. Move timeline entries from secondary → primary
    moved_activities = TicketActivity.unscoped.filter(
        ticket=secondary,
    ).update(ticket=primary)

    # 3. Move attachments from secondary → primary
    moved_attachments = Attachment.unscoped.filter(
        content_type=ticket_ct,
        object_id=secondary.pk,
    ).update(object_id=primary.pk)

    # 4. Create duplicate_of link
    TicketLink.objects.get_or_create(
        source_ticket=secondary,
        target_ticket=primary,
        link_type=TicketLink.LinkType.DUPLICATE_OF,
        defaults={"created_by": actor, "tenant": tenant},
    )

    # 5. Close the secondary ticket
    closed_status = (
        TicketStatus.objects.filter(is_closed=True)
        .order_by("order")
        .first()
    )
    if closed_status:
        secondary.status = closed_status
        if not secondary.closed_at:
            secondary.closed_at = timezone.now()
        if not secondary.resolved_at:
            secondary.resolved_at = timezone.now()

    secondary.merged_into = primary
    secondary._skip_signal_logging = True
    secondary.save(update_fields=[
        "status", "closed_at", "resolved_at", "merged_into", "updated_at",
    ])

    # 6. System comment on the secondary
    Comment(
        content_type=ticket_ct,
        object_id=secondary.pk,
        author=actor,
        body=f"Merged into #{primary.number} by {actor.get_full_name() or actor.email}",
        is_internal=True,
        tenant=tenant,
    ).save()

    # 7. Dual-write audit on the primary ticket
    msg = (
        f"Ticket #{secondary.number} merged into this ticket by "
        f"{actor.get_full_name() or actor.email} "
        f"({moved_comments} comments, {moved_activities} activities, "
        f"{moved_attachments} attachments transferred)"
    )

    log_activity(
        tenant=tenant,
        actor=actor,
        content_object=primary,
        action=ActivityLog.Action.UPDATED,
        description=msg,
        changes={
            "merged_ticket": secondary.number,
            "comments_transferred": moved_comments,
            "activities_transferred": moved_activities,
            "attachments_transferred": moved_attachments,
        },
        request=request,
    )

    _create_ticket_activity(
        primary,
        actor=actor,
        event=TicketActivity.Event.STATUS_CHANGED,
        message=msg,
        metadata={
            "merged_ticket_id": str(secondary.pk),
            "merged_ticket_number": secondary.number,
        },
    )

    logger.info(
        "Ticket #%s merged into #%s by %s.",
        secondary.number, primary.number, actor,
    )

    return primary


# ---------------------------------------------------------------------------
# Ticket split
# ---------------------------------------------------------------------------


@transaction.atomic
def split_ticket(source, comment_ids, actor, new_ticket_data, request=None):
    """
    Split selected comments from *source* into a new child ticket.

    Creates a new ticket with *new_ticket_data*, moves the specified
    comments from the source to the child, links them as ``related_to``,
    initialises SLA on the child, and writes dual audit logs on both.

    Args:
        source: The Ticket instance to split from.
        comment_ids: List of Comment UUIDs to move to the child.
        actor: The User performing the split.
        new_ticket_data: Dict with keys: subject (required), queue (optional
            UUID), priority (optional, defaults to source's).
        request: Optional HTTP request (for IP logging).

    Returns:
        The newly created child Ticket instance.

    Raises:
        ValueError: If comment_ids is empty, or any comment doesn't belong
            to the source ticket / tenant.
    """
    from django.contrib.contenttypes.models import ContentType

    from apps.comments.models import Comment
    from apps.tickets.models import TicketLink, TicketStatus

    if not comment_ids:
        raise ValueError("At least one comment must be selected for the split.")

    tenant = source.tenant
    ticket_ct = ContentType.objects.get_for_model(Ticket)

    # Lock the source ticket
    Ticket.unscoped.select_for_update().filter(pk=source.pk).exists()

    # Validate all comments belong to the source ticket
    comments = Comment.unscoped.filter(
        pk__in=comment_ids,
        content_type=ticket_ct,
        object_id=source.pk,
    )
    if comments.count() != len(comment_ids):
        raise ValueError(
            "One or more comment IDs do not belong to this ticket."
        )

    # 1. Determine default status
    default_status = TicketStatus.objects.filter(is_default=True).first()
    if not default_status:
        default_status = TicketStatus.objects.first()
    if not default_status:
        raise ValueError("No ticket statuses configured for this tenant.")

    # 2. Resolve optional queue FK
    queue = None
    queue_id = new_ticket_data.get("queue")
    if queue_id:
        from apps.tickets.models import Queue

        try:
            queue = Queue.objects.get(pk=queue_id)
        except Queue.DoesNotExist:
            raise ValueError(f"Queue {queue_id} not found.")

    # 3. Create the child ticket
    child = Ticket(
        subject=new_ticket_data["subject"],
        description=f"Split from ticket #{source.number}",
        status=default_status,
        priority=new_ticket_data.get("priority", source.priority),
        queue=queue,
        assignee=source.assignee,
        assigned_by=actor,
        assigned_at=timezone.now() if source.assignee else None,
        contact=source.contact,
        created_by=actor,
        tenant=tenant,
    )
    child.save()

    # 4. Initialize SLA on the child
    initialize_sla(child)

    # 5. Move selected comments from source → child
    moved = comments.update(object_id=child.pk)

    # 6. Create related_to link
    TicketLink.objects.create(
        source_ticket=child,
        target_ticket=source,
        link_type=TicketLink.LinkType.RELATED_TO,
        created_by=actor,
        tenant=tenant,
    )

    actor_name = actor.get_full_name() or actor.email

    # 7. System comment on source
    Comment(
        content_type=ticket_ct,
        object_id=source.pk,
        author=actor,
        body=f"Ticket split — #{child.number} created by {actor_name}",
        is_internal=True,
        tenant=tenant,
    ).save()

    # 8. System comment on child
    Comment(
        content_type=ticket_ct,
        object_id=child.pk,
        author=actor,
        body=f"Split from #{source.number} by {actor_name}",
        is_internal=True,
        tenant=tenant,
    ).save()

    # 9. Dual-write audit on source
    source_msg = (
        f"Ticket split: {moved} comment(s) moved to #{child.number} "
        f"by {actor_name}"
    )
    log_activity(
        tenant=tenant,
        actor=actor,
        content_object=source,
        action=ActivityLog.Action.UPDATED,
        description=source_msg,
        changes={
            "split_child_ticket": child.number,
            "comments_moved": moved,
        },
        request=request,
    )
    _create_ticket_activity(
        source,
        actor=actor,
        event=TicketActivity.Event.STATUS_CHANGED,
        message=source_msg,
        metadata={
            "child_ticket_id": str(child.pk),
            "child_ticket_number": child.number,
            "comments_moved": moved,
        },
    )

    # 10. Dual-write audit on child
    child_msg = f"Created from split of #{source.number} by {actor_name}"
    log_activity(
        tenant=tenant,
        actor=actor,
        content_object=child,
        action=ActivityLog.Action.CREATED,
        description=child_msg,
        changes={"split_source_ticket": source.number},
        request=request,
    )
    _create_ticket_activity(
        child,
        actor=actor,
        event=TicketActivity.Event.CREATED,
        message=child_msg,
        metadata={
            "source_ticket_id": str(source.pk),
            "source_ticket_number": source.number,
        },
    )

    logger.info(
        "Ticket #%s split: %d comments moved to new ticket #%s by %s.",
        source.number, moved, child.number, actor,
    )

    return child


# ---------------------------------------------------------------------------
# Macros
# ---------------------------------------------------------------------------


def render_macro(macro, ticket, agent):
    """
    Render a macro body with variable substitution.

    Replaces ``{{variable}}`` placeholders using a flat mapping.
    Unknown variables are left as-is rather than raising errors.

    Supported variables:
        {{ticket.number}}, {{ticket.subject}}, {{ticket.queue}},
        {{contact.name}}, {{contact.first_name}}, {{contact.email}},
        {{agent.name}}, {{agent.first_name}}, {{agent.email}}
    """
    contact = ticket.contact
    queue = ticket.queue

    replacements = {
        "{{ticket.number}}": str(ticket.number),
        "{{ticket.subject}}": ticket.subject or "",
        "{{ticket.queue}}": queue.name if queue else "",
        "{{contact.name}}": contact.full_name if contact else "",
        "{{contact.first_name}}": contact.first_name if contact else "",
        "{{contact.email}}": contact.email if contact else "",
        "{{agent.name}}": agent.get_full_name() or agent.email,
        "{{agent.first_name}}": agent.first_name or agent.email.split("@")[0],
        "{{agent.email}}": agent.email,
    }

    result = macro.body
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    return result


@transaction.atomic
def apply_macro(ticket, macro, actor, request=None):
    """
    Apply a macro to a ticket: render the body as a comment, then execute
    each action in ``macro.actions`` atomically.

    Supported actions:
    - ``set_status``: transition to the named status slug
    - ``set_priority``: change ticket priority
    - ``add_tag``: append a tag to the ticket's tags list

    Returns the created Comment instance.

    Raises:
        ValueError: If the macro and ticket are in different tenants.
    """
    from django.contrib.contenttypes.models import ContentType

    from apps.comments.models import Comment

    if macro.tenant_id != ticket.tenant_id:
        raise ValueError("Cannot apply a macro from a different tenant.")

    tenant = ticket.tenant

    # 1. Render and post the comment
    rendered = render_macro(macro, ticket, actor)
    ticket_ct = ContentType.objects.get_for_model(Ticket)

    comment = Comment(
        content_type=ticket_ct,
        object_id=ticket.pk,
        author=actor,
        body=rendered,
        is_internal=False,
        tenant=tenant,
    )
    comment.save()

    # 2. Execute actions
    for action_def in macro.actions or []:
        action_type = action_def.get("action")
        value = action_def.get("value")

        if action_type == "set_status":
            from apps.tickets.models import TicketStatus

            new_status = TicketStatus.objects.filter(slug=value).first()
            if new_status and new_status.pk != ticket.status_id:
                transition_ticket_status(ticket, new_status, actor, request=request)

        elif action_type == "set_priority":
            if value and value != ticket.priority:
                change_ticket_priority(ticket, value, actor, request=request)

        elif action_type == "add_tag":
            if value:
                tags = ticket.tags or []
                if value not in tags:
                    tags.append(value)
                    ticket.tags = tags
                    ticket.save(update_fields=["tags", "updated_at"])

    # 3. Dual-write audit
    actor_name = actor.get_full_name() or actor.email
    action_summary = ", ".join(
        f"{a['action']}={a['value']}" for a in (macro.actions or [])
    )
    msg = f"Macro '{macro.name}' applied by {actor_name}"
    if action_summary:
        msg += f" (actions: {action_summary})"

    log_activity(
        tenant=tenant,
        actor=actor,
        content_object=ticket,
        action=ActivityLog.Action.UPDATED,
        description=msg,
        changes={"macro": macro.name, "actions": macro.actions or []},
        request=request,
    )
    _create_ticket_activity(
        ticket,
        actor=actor,
        event=TicketActivity.Event.COMMENTED,
        message=msg,
        metadata={"macro_id": str(macro.pk), "macro_name": macro.name},
    )

    logger.info(
        "Macro '%s' applied to Ticket #%s by %s.",
        macro.name, ticket.number, actor,
    )

    return comment


# ---------------------------------------------------------------------------
# Pipeline stage transition
# ---------------------------------------------------------------------------


@transaction.atomic
def transition_pipeline_stage(ticket, new_stage, changed_by, reason="", request=None):
    """
    Move a ticket to a new pipeline stage.

    Validates that the new stage belongs to the same pipeline as the current
    stage (or the ticket has no current stage). If the new stage is a terminal
    won/lost stage, the ticket's status is auto-transitioned to Resolved/Closed
    and the corresponding timestamp is recorded.

    Dual-writes to ActivityLog and TicketActivity.

    Args:
        ticket: The Ticket instance.
        new_stage: The target PipelineStage instance.
        changed_by: The User performing the change.
        reason: Optional reason string (for won/lost).
        request: Optional HTTP request (for IP logging).

    Returns:
        The updated Ticket instance.

    Raises:
        ValueError: If the new stage doesn't belong to the same pipeline.
    """
    from apps.tickets.models import PipelineStage, TicketActivity, TicketStatus

    old_stage = ticket.pipeline_stage
    tenant = ticket.tenant
    now = timezone.now()

    # Validate: new_stage must belong to the same pipeline as current stage
    if old_stage and old_stage.pipeline_id != new_stage.pipeline_id:
        raise ValueError(
            f"Cannot move to stage '{new_stage.name}' — it belongs to a "
            f"different pipeline than the current stage '{old_stage.name}'."
        )

    if old_stage and old_stage.pk == new_stage.pk:
        return ticket  # No change

    old_stage_name = old_stage.name if old_stage else None

    # Update pipeline stage
    ticket.pipeline_stage = new_stage
    update_fields = ["pipeline_stage", "updated_at"]

    # Won stage: set won_at + reason, auto-transition to Resolved
    if new_stage.is_won:
        ticket.won_at = now
        ticket.won_reason = reason or None
        update_fields.extend(["won_at", "won_reason"])

        resolved_status = TicketStatus.objects.filter(
            tenant=tenant, slug="resolved",
        ).first()
        if resolved_status and ticket.status_id != resolved_status.pk:
            ticket.status = resolved_status
            ticket.status_changed_at = now
            ticket.status_changed_by = changed_by
            update_fields.extend(["status", "status_changed_at", "status_changed_by"])

    # Lost stage: set lost_at + reason, auto-transition to Closed
    elif new_stage.is_lost:
        ticket.lost_at = now
        ticket.lost_reason = reason or None
        update_fields.extend(["lost_at", "lost_reason"])

        closed_status = TicketStatus.objects.filter(
            tenant=tenant, slug="closed",
        ).first()
        if closed_status and ticket.status_id != closed_status.pk:
            ticket.status = closed_status
            ticket.status_changed_at = now
            ticket.status_changed_by = changed_by
            update_fields.extend(["status", "status_changed_at", "status_changed_by"])

    ticket._skip_signal_logging = True
    ticket.save(update_fields=update_fields)

    # Dual-write logging
    timeline_msg = (
        f"Pipeline stage changed from '{old_stage_name}' to '{new_stage.name}'"
        if old_stage_name
        else f"Pipeline stage set to '{new_stage.name}'"
    )
    metadata = {
        "old_stage": old_stage_name,
        "new_stage": new_stage.name,
        "pipeline": new_stage.pipeline.name,
    }
    if reason:
        metadata["reason"] = reason

    # 1. Audit log (ActivityLog)
    log_activity(
        tenant=tenant,
        actor=changed_by,
        content_object=ticket,
        action=ActivityLog.Action.PIPELINE_STAGE_CHANGED,
        description=timeline_msg,
        changes={
            "pipeline_stage": [old_stage_name, new_stage.name],
            "pipeline": new_stage.pipeline.name,
        },
        request=request,
    )

    # 2. Ticket timeline (TicketActivity)
    _create_ticket_activity(
        ticket,
        actor=changed_by,
        event=TicketActivity.Event.PIPELINE_STAGE_CHANGED,
        message=timeline_msg,
        metadata=metadata,
    )

    logger.info(
        "Ticket #%s pipeline stage changed from '%s' to '%s' by %s.",
        ticket.number, old_stage_name, new_stage.name, changed_by,
    )

    return ticket
