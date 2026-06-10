"""Agent presence tracking driven by the live WebSocket heartbeat.

Presence is derived from a *fresh* ``/ws/live/`` socket rather than a manual
dropdown, so the Inbox Hub never auto-assigns work to a dead browser session:

- on connect and on each ~25s heartbeat ping we stamp ``AgentAvailability.last_seen``
- an OFFLINE agent who opens a socket is auto-promoted to ONLINE (a deliberate
  AWAY/BUSY choice is preserved — we only ever promote *up* from OFFLINE)
- a session that stops heart-beating ages out past ``AGENT_PRESENCE_TTL_SECONDS``;
  the reaper task (:func:`apps.agents.tasks.reap_stale_presence`) then flips it
  to AWAY

Multi-tab is handled implicitly: as long as any tab heartbeats, ``last_seen``
stays fresh; the last tab closing simply stops the heartbeat and the TTL handles
it. A tab refresh re-connects well inside the TTL, so presence never thrashes —
which is why disconnect does NOT hard-set OFFLINE.

All functions here are synchronous and safe to call from a worker thread
(``database_sync_to_async``) or a Celery task.
"""

import logging

from django.conf import settings
from django.utils import timezone

from apps.agents.models import AgentAvailability, AgentStatus

logger = logging.getLogger(__name__)


def touch_presence(user, tenant):
    """Stamp a presence heartbeat for ``user`` in ``tenant``.

    Returns ``(old_status, new_status)`` when the status changed (so the caller
    can broadcast), otherwise ``None``. Cheap and idempotent — at most one row
    INSERT/UPDATE.
    """
    now = timezone.now()
    auto_online = getattr(settings, "AGENT_PRESENCE_AUTO_ONLINE", True)

    avail, created = AgentAvailability.unscoped.get_or_create(
        tenant=tenant,
        user=user,
        defaults={
            "status": AgentStatus.ONLINE if auto_online else AgentStatus.OFFLINE,
            "last_seen": now,
            "last_activity": now,
        },
    )
    if created:
        if avail.status == AgentStatus.ONLINE:
            return (AgentStatus.OFFLINE, AgentStatus.ONLINE)
        return None

    old_status = avail.status
    new_status = (
        AgentStatus.ONLINE
        if (auto_online and old_status == AgentStatus.OFFLINE)
        else old_status
    )

    AgentAvailability.unscoped.filter(pk=avail.pk).update(
        last_seen=now,
        last_activity=now,
        status=new_status,
        updated_at=now,
    )
    return (old_status, new_status) if new_status != old_status else None


def handle_live_heartbeat(user, tenant, *, is_connect=False):
    """Sync entry point for ``LiveEventConsumer``.

    Stamps presence; on a status change broadcasts ``agent.presence``; and on a
    (re)connect that leaves the agent assignable, drains any held Inbox Hub
    backlog to them. Swallows downstream errors so a presence write never tears
    down the socket.
    """
    try:
        change = touch_presence(user, tenant)
    except Exception:
        logger.exception("touch_presence failed for user=%s", getattr(user, "pk", user))
        return

    if change is not None:
        _broadcast_presence(tenant, user, change[1])

    # Drain on any (re)connect that makes the agent freshly assignable, OR when
    # presence just transitioned into ONLINE. This covers both OFFLINE->ONLINE
    # and the "was stale-ONLINE, now fresh again" case after a reconnect.
    if is_connect or change is not None:
        _maybe_drain(user, tenant)


def _maybe_drain(user, tenant):
    avail = AgentAvailability.unscoped.filter(tenant=tenant, user=user).first()
    if avail is None or not avail.is_assignable:
        return
    try:
        from apps.inbox_hub.assignment import drain_department_backlog
    except Exception:
        # inbox_hub assignment engine not available — nothing to drain.
        return
    try:
        drain_department_backlog(user, tenant)
    except Exception:
        logger.exception(
            "drain_department_backlog failed for user=%s tenant=%s",
            getattr(user, "pk", user), getattr(tenant, "pk", tenant),
        )


def _broadcast_presence(tenant, user, status):
    from apps.tenants.live import broadcast_live_event

    broadcast_live_event(
        tenant=tenant,
        event="agent.presence",
        payload={"user_id": str(user.pk), "status": status},
        immediate=True,
    )
