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
from django.utils import timezone

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
                      assignee=None, priority=None, subject=None,
                      description=None, category=None, due_date=None, tags=None):
    """Agent-driven conversion: HubEmail → Ticket.

    Reuses ``apps.inbound_email.services._create_ticket_from_email`` so the
    resulting Ticket is field-identical to the legacy auto-create path
    (contact, default status, custom_data, SLA initialisation, attachment copy,
    confirmation email queueing).

    Optional overrides let callers build a "proper" ticket — refined subject, a
    written description, priority/queue/status/assignee/category/tags/due_date —
    instead of accepting the raw email-derived defaults. They are folded into
    the initial ticket creation (not patched afterwards) so SLA windows match
    the final priority, status-lifecycle timestamps are correct, and the audit
    trail stays clean. The actor becomes the ticket's ``created_by`` (vs.
    system_user in the legacy path) so the audit trail reflects the human
    decision.

    Idempotent: re-running on an already-converted HubEmail returns the
    existing ``converted_ticket`` without creating a new one.

    Raises ``ValueError`` when the email is in a terminal state the state
    machine forbids converting from (i.e. DISMISSED).
    """
    from apps.inbox_hub.state_machine import assert_transition

    # Late import to avoid the inbox_hub ↔ inbound_email circular at module
    # load time (inbound_email imports inbox_hub services from the seam).
    from apps.inbound_email.services import _create_ticket_from_email

    inbound = hub_email.inbound
    contact = hub_email.contact
    tenant = hub_email.tenant

    # Drop unset keys so the email-derived defaults survive inside
    # _create_ticket_from_email; pre-resolved instances pass straight through.
    overrides = {
        "subject": subject,
        "description": description,
        "priority": priority,
        "status": status,
        "queue": queue,
        "assignee": assignee,
        "category": category,
        "due_date": due_date,
        "tags": tags,
    }
    overrides = {key: value for key, value in overrides.items() if value is not None}

    with transaction.atomic():
        # Re-read the lifecycle fields under a row lock so concurrent
        # convert/dismiss requests serialize — asserting on the caller's
        # unlocked in-memory state would let two racing terminal actions
        # both pass the guard (reassign_hub_email locks the same way).
        locked = (
            HubEmail.unscoped.select_for_update()
            .values("state", "converted_ticket_id", "first_responded_at")
            .get(pk=hub_email.pk)
        )
        hub_email.state = locked["state"]
        hub_email.converted_ticket_id = locked["converted_ticket_id"]
        hub_email.first_responded_at = locked["first_responded_at"]

        if hub_email.state == HubEmail.State.CONVERTED_TO_TICKET and hub_email.converted_ticket_id:
            logger.info(
                "HubEmail %s already converted to ticket %s; returning existing",
                hub_email.pk, hub_email.converted_ticket_id,
            )
            return hub_email.converted_ticket

        # The state machine is authoritative for terminal states: converting a
        # DISMISSED email is illegal (ALLOWED_TRANSITIONS[DISMISSED] is empty).
        # A CONVERTED row whose ticket has since been deleted (converted_ticket
        # is SET_NULL) is deliberately allowed back through as a recovery path,
        # so only assert when the row isn't already CONVERTED.
        if hub_email.state != HubEmail.State.CONVERTED_TO_TICKET:
            assert_transition(hub_email.state, HubEmail.State.CONVERTED_TO_TICKET)

        ticket = _create_ticket_from_email(
            inbound, tenant, contact, actor, overrides=overrides,
        )

        # Transition HubEmail. Converting IS the triage response — stamp
        # first_responded_at (when unset) so the response SLA reads as met
        # and reporting can measure time-to-triage on converted mail.
        hub_email.state = HubEmail.State.CONVERTED_TO_TICKET
        hub_email.converted_ticket = ticket
        update_fields = ["state", "converted_ticket", "updated_at"]
        if hub_email.first_responded_at is None:
            hub_email.first_responded_at = timezone.now()
            update_fields.append("first_responded_at")
        hub_email.save(update_fields=update_fields)

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

    Raises ``ValueError`` when the email is in a terminal state the state
    machine forbids dismissing from (i.e. CONVERTED_TO_TICKET).
    """
    from apps.inbox_hub.state_machine import assert_transition

    with transaction.atomic():
        # Row-lock + re-read so a concurrent convert can't slip past the
        # terminal guard (see convert_to_ticket for the race this closes).
        locked_state = (
            HubEmail.unscoped.select_for_update()
            .values_list("state", flat=True)
            .get(pk=hub_email.pk)
        )
        hub_email.state = locked_state

        if locked_state == HubEmail.State.DISMISSED:
            logger.info("HubEmail %s already dismissed; no-op", hub_email.pk)
            return hub_email

        # Terminal guard: a CONVERTED email is done — the state machine has no
        # outgoing edges from a terminal state, so dismissing it is illegal.
        assert_transition(locked_state, HubEmail.State.DISMISSED)

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


def transition_hub_email(hub_email, new_state, actor=None, *, note=""):
    """Validate + apply a lifecycle state change.

    Raises ``ValueError`` (from the state machine) on an illegal transition.
    Writes a STATUS_CHANGED audit row and broadcasts ``hub_email.transitioned``.
    """
    from django.utils import timezone

    from apps.inbox_hub.state_machine import assert_transition

    # A "first response" = the agent first moves the email into an active
    # working state. Stamping first_responded_at marks the response SLA as met
    # (check_hub_sla_breaches treats first_responded_at IS NOT NULL as
    # "responded"), so an email an agent is actually working stops
    # false-breaching + auto-escalating on every deadline.
    RESPONSE_STATES = {
        HubEmail.State.IN_PROGRESS,
        HubEmail.State.PENDING_AGENT,
        HubEmail.State.AWAITING_CUSTOMER,
        HubEmail.State.RESOLVED,
    }

    old_state = hub_email.state
    assert_transition(old_state, new_state)

    with transaction.atomic():
        hub_email.state = new_state
        update_fields = ["state", "updated_at"]
        if new_state in RESPONSE_STATES and hub_email.first_responded_at is None:
            hub_email.first_responded_at = timezone.now()
            update_fields.append("first_responded_at")
        hub_email.save(update_fields=update_fields)
        _write_activity_log(
            hub_email=hub_email,
            actor=actor,
            action=ActivityLog.Action.STATUS_CHANGED,
            description=f"State {old_state} → {new_state}" + (f": {note}" if note else ""),
            changes={"from": old_state, "to": new_state},
        )
        broadcast_live_event(
            tenant=hub_email.tenant,
            event="hub_email.transitioned",
            payload=_hub_email_payload(hub_email),
        )
    return hub_email


def escalate_hub_email(hub_email, actor=None, *, reason=""):
    """Escalate to the department lead.

    Only acts on a genuine lifecycle transition into ESCALATED: increments
    ``escalation_count``, sets ``escalated_to`` (department lead), moves the
    state, writes EMAIL_ESCALATED, broadcasts ``hub_email.escalated`` and
    notifies the lead. Anything else — terminal/resolved rows AND rows that
    are already ESCALATED — is a silent no-op, so neither the 120s SLA sweep
    nor a repeated manual escalate can inflate the counter or re-nudge the
    lead without an actual state change.
    """
    from apps.inbox_hub.state_machine import can_transition

    tenant = hub_email.tenant
    target = hub_email.department.lead if hub_email.department_id else None

    with transaction.atomic():
        old_state = hub_email.state
        if not can_transition(old_state, HubEmail.State.ESCALATED):
            return hub_email

        hub_email.escalation_count = (hub_email.escalation_count or 0) + 1
        hub_email.state = HubEmail.State.ESCALATED
        update_fields = ["escalation_count", "state", "updated_at"]
        if target is not None:
            hub_email.escalated_to = target
            update_fields.append("escalated_to")
        hub_email.save(update_fields=update_fields)

        _write_activity_log(
            hub_email=hub_email,
            actor=actor,
            action=ActivityLog.Action.EMAIL_ESCALATED,
            description="Escalated" + (f": {reason}" if reason else ""),
            changes={
                "escalated_to": str(target.pk) if target else None,
                "count": hub_email.escalation_count,
            },
        )
        broadcast_live_event(
            tenant=tenant,
            event="hub_email.escalated",
            payload=_hub_email_payload(hub_email),
        )
        if target is not None:
            transaction.on_commit(
                lambda: _notify_escalation(tenant, hub_email, target, reason)
            )
    return hub_email


def reassign_hub_email(hub_email, new_user, actor=None, *, reason=""):
    """Manually move an email to ``new_user`` (override; online not required)."""
    from django.db.models import F

    from apps.agents.models import AgentAvailability
    from apps.inbox_hub.models import HubEmailAssignment

    tenant = hub_email.tenant
    now = timezone.now()

    with transaction.atomic():
        he = HubEmail.unscoped.select_for_update().get(pk=hub_email.pk)

        # Terminal emails are done. Reassigning one would re-hand a closed item
        # to an agent's inbox and corrupt load counters -- reject it.
        if he.state in (
            HubEmail.State.CONVERTED_TO_TICKET,
            HubEmail.State.DISMISSED,
        ):
            raise ValueError(
                f"Cannot reassign an email in terminal state '{he.state}'."
            )

        old_user_id = he.assignee_id
        same_user = old_user_id == new_user.pk
        he.assignee = new_user
        update_fields = ["assignee", "first_assigned_at", "state", "updated_at"]
        if he.first_assigned_at is None:
            he.first_assigned_at = now
        if he.state == HubEmail.State.NEW:
            he.state = HubEmail.State.ASSIGNED
        # The RESPONDER acting counts as the first response: claim/self-assign
        # is the agent saying "I've picked this up" (the cockpit never calls
        # ``transition``), so without this stamp every self-triaged email
        # false-breaches its response SLA and auto-escalates. A manager merely
        # ROUTING mail to someone else must NOT stamp — that would disarm the
        # sat-on-mail breach + lead escalation for manually-routed mail while
        # identical auto-assigned mail (no actor) still breaches. Engine
        # auto-assignment (AssignmentEngine.assign_to) is a separate code path
        # and never reaches this function.
        if (
            actor is not None
            and actor.pk == new_user.pk
            and he.first_responded_at is None
        ):
            he.first_responded_at = now
            update_fields.append("first_responded_at")
        he.save(update_fields=update_fields)

        # Hand the ORIGINAL customer email to the agent's personal email
        # inbox so they can read it and act on it (create ticket / reply)
        # once it has left the Hub. Triage happens in the Hub; the actual
        # work happens in the agent's email section + the ticket system.
        from apps.inbound_email.models import InboundEmail

        inbound = he.inbound
        if inbound is not None:
            inbound.assignee = new_user
            inbound.inbox_status = InboundEmail.InboxStatus.PENDING
            inbound.is_read = False
            inbound.save(update_fields=[
                "assignee", "inbox_status", "is_read", "updated_at",
            ])

        is_reassign = old_user_id is not None and old_user_id != new_user.pk
        HubEmailAssignment.unscoped.create(
            tenant=tenant,
            hub_email=he,
            assigned_to=new_user,
            assigned_by=actor,
            reason=(
                HubEmailAssignment.Reason.REASSIGNMENT if is_reassign
                else HubEmailAssignment.Reason.MANUAL
            ),
            note=reason,
        )

        # Only adjust load counters on a GENUINE change of assignee. Claiming
        # or re-assigning to the SAME agent must not inflate their count (the
        # old code did +1 unconditionally with the -1 gated on a different
        # user, so a same-user re-claim leaked +1 every time).
        if not same_user:
            AgentAvailability.unscoped.filter(tenant=tenant, user=new_user).update(
                current_ticket_count=F("current_ticket_count") + 1, last_activity=now,
            )
            if old_user_id:
                AgentAvailability.unscoped.filter(
                    tenant=tenant, user_id=old_user_id, current_ticket_count__gt=0,
                ).update(current_ticket_count=F("current_ticket_count") - 1)
        else:
            AgentAvailability.unscoped.filter(tenant=tenant, user=new_user).update(
                last_activity=now,
            )

        _write_activity_log(
            hub_email=he,
            actor=actor,
            action=(
                ActivityLog.Action.EMAIL_REASSIGNED if is_reassign
                else ActivityLog.Action.EMAIL_AGENT_ASSIGNED
            ),
            description=f"{'Reassigned' if is_reassign else 'Assigned'} to "
                        f"{new_user.get_full_name() or new_user.email}"
                        + (f": {reason}" if reason else ""),
            changes={"assignee": str(new_user.pk), "from": str(old_user_id) if old_user_id else None},
        )
        broadcast_live_event(
            tenant=tenant,
            event="hub_email.reassigned" if is_reassign else "hub_email.assigned",
            payload=_hub_email_payload(he),
        )
        transaction.on_commit(
            lambda: _notify_reassignment(tenant, he, new_user, is_reassign)
        )
    return he


def add_hub_email_note(hub_email, author, body):
    """Append an internal triage note to a HubEmail."""
    from apps.inbox_hub.models import HubEmailNote

    note = HubEmailNote.unscoped.create(
        tenant=hub_email.tenant, hub_email=hub_email, author=author, body=body,
    )
    broadcast_live_event(
        tenant=hub_email.tenant,
        event="hub_email.transitioned",
        payload=_hub_email_payload(hub_email),
    )
    return note


def _notify_escalation(tenant, hub_email, target, reason):
    from apps.notifications.models import NotificationType
    from apps.notifications.services import send_notification

    try:
        subject = hub_email.inbound.subject or ""
    except Exception:
        subject = ""
    try:
        send_notification(
            tenant=tenant,
            recipient=target,
            notification_type=NotificationType.HUB_EMAIL_ESCALATED_TO_ME,
            title="Email escalated to you",
            body=(reason or subject or "(no subject)"),
            data={"hub_email_id": str(hub_email.pk), "url": "/emails/"},
        )
    except Exception:
        logger.exception("Failed to send escalation notification for %s", hub_email.pk)


def _notify_reassignment(tenant, hub_email, user, is_reassign):
    from apps.notifications.models import NotificationType
    from apps.notifications.services import send_notification

    try:
        subject = hub_email.inbound.subject or ""
    except Exception:
        subject = ""
    n_type = (
        NotificationType.HUB_EMAIL_REASSIGNED if is_reassign
        else NotificationType.HUB_EMAIL_ASSIGNED
    )
    try:
        send_notification(
            tenant=tenant,
            recipient=user,
            notification_type=n_type,
            title="Email reassigned to you" if is_reassign else "Email assigned to you",
            body=subject or "(no subject)",
            data={"hub_email_id": str(hub_email.pk), "url": "/inbox/"},
        )
    except Exception:
        logger.exception("Failed to send reassignment notification for %s", hub_email.pk)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _post_park_hooks(hub_email):
    """Route → assign → init SLA for a freshly parked email.

    Runs after commit (scheduled from :func:`park_email_in_hub`). Each step is
    isolated so a downstream failure never breaks the inbound-email pipeline
    that produced this HubEmail.

    1. RoutingEngine — resolve department/queue/category/priority.
    2. AssignmentEngine — auto-assign to an online department agent (when the
       tenant has ``inbox_hub_auto_assign`` on); held if none online.
    3. SLA init — seed response/resolution deadlines from the matching policy.
    """
    tenant = hub_email.tenant

    # 1. Routing
    try:
        from apps.inbox_hub.routing import RoutingEngine
        RoutingEngine.classify_and_route(hub_email)
    except Exception:
        logger.exception("Inbox Hub routing failed for HubEmail %s", hub_email.pk)

    # 2. SLA init (before assignment so deadlines exist on the assigned row).
    try:
        _initialize_hub_sla(hub_email)
    except Exception:
        logger.exception("Inbox Hub SLA init failed for HubEmail %s", hub_email.pk)

    # 3. Auto-assignment (respect the per-tenant toggle).
    settings_obj = getattr(tenant, "settings", None)
    auto_assign = settings_obj is None or settings_obj.inbox_hub_auto_assign
    if auto_assign:
        try:
            from apps.inbox_hub.assignment import AssignmentEngine
            AssignmentEngine.try_assign(hub_email)
        except Exception:
            logger.exception("Inbox Hub auto-assign failed for HubEmail %s", hub_email.pk)


def _initialize_hub_sla(hub_email):
    """Seed ``sla_response_due_at`` / ``sla_resolution_due_at`` from policy.

    Resolution order mirrors the model docstring: queue+priority →
    department+priority. No-op when no matching HubEmailSLA exists. Uses simple
    wall-clock deadlines for MVP (business-hours-aware deadlines can layer on
    later via apps.tickets.sla helpers).
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.inbox_hub.models import HubEmailSLA

    tenant = hub_email.tenant
    policy = None
    if hub_email.queue_id:
        policy = HubEmailSLA.unscoped.filter(
            tenant=tenant, queue_id=hub_email.queue_id, priority=hub_email.priority,
        ).first()
    if policy is None and hub_email.department_id:
        policy = HubEmailSLA.unscoped.filter(
            tenant=tenant, department_id=hub_email.department_id, priority=hub_email.priority,
        ).first()
    if policy is None:
        return

    now = timezone.now()
    hub_email.sla_response_due_at = now + timedelta(minutes=policy.response_minutes)
    hub_email.sla_resolution_due_at = now + timedelta(minutes=policy.resolution_minutes)
    hub_email.save(update_fields=["sla_response_due_at", "sla_resolution_due_at", "updated_at"])


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
