"""Helpers to publish tenant-scoped events onto the live WebSocket channel.

Server-side code (signals, services, tasks) imports
``broadcast_live_event`` and calls it with a tenant + event type +
payload.  The helper queues the broadcast for after the current
database transaction commits so that a rollback never leaks an event
that the rest of the system doesn't see.

Usage::

    from apps.tenants.live import broadcast_live_event

    broadcast_live_event(
        tenant=instance.tenant,
        event="newsfeed.created",
        payload={"id": str(instance.id), "title": instance.title},
    )

Event naming convention:
    ``<domain>.<verb>`` -- e.g. ``newsfeed.created``,
    ``reminder.completed``, ``activity.created``, ``profile.updated``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from apps.tenants.consumers import LiveEventConsumer

logger = logging.getLogger(__name__)


def broadcast_live_event(
    tenant,
    event: str,
    payload: dict[str, Any] | None = None,
    *,
    immediate: bool = False,
) -> None:
    """Push a typed event to every connected client of ``tenant``.

    The broadcast is deferred to ``transaction.on_commit`` so that a
    rolled-back transaction does not leak events to clients.  Callers
    that aren't inside a transaction (e.g. management commands) can
    pass ``immediate=True`` to skip the defer and send right away.

    ``tenant`` can be a ``Tenant`` instance or a tenant primary key /
    UUID; we coerce to a string for the group name.
    """
    if tenant is None:
        return

    tenant_pk = getattr(tenant, "pk", None) or tenant
    if not tenant_pk:
        return
    group_name = f"{LiveEventConsumer.GROUP_PREFIX}_{tenant_pk}"

    message = {
        "type":    "live_event",                                  # channel-layer routing key
        "event":   event,                                          # public event name e.g. "newsfeed.created"
        "payload": payload or {},
        "ts":      datetime.now(timezone.utc).isoformat(),
    }

    def _send() -> None:
        layer = get_channel_layer()
        if layer is None:
            logger.warning(
                "Channel layer not configured; skipping live event %s for tenant %s.",
                event, tenant_pk,
            )
            return
        try:
            async_to_sync(layer.group_send)(group_name, message)
        except Exception:
            # Live events are best-effort.  A channel-layer outage must
            # not break the user-facing write that triggered the signal.
            logger.exception(
                "Failed to broadcast live event %s to group %s.",
                event, group_name,
            )

    if immediate:
        _send()
    else:
        # If no transaction is active (e.g. autocommit), Django invokes
        # the callback immediately, so this still works for callers
        # outside an atomic block.
        transaction.on_commit(_send)
