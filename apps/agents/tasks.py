"""Celery tasks for the agents app.

``reap_stale_presence`` is the safety net behind heartbeat-driven presence: a
live ``/ws/live/`` socket re-stamps ``AgentAvailability.last_seen`` every ~25s,
but if a session dies (closed tab, network drop, crash) no further stamps
arrive. This task — run from Celery Beat every 60s — flips any agent still
marked ONLINE whose last heartbeat has aged past ``AGENT_PRESENCE_TTL_SECONDS``
to AWAY, so the Inbox Hub stops considering them assignable.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def reap_stale_presence():
    """Flip stale ONLINE agents to AWAY and broadcast the change.

    Cross-tenant by design (runs once globally). An agent is stale when their
    status is ONLINE but ``last_seen`` is null or older than the presence TTL.
    Returns the number of rows reaped (handy for tests/observability).
    """
    from apps.agents.models import AgentAvailability, AgentStatus
    from apps.tenants.live import broadcast_live_event

    ttl = getattr(settings, "AGENT_PRESENCE_TTL_SECONDS", 90)
    cutoff = timezone.now() - timedelta(seconds=ttl)

    stale = (
        AgentAvailability.unscoped
        .filter(status=AgentStatus.ONLINE)
        .filter(Q(last_seen__isnull=True) | Q(last_seen__lt=cutoff))
        .select_related("tenant", "user")
    )

    reaped = 0
    for avail in stale.iterator(chunk_size=200):
        AgentAvailability.unscoped.filter(pk=avail.pk).update(
            status=AgentStatus.AWAY,
            updated_at=timezone.now(),
        )
        # Best-effort live notice so any presence-aware UI updates immediately.
        try:
            broadcast_live_event(
                tenant=avail.tenant,
                event="agent.presence",
                payload={"user_id": str(avail.user_id), "status": AgentStatus.AWAY},
                immediate=True,
            )
        except Exception:
            logger.exception("Failed to broadcast presence reap for availability %s", avail.pk)
        reaped += 1

    if reaped:
        logger.info("reap_stale_presence: flipped %d stale ONLINE agent(s) to AWAY.", reaped)
    return reaped
