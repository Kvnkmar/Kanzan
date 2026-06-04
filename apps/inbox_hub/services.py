"""
Inbox Hub service layer.

Phase 1A scope:
- ``park_email_in_hub`` (extended from Phase 0): writes the EMAIL_RECEIVED
  ActivityLog row and broadcasts ``hub_email.created`` on commit.
- ``convert_to_ticket``: agent-driven conversion. Reuses the existing
  ``apps.inbound_email.services._create_ticket_from_email`` so the
  resulting Ticket is field-identical to the legacy auto-create path;
  then applies the agent's optional overrides (queue/status/assignee/
  priority) and writes EMAIL_CONVERTED_TO_TICKET + broadcasts.
- ``dismiss_hub_email``: writes EMAIL_DISMISSED + broadcasts.

Out of scope for Phase 1A (lands later):
- RoutingEngine / AssignmentEngine (still no queue/department/assignee
  populated at park time — handled by future ``_post_park_hooks`` body).
- assign / reassign / transition / escalate / reply services.
- Notification creation (5 ``HUB_EMAIL_*`` types).
"""

import logging

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from apps.comments.models import ActivityLog
from apps.inbound_email.models import InboundEmail
from apps.inbox_hub.models import HubEmail
from apps.tenants.live import broadcast_live_event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def park_email_in_hub(inbound, tenant, contact, system_user):
    """Create a HubEmail row for ``inbound`` and mark the source PARKED_IN_HUB.

    Idempotent — if a HubEmail already exists for this inbound (e.g. the
    Celery task retried after the InboundEmail save committed), we update
    the source status only.

    Side effects on first creation:
    - Writes an ``EMAIL_RECEIVED`` ActivityLog row pointing at the HubEmail.
    - Schedules a ``hub_email.created`` broadcast on commit.
    - Schedules ``_post_park_hooks`` on commit (Phase 1+ will fill it with
      routing + assignment + live broadcast for those steps).

    The ``system_user`` arg is captured as the ActivityLog actor — it is
    the synthetic per-tenant user the inbound pipeline uses when no human
    is involved (matches the legacy ``_create_ticket_from_email`` actor).
    """
    hub_email, created = HubEmail.objects.get_or_create(
        inbound=inbound,
        defaults={
            "tenant": tenant,
            "contact": contact,
            "state": HubEmail.State.NEW,
            "priority": HubEmail.Priority.NORMAL,
        },
    )

    inbound.status = InboundEmail.Status.PARKED_IN_HUB
    inbound.save(update_fields=["status", "updated_at"])

    if created:
        _write_activity_log(
            hub_email=hub_email,
            actor=system_user,
            action=ActivityLog.Action.EMAIL_RECEIVED,
            description=(
                f"Email from {inbound.sender_email} parked in Inbox Hub: "
                f"{inbound.subject or '(no subject)'}"
            ),
        )
        logger.info(
            "Parked inbound %s in Inbox Hub for tenant %s (hub_email=%s)",
            inbound.pk, tenant.slug, hub_email.pk,
        )
        # Defer broadcast + future routing/assignment until after commit
        # so a rolled-back outer transaction never leaks events.
        broadcast_live_event(
            tenant=tenant,
            event="hub_email.created",
            payload=_hub_email_payload(hub_email),
        )
        transaction.on_commit(lambda: _post_park_hooks(hub_email))
    else:
        logger.info(
            "Inbound %s already parked (hub_email=%s); only updated status",
            inbound.pk, hub_email.pk,
        )

    return hub_email


def convert_to_ticket(hub_email, actor, *, queue=None, status=None,
                      assignee=None, priority=None):
    """Agent-driven conversion: HubEmail → Ticket.

    Reuses ``apps.inbound_email.services._create_ticket_from_email`` so the
    resulting Ticket is field-identical to the legacy auto-create path
    (subject, description, contact, default status, tags, custom_data,
    SLA initialisation, attachment copy, confirmation email queueing).

    After the base ticket is created, applies the agent's optional
    overrides via a single ``save(update_fields=...)``. The actor becomes
    the ticket's ``created_by`` (vs. system_user in the legacy path) so
    the audit trail reflects the human decision.

    Idempotent: re-running on an already-converted HubEmail returns the
    existing ``converted_ticket`` without creating a new one.
    """
    if hub_email.state == HubEmail.State.CONVERTED_TO_TICKET and hub_email.converted_ticket_id:
        logger.info(
            "HubEmail %s already converted to ticket %s; returning existing",
            hub_email.pk, hub_email.converted_ticket_id,
        )
        return hub_email.converted_ticket

    # Late import to avoid the inbox_hub ↔ inbound_email circular at module
    # load time (inbound_email imports inbox_hub services from the seam).
    from apps.inbound_email.services import _create_ticket_from_email

    inbound = hub_email.inbound
    contact = hub_email.contact
    tenant = hub_email.tenant

    with transaction.atomic():
        ticket = _create_ticket_from_email(inbound, tenant, contact, actor)

        # Apply optional overrides from the conversion payload. We patch
        # AFTER the base creation so _maybe_auto_assign has already run
        # (its assignment, if any, gets overridden when the agent supplied
        # an assignee — the agent's intent wins).
        update_fields = []
        if queue is not None:
            ticket.queue = queue
            update_fields.append("queue")
        if status is not None:
            ticket.status = status
            update_fields.append("status")
        if assignee is not None:
            ticket.assignee = assignee
            update_fields.append("assignee")
        if priority is not None:
            ticket.priority = priority
            update_fields.append("priority")
        if update_fields:
            update_fields.append("updated_at")
            ticket.save(update_fields=update_fields)

        # Transition HubEmail.
        hub_email.state = HubEmail.State.CONVERTED_TO_TICKET
        hub_email.converted_ticket = ticket
        hub_email.save(update_fields=["state", "converted_ticket", "updated_at"])

        _write_activity_log(
            hub_email=hub_email,
            actor=actor,
            action=ActivityLog.Action.EMAIL_CONVERTED_TO_TICKET,
            description=f"Converted to ticket #{ticket.number}",
            changes={"ticket_id": str(ticket.pk), "ticket_number": ticket.number},
        )
        broadcast_live_event(
            tenant=tenant,
            event="hub_email.converted_to_ticket",
            payload={
                **_hub_email_payload(hub_email),
                "ticket_id": str(ticket.pk),
                "ticket_number": ticket.number,
            },
        )

    logger.info(
        "Converted HubEmail %s → ticket #%d (tenant=%s, actor=%s)",
        hub_email.pk, ticket.number, tenant.slug, actor.email if actor else "?",
    )
    return ticket


def dismiss_hub_email(hub_email, actor, reason=""):
    """Agent-driven dismiss. Terminal state — does NOT create a ticket.

    Use for spam, vendor newsletters, accidentally-routed mail. The
    InboundEmail row stays (audit), but the HubEmail enters DISMISSED so
    it drops out of triage lists.

    Idempotent: re-dismissing a dismissed HubEmail is a no-op.
    """
    if hub_email.state == HubEmail.State.DISMISSED:
        logger.info("HubEmail %s already dismissed; no-op", hub_email.pk)
        return hub_email

    from django.utils import timezone

    with transaction.atomic():
        hub_email.state = HubEmail.State.DISMISSED
        hub_email.dismissed_at = timezone.now()
        hub_email.dismissed_by = actor
        hub_email.dismissal_reason = (reason or "")[:255]
        hub_email.save(update_fields=[
            "state", "dismissed_at", "dismissed_by",
            "dismissal_reason", "updated_at",
        ])

        _write_activity_log(
            hub_email=hub_email,
            actor=actor,
            action=ActivityLog.Action.EMAIL_DISMISSED,
            description=f"Dismissed: {reason or '(no reason given)'}",
            changes={"reason": reason or ""},
        )
        broadcast_live_event(
            tenant=hub_email.tenant,
            event="hub_email.dismissed",
            payload=_hub_email_payload(hub_email),
        )

    logger.info(
        "Dismissed HubEmail %s (tenant=%s, actor=%s, reason=%r)",
        hub_email.pk, hub_email.tenant.slug,
        actor.email if actor else "?", reason,
    )
    return hub_email


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _post_park_hooks(hub_email):
    """Placeholder for Phase 1B/C/D: routing + assignment + SLA init.

    Lives behind transaction.on_commit so the integration shape is already
    in place when those phases land — only the body changes.
    """
    # Phase 1B+: RoutingEngine().classify_and_route(hub_email)
    # Phase 1B+: AssignmentEngine().assign(hub_email)
    # Phase 1B+: _initialize_hub_sla(hub_email)
    return


def _hub_email_payload(hub_email):
    """Minimal dict shipped over LiveBus. Clients refetch for the full row."""
    return {
        "id":         str(hub_email.pk),
        "state":      hub_email.state,
        "priority":   hub_email.priority,
        "queue_id":   str(hub_email.queue_id) if hub_email.queue_id else None,
        "assignee_id": str(hub_email.assignee_id) if hub_email.assignee_id else None,
    }


def _write_activity_log(*, hub_email, actor, action, description, changes=None):
    """Polymorphic ActivityLog row pointing at the HubEmail."""
    ct = ContentType.objects.get_for_model(HubEmail)
    ActivityLog.objects.create(
        tenant=hub_email.tenant,
        actor=actor,
        action=action,
        description=description,
        content_type=ct,
        object_id=hub_email.id,
        changes=changes or {},
    )
