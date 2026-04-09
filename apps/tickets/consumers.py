"""
Django Channels WebSocket consumer for ticket presence tracking.

Shows which agents are currently viewing a ticket, preventing duplicate
replies by making concurrent viewers visible to each other.
"""

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class TicketPresenceConsumer(AsyncJsonWebsocketConsumer):
    """
    Async JSON WebSocket consumer for ticket presence tracking.

    URL: ``ws/tickets/<ticket_id>/presence/``

    **Connection lifecycle:**
    - ``connect``: authenticates the user, verifies tenant membership and
      ticket access, joins the presence group, broadcasts ``agent_joined``.
    - ``disconnect``: broadcasts ``agent_left``, leaves the group.

    **Client → server:**
    - ``{"type": "heartbeat"}``: no-op to keep the connection alive.

    **Server → client (group broadcasts):**
    - ``{"type": "agent_joined", "user_id": ..., "display_name": ..., "avatar_initials": ...}``
    - ``{"type": "agent_left", "user_id": ...}``
    - ``{"type": "presence_list", "agents": [...]}``: sent to newly connected
      clients so they know who's already viewing.
    """

    async def connect(self):
        self.ticket_id = self.scope["url_route"]["kwargs"]["ticket_id"]
        self.group_name = f"ticket_{self.ticket_id}_presence"
        self.user = self.scope.get("user")

        # Reject unauthenticated connections
        if not self.user or self.user.is_anonymous:
            await self.close(code=4001)
            return

        # Verify tenant membership
        self.tenant = self.scope.get("tenant")
        if not self.tenant:
            await self.close(code=4001)
            return

        is_member = await self._is_tenant_member()
        if not is_member:
            await self.close(code=4003)
            return

        # Verify the ticket belongs to the tenant and user can access it
        has_access = await self._can_access_ticket()
        if not has_access:
            await self.close(code=4003)
            return

        # Build user display info
        self.user_id = str(self.user.pk)
        self.display_name = await self._get_display_name()
        self.avatar_initials = await self._get_initials()

        # Accept the connection and join the group
        await self.accept()
        await self.channel_layer.group_add(self.group_name, self.channel_name)

        # Broadcast agent_joined to the group (including self — this is
        # how the newly connected client learns its own presence was ack'd)
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "agent_joined",
                "user_id": self.user_id,
                "display_name": self.display_name,
                "avatar_initials": self.avatar_initials,
            },
        )

        logger.info(
            "Agent %s joined presence for ticket %s (tenant %s).",
            self.user_id,
            self.ticket_id,
            self.tenant.slug,
        )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name") and hasattr(self, "user_id"):
            # Broadcast agent_left before leaving the group
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "agent_left",
                    "user_id": self.user_id,
                },
            )
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

            logger.info(
                "Agent %s left presence for ticket %s.",
                self.user_id,
                self.ticket_id,
            )

    async def receive_json(self, content, **kwargs):
        msg_type = content.get("type", "")
        if msg_type == "heartbeat":
            return  # Keep-alive; no broadcast needed

    # ------------------------------------------------------------------
    # Group event handlers (called by channel_layer.group_send)
    # ------------------------------------------------------------------

    async def agent_joined(self, event):
        """Relay agent_joined event to the WebSocket client."""
        await self.send_json({
            "type": "agent_joined",
            "user_id": event["user_id"],
            "display_name": event["display_name"],
            "avatar_initials": event["avatar_initials"],
        })

    async def agent_left(self, event):
        """Relay agent_left event to the WebSocket client."""
        await self.send_json({
            "type": "agent_left",
            "user_id": event["user_id"],
        })

    # ------------------------------------------------------------------
    # Database helpers (sync → async bridge)
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _is_tenant_member(self):
        from apps.accounts.models import TenantMembership

        return TenantMembership.objects.filter(
            user=self.user,
            tenant=self.tenant,
            is_active=True,
        ).exists()

    @database_sync_to_async
    def _can_access_ticket(self):
        from apps.tickets.models import Ticket

        return Ticket.unscoped.filter(
            pk=self.ticket_id,
            tenant=self.tenant,
        ).exists()

    @database_sync_to_async
    def _get_display_name(self):
        full = f"{self.user.first_name} {self.user.last_name}".strip()
        return full or self.user.email

    @database_sync_to_async
    def _get_initials(self):
        first = (self.user.first_name or "")[:1].upper()
        last = (self.user.last_name or "")[:1].upper()
        return (first + last) or self.user.email[0].upper()
