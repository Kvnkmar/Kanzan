"""Tenant-wide live-event WebSocket consumer.

A single connection per browser tab that joins ``live_tenant_{tenant_id}``
and receives typed events for *all* the resources that don't already have
a dedicated consumer (news feed, CRM activities, reminders, profile and
membership changes, etc.).

Events are dispatched into the LiveBus on the client where any page can
subscribe by event type without opening its own WebSocket.

URL: ``ws/live/``

Outbound payload shape::

    {
        "type": "<domain>.<verb>",          # e.g. "newsfeed.created"
        "payload": { ... domain-specific JSON ... },
        "ts":      "2026-05-13T12:34:56Z",  # server emit time
    }

The consumer does not accept any client-to-server messages today;
inbound messages are silently ignored.
"""

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

logger = logging.getLogger(__name__)


class LiveEventConsumer(AsyncJsonWebsocketConsumer):
    """Fan-out consumer for tenant-scoped live events."""

    GROUP_PREFIX = "live_tenant"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.tenant = None
        self.group_name = None

    async def connect(self):
        self.user = self.scope.get("user")
        if self.user is None or self.user.is_anonymous:
            await self.close(code=4001)
            return

        self.tenant = self.scope.get("tenant")
        if self.tenant is None:
            await self.close(code=4001)
            return

        # Verify the user is an active member of this tenant before
        # subscribing them to the group. This prevents JWT-only clients
        # that happen to know a tenant slug from eavesdropping on its
        # event stream.
        if not await self._is_tenant_member():
            await self.close(code=4003)
            return

        self.group_name = f"{self.GROUP_PREFIX}_{self.tenant.pk}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        # A live socket is the source of truth for agent presence — stamp a
        # heartbeat now so the agent becomes assignable and any held Inbox Hub
        # backlog drains to them.
        await self._presence_heartbeat(is_connect=True)
        logger.debug(
            "Live event consumer connected: user=%s tenant=%s group=%s",
            self.user.pk, self.tenant.slug, self.group_name,
        )

    async def disconnect(self, close_code):
        # Presence is intentionally NOT cleared here: a tab refresh would
        # otherwise flap the agent offline/online. The heartbeat TTL + reaper
        # task age out a genuinely dead session within AGENT_PRESENCE_TTL_SECONDS.
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            logger.debug(
                "Live event consumer disconnected: group=%s code=%s",
                self.group_name, close_code,
            )

    async def receive_json(self, content, **kwargs):
        """The only client-to-server message is the heartbeat ping.

        live-connection.js sends ``{action: "ping"}`` every ~25s. We re-stamp
        presence and reply with a pong so the client's pong-timeout doesn't
        needlessly recycle the socket on quiet tenants.
        """
        if isinstance(content, dict) and content.get("action") == "ping":
            await self._presence_heartbeat(is_connect=False)
            await self.send_json({
                "type": "live.pong",
                "payload": {},
                "ts": timezone.now().isoformat(),
            })
        return

    async def _presence_heartbeat(self, is_connect):
        await self._do_presence_heartbeat(is_connect)

    @database_sync_to_async
    def _do_presence_heartbeat(self, is_connect):
        from apps.agents.presence import handle_live_heartbeat

        try:
            handle_live_heartbeat(self.user, self.tenant, is_connect=is_connect)
        except Exception:
            logger.exception("Live presence heartbeat failed (user=%s)", self.user.pk)

    # ------------------------------------------------------------------
    # Group event handler -- called by channel_layer.group_send
    # ------------------------------------------------------------------

    async def live_event(self, event):
        """Forward the event payload to the WebSocket client.

        The ``event`` dict is shaped::

            {
                "type":    "live_event",   # channel-layer routing key
                "event":   "<domain>.<verb>",
                "payload": {...},
                "ts":      "<iso8601>",
            }
        """
        await self.send_json({
            "type":    event.get("event") or "unknown",
            "payload": event.get("payload") or {},
            "ts":      event.get("ts"),
        })

    @database_sync_to_async
    def _is_tenant_member(self):
        from apps.accounts.models import TenantMembership

        return TenantMembership.objects.filter(
            user=self.user,
            tenant=self.tenant,
            is_active=True,
        ).exists()
