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


def _get_client_ip(request):
    """Extract client IP for audit log."""
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


# ---------------------------------------------------------------------------
# Ticket creation
# ---------------------------------------------------------------------------


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
    TicketActivity.objects.create(
        tenant=tenant,
        ticket=ticket,
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
    auto_status_msg = None
    if ticket.status and ticket.status.is_default and not ticket.status.is_closed:
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

    # Track first agent response (assignment counts as a response)
    record_first_response(ticket, actor)

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
    TicketActivity.objects.create(
        tenant=tenant,
        ticket=ticket,
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
        TicketActivity.objects.create(
            tenant=tenant,
            ticket=ticket,
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
# Status change
# ---------------------------------------------------------------------------


@transaction.atomic
def change_ticket_status(ticket, new_status, actor, request=None):
    """
    Change a ticket's status and write both log entries.

    Handles closed/reopened detection: if the new status has ``is_closed=True``,
    records a CLOSED event; if moving from closed to open, records REOPENED.

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
    new_status_name = new_status.name
    tenant = ticket.tenant

    if old_status and old_status.pk == new_status.pk:
        return ticket  # No change

    # 1. Update the ticket (pre_save signal handles timestamps)
    ticket.status = new_status
    ticket.save(update_fields=["status", "updated_at"])

    # Determine the timeline event type
    was_closed = old_status.is_closed if old_status else False
    now_closed = new_status.is_closed

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
    TicketActivity.objects.create(
        tenant=tenant,
        ticket=ticket,
        actor=actor,
        event=timeline_event,
        message=timeline_msg,
        metadata={
            "old_status": old_status_name,
            "new_status": new_status_name,
        },
    )

    return ticket


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

    return change_ticket_status(ticket, closed_status, actor, request=request)


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
    TicketActivity.objects.create(
        tenant=tenant,
        ticket=ticket,
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

    TicketActivity.objects.create(
        tenant=ticket.tenant,
        ticket=ticket,
        actor=actor,
        event=event,
        message=f"Added a {label}",
    )


# ---------------------------------------------------------------------------
# First response tracking
# ---------------------------------------------------------------------------


def record_first_response(ticket, actor):
    """
    Stamp ``first_responded_at`` on the ticket if not already set.

    Only counts if *actor* is NOT the original ticket creator (i.e. an agent
    is responding, not the requester adding to their own ticket).
    """
    if ticket.first_responded_at is not None:
        return
    if actor and actor.pk == ticket.created_by_id:
        return

    ticket.first_responded_at = timezone.now()
    ticket.save(update_fields=["first_responded_at", "updated_at"])
