"""
Service layer for the agent-facing email inbox workflow.

Provides functions to link inbound emails to tickets and take actions
(open, assign, close) on those tickets via the inbox interface.

All mutations dual-write to both ActivityLog (audit) and TicketActivity
(ticket timeline) for full traceability.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.comments.models import ActivityLog
from apps.comments.services import log_activity
from apps.inbound_email.models import InboundEmail
from apps.tickets.models import Ticket, TicketActivity, TicketStatus
from apps.tickets.services import (
    _create_ticket_activity,
    assign_ticket,
    close_ticket,
    transition_ticket_status,
)

logger = logging.getLogger(__name__)


@transaction.atomic
def link_email_to_ticket(inbound_email, ticket_number, linked_by):
    """
    Link an inbound email to an existing ticket by ticket number.

    Args:
        inbound_email: The InboundEmail instance to link.
        ticket_number: Positive integer ticket number within the tenant.
        linked_by: The User performing the link.

    Returns:
        The Ticket instance.

    Raises:
        ValidationError: If ticket_number is invalid or ticket not found
            in the same tenant.
    """
    if not isinstance(ticket_number, int) or ticket_number <= 0:
        raise ValidationError("ticket_number must be a positive integer.")

    tenant = inbound_email.tenant
    if tenant is None:
        raise ValidationError("Email has no tenant — cannot link.")

    ticket = (
        Ticket.objects.filter(tenant=tenant, number=ticket_number)
        .select_related("status")
        .first()
    )
    if ticket is None:
        raise ValidationError(
            f"Ticket #{ticket_number} not found in your workspace"
        )

    inbound_email.linked_ticket = ticket
    inbound_email.linked_at = timezone.now()
    inbound_email.linked_by = linked_by
    inbound_email.inbox_status = InboundEmail.InboxStatus.LINKED
    inbound_email.save(update_fields=[
        "linked_ticket", "linked_at", "linked_by", "inbox_status", "updated_at",
    ])

    actor_name = linked_by.get_full_name() or str(linked_by)
    description = f"Email linked by {actor_name}"

    # Dual-write: ActivityLog (audit)
    log_activity(
        tenant=tenant,
        actor=linked_by,
        content_object=ticket,
        action=ActivityLog.Action.EMAIL_LINKED,
        description=description,
        changes={"inbound_email_id": str(inbound_email.pk)},
    )

    # Dual-write: TicketActivity (timeline)
    _create_ticket_activity(
        ticket,
        actor=linked_by,
        event=TicketActivity.Event.EMAIL_LINKED,
        message=description,
        metadata={
            "inbound_email_id": str(inbound_email.pk),
            "from_email": inbound_email.sender_email,
            "subject": inbound_email.subject,
        },
    )

    return ticket


@transaction.atomic
def action_email(inbound_email, action, actioned_by, assignee_id=None):
    """
    Take an action on a linked inbound email's ticket.

    Args:
        inbound_email: The InboundEmail instance (must be in 'linked' status).
        action: One of 'open', 'assign', 'close'.
        actioned_by: The User performing the action.
        assignee_id: UUID of the user to assign (required when action='assign').

    Raises:
        ValidationError: If preconditions are not met.
    """
    # Idempotency guard
    if inbound_email.inbox_status == InboundEmail.InboxStatus.ACTIONED:
        raise ValidationError("This email has already been actioned")

    if inbound_email.inbox_status != InboundEmail.InboxStatus.LINKED:
        raise ValidationError(
            "Email must be linked to a ticket before taking action"
        )

    if inbound_email.linked_ticket is None:
        raise ValidationError(
            "Email must be linked to a ticket before taking action"
        )

    if action not in ("open", "assign", "close"):
        raise ValidationError(f"Invalid action: {action}")

    ticket = (
        Ticket.objects.select_related("status", "tenant")
        .get(pk=inbound_email.linked_ticket_id)
    )
    tenant = ticket.tenant

    if action == "open":
        open_status = TicketStatus.objects.filter(slug="open").first()
        if open_status and ticket.status_id != open_status.pk:
            transition_ticket_status(ticket, open_status, actor=actioned_by)

    elif action == "assign":
        if assignee_id is None:
            raise ValidationError("assignee is required for 'assign' action.")
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            assignee = User.objects.get(pk=assignee_id)
        except User.DoesNotExist:
            raise ValidationError("Assignee not found.")
        assign_ticket(ticket, assignee, actor=actioned_by)

    elif action == "close":
        close_ticket(ticket, actor=actioned_by)
        # Queue CSAT survey if contact has email
        if ticket.contact and getattr(ticket.contact, "email", None):
            ticket_pk = str(ticket.pk)
            tenant_pk = str(tenant.pk)

            def _queue_csat():
                try:
                    from apps.tickets.services import _schedule_csat_survey
                    _schedule_csat_survey(ticket_pk, tenant_pk)
                except Exception:
                    logger.exception(
                        "Failed to queue CSAT for ticket #%s", ticket.number,
                    )

            transaction.on_commit(_queue_csat)

    # Mark email as actioned
    now = timezone.now()
    inbound_email.inbox_status = InboundEmail.InboxStatus.ACTIONED
    inbound_email.actioned_at = now
    inbound_email.actioned_by = actioned_by
    inbound_email.action_taken = action
    inbound_email.save(update_fields=[
        "inbox_status", "actioned_at", "actioned_by", "action_taken", "updated_at",
    ])

    actor_name = actioned_by.get_full_name() or str(actioned_by)
    description = f"Email actioned: {action} by {actor_name}"

    # Dual-write: ActivityLog (audit)
    log_activity(
        tenant=tenant,
        actor=actioned_by,
        content_object=ticket,
        action=ActivityLog.Action.EMAIL_ACTIONED,
        description=description,
        changes={
            "inbound_email_id": str(inbound_email.pk),
            "action": action,
        },
    )

    # Dual-write: TicketActivity (timeline)
    _create_ticket_activity(
        ticket,
        actor=actioned_by,
        event=TicketActivity.Event.EMAIL_ACTIONED,
        message=description,
        metadata={
            "inbound_email_id": str(inbound_email.pk),
            "action": action,
        },
    )


@transaction.atomic
def ignore_email(inbound_email, ignored_by):
    """
    Mark an inbound email as ignored. No ticket association needed.

    Writes to ActivityLog only (no TicketActivity since no ticket).

    Args:
        inbound_email: The InboundEmail instance.
        ignored_by: The User ignoring the email.

    Raises:
        ValidationError: If already actioned.
    """
    if inbound_email.inbox_status == InboundEmail.InboxStatus.ACTIONED:
        raise ValidationError("This email has already been actioned")

    if inbound_email.inbox_status == InboundEmail.InboxStatus.IGNORED:
        raise ValidationError("This email has already been ignored")

    inbound_email.inbox_status = InboundEmail.InboxStatus.IGNORED
    inbound_email.save(update_fields=["inbox_status", "updated_at"])

    tenant = inbound_email.tenant
    if tenant:
        actor_name = ignored_by.get_full_name() or str(ignored_by)
        log_activity(
            tenant=tenant,
            actor=ignored_by,
            content_object=inbound_email,
            action=ActivityLog.Action.UPDATED,
            description=f"Email ignored by {actor_name}",
            changes={"inbox_status": ["pending", "ignored"]},
        )
