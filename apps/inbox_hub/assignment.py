"""Inbox Hub assignment engine.

Picks an **online** agent in a HubEmail's department and assigns the email to
them using a load + fairness strategy chain. "Online" means *truly* online: a
fresh ``/ws/live/`` heartbeat (see :pyattr:`AgentAvailability.is_assignable`) —
a stale or manually-away session is never chosen.

Behaviour:
- :func:`AssignmentEngine.try_assign` — selection path used at park time. Picks
  the best assignable department agent; if none is online the email is *held*
  (stays NEW, unassigned) for later pickup.
- :func:`drain_department_backlog` — called when an agent comes online; assigns
  the oldest held emails in their department(s) to them, up to their capacity.
- :func:`assign_to` — targeted assignment of a NEW/held email to a specific
  user (used by drain and the manual assign/claim API).

Strategy chain comes from ``QueueRouting.strategy_code`` (pipe-delimited),
default ``availability_aware|least_loaded|round_robin``. Each token contributes
one sort key, applied left-to-right as tie-breakers.
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max, Q
from django.db.models.expressions import F
from django.utils import timezone

from apps.agents.models import AgentAvailability
from apps.comments.models import ActivityLog
from apps.inbox_hub.models import (
    DepartmentMembership,
    HubEmail,
    HubEmailAssignment,
    QueueRouting,
)
from apps.notifications.models import NotificationType
from apps.notifications.services import send_notification
from apps.tenants.live import broadcast_live_event

logger = logging.getLogger(__name__)
User = get_user_model()

# States where an assignee is still "carrying" the email — used for load.
ACTIVE_STATES = [
    HubEmail.State.NEW,
    HubEmail.State.ASSIGNED,
    HubEmail.State.IN_PROGRESS,
    HubEmail.State.PENDING_AGENT,
    HubEmail.State.AWAITING_CUSTOMER,
    HubEmail.State.ESCALATED,
]

AGENT_ROLE_LEVEL = 30
DEFAULT_STRATEGY = "availability_aware|least_loaded|round_robin"


class AssignmentEngine:
    """Stateless selection + assignment façade."""

    @staticmethod
    def try_assign(hub_email, *, actor=None, notify_on_hold=True):
        """Select an online department agent and assign, or hold.

        Returns the assigned ``User``, or ``None`` when no agent is online
        (the email is left NEW/unassigned — "held").
        """
        tenant = hub_email.tenant
        user_ids, department = _candidate_user_ids(hub_email)
        chosen = _select_agent(tenant, user_ids, hub_email)

        if chosen is None:
            logger.info(
                "AssignmentEngine: no online agent for HubEmail %s (dept=%s) — holding.",
                hub_email.pk, getattr(department, "slug", None),
            )
            if notify_on_hold:
                _notify_hold(hub_email, department)
            return None

        return assign_to(
            hub_email, chosen, reason=HubEmailAssignment.Reason.AUTO,
            actor=actor, require_online=True,
        )


def assign_to(hub_email, user, *, reason, actor=None, require_online=False):
    """Assign a NEW/held ``hub_email`` to ``user`` atomically.

    Locks the email row (and the agent's availability row when capacity must be
    enforced) to avoid double-assignment under concurrency. Returns ``user`` on
    success or ``None`` if the email was already taken or the agent is no longer
    assignable.
    """
    from apps.inbox_hub.services import _hub_email_payload, _write_activity_log

    tenant = hub_email.tenant
    now = timezone.now()

    with transaction.atomic():
        he = HubEmail.unscoped.select_for_update().get(pk=hub_email.pk)
        if he.assignee_id is not None or he.state != HubEmail.State.NEW:
            # Already claimed by a concurrent assignment.
            return None

        locked_avail = (
            AgentAvailability.unscoped
            .select_for_update()
            .filter(tenant=tenant, user=user)
            .first()
        )
        if require_online and (locked_avail is None or not locked_avail.is_assignable):
            return None

        he.assignee = user
        if he.first_assigned_at is None:
            he.first_assigned_at = now
        he.state = HubEmail.State.ASSIGNED  # NEW -> ASSIGNED (always valid)
        he.save(update_fields=["assignee", "first_assigned_at", "state", "updated_at"])

        HubEmailAssignment.unscoped.create(
            tenant=tenant,
            hub_email=he,
            assigned_to=user,
            assigned_by=actor,
            reason=reason,
            note="",
        )

        if locked_avail is not None:
            AgentAvailability.unscoped.filter(pk=locked_avail.pk).update(
                current_ticket_count=F("current_ticket_count") + 1,
                last_activity=now,
                updated_at=now,
            )

        _write_activity_log(
            hub_email=he,
            actor=actor,
            action=ActivityLog.Action.EMAIL_AGENT_ASSIGNED,
            description=f"Assigned to {user.get_full_name() or user.email}",
            changes={"assignee": str(user.pk), "reason": reason},
        )

        payload = _hub_email_payload(he)
        payload["department_id"] = str(he.department_id) if he.department_id else None
        broadcast_live_event(tenant=tenant, event="hub_email.assigned", payload=payload)
        transaction.on_commit(lambda: _notify_assignment(tenant, he, user, actor))

    logger.info("AssignmentEngine: HubEmail %s assigned to %s (%s).", he.pk, user.email, reason)
    return user


def drain_department_backlog(user, tenant):
    """Assign oldest held emails in ``user``'s department(s) to ``user``.

    Called when an agent comes online (from the presence hook). Respects the
    tenant auto-assign toggle and the agent's remaining capacity, so a single
    returning agent is never flooded. Returns the number assigned.
    """
    settings_obj = getattr(tenant, "settings", None)
    if settings_obj is not None and not settings_obj.inbox_hub_auto_assign:
        return 0

    avail = AgentAvailability.unscoped.filter(tenant=tenant, user=user).first()
    if avail is None or not avail.is_assignable:
        return 0
    remaining = avail.remaining_capacity
    if remaining <= 0:
        return 0

    dept_ids = list(
        DepartmentMembership.unscoped
        .filter(tenant=tenant, user=user)
        .values_list("department_id", flat=True)
    )
    if dept_ids:
        held_q = Q(department_id__in=dept_ids) | Q(department__isnull=True)
    else:
        held_q = Q(department__isnull=True)

    held = list(
        HubEmail.unscoped
        .filter(tenant=tenant, state=HubEmail.State.NEW, assignee__isnull=True)
        .filter(held_q)
        .order_by("created_at")[:remaining]
    )

    assigned = 0
    for he in held:
        if assign_to(
            he, user, reason=HubEmailAssignment.Reason.AUTO, require_online=True,
        ) is not None:
            assigned += 1

    if assigned:
        logger.info("drain_department_backlog: assigned %d held email(s) to %s.", assigned, user.email)
    return assigned


# ---------------------------------------------------------------------------
# Selection internals
# ---------------------------------------------------------------------------


def _candidate_user_ids(hub_email):
    """Return ``(user_ids, department)`` for the email's candidate pool."""
    tenant = hub_email.tenant
    department = hub_email.department
    if department is None and hub_email.queue_id and hub_email.queue.department_id:
        department = hub_email.queue.department

    if department is not None:
        ids = list(
            DepartmentMembership.unscoped
            .filter(tenant=tenant, department=department)
            .values_list("user_id", flat=True)
        )
        return ids, department

    # No department resolved — fall back to tenant agents (level == 30),
    # mirroring the legacy pick_email_agent pool.
    from apps.accounts.models import TenantMembership

    ids = list(
        TenantMembership.objects
        .filter(tenant=tenant, is_active=True, role__hierarchy_level=AGENT_ROLE_LEVEL)
        .values_list("user_id", flat=True)
    )
    return ids, None


def _select_agent(tenant, user_ids, hub_email):
    """Pick the best assignable agent from ``user_ids`` per the strategy chain."""
    if not user_ids:
        return None

    avails = list(
        AgentAvailability.unscoped.filter(tenant=tenant, user_id__in=user_ids)
        .select_related("user")
    )
    # Hard gate: only agents with a fresh heartbeat + spare capacity.
    candidates = [a for a in avails if a.is_assignable]
    if not candidates:
        return None

    cand_ids = [a.user_id for a in candidates]

    # Batch the load + fairness metrics so selection is two queries, not N.
    hub_loads = dict(
        HubEmail.unscoped
        .filter(tenant=tenant, assignee_id__in=cand_ids, state__in=ACTIVE_STATES)
        .values_list("assignee_id")
        .annotate(c=Count("id"))
        .values_list("assignee_id", "c")
    )
    last_assigned = dict(
        HubEmailAssignment.unscoped
        .filter(tenant=tenant, assigned_to_id__in=cand_ids)
        .values_list("assigned_to_id")
        .annotate(m=Max("created_at"))
        .values_list("assigned_to_id", "m")
    )

    strategies = _strategy_chain(tenant, hub_email)

    def sort_key(a):
        load = hub_loads.get(a.user_id, 0) + a.current_ticket_count
        last = last_assigned.get(a.user_id)
        last_ts = last.timestamp() if last else 0.0
        parts = []
        for s in strategies:
            if s == "availability_aware":
                parts.append(-a.remaining_capacity)
            elif s == "least_loaded":
                parts.append(load)
            elif s == "round_robin":
                parts.append(last_ts)
            # unknown strategy tokens are ignored
        parts.append(str(a.user_id))  # deterministic final tie-break
        return tuple(parts)

    candidates.sort(key=sort_key)
    return candidates[0].user


def _strategy_chain(tenant, hub_email):
    code = DEFAULT_STRATEGY
    if hub_email.queue_id:
        routing = QueueRouting.unscoped.filter(tenant=tenant, queue_id=hub_email.queue_id).first()
        if routing and routing.strategy_code:
            code = routing.strategy_code
    return [tok.strip() for tok in code.split("|") if tok.strip()]


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def _notify_assignment(tenant, hub_email, user, actor):
    try:
        subject = ""
        try:
            subject = hub_email.inbound.subject or ""
        except Exception:
            pass
        send_notification(
            tenant=tenant,
            recipient=user,
            notification_type=NotificationType.HUB_EMAIL_ASSIGNED,
            title="New email assigned to you",
            body=subject or "(no subject)",
            data={"hub_email_id": str(hub_email.pk), "url": "/emails/"},
        )
    except Exception:
        logger.exception("Failed to send HUB_EMAIL_ASSIGNED notification for %s", hub_email.pk)


def _notify_hold(hub_email, department):
    """Held email: surface it to the department lead so it isn't missed.

    The sidebar badge already bumps for every member on park (hub_email.created),
    so this is a targeted nudge to the person responsible rather than a tenant
    broadcast. No dedicated 'held' NotificationType exists yet — we reuse the
    assignment type addressed to the lead with a clarifying title.
    """
    lead = getattr(department, "lead", None) if department else None
    if lead is None:
        return
    try:
        subject = hub_email.inbound.subject or ""
    except Exception:
        subject = ""
    try:
        send_notification(
            tenant=hub_email.tenant,
            recipient=lead,
            notification_type=NotificationType.HUB_EMAIL_ASSIGNED,
            title="Unassigned email waiting (no agent online)",
            body=subject or "(no subject)",
            data={"hub_email_id": str(hub_email.pk), "url": "/emails/", "held": True},
        )
    except Exception:
        logger.exception("Failed to send hold notification for %s", hub_email.pk)
