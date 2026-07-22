"""
WebSocket consumers for VoIP real-time events.

Provides the CallEventConsumer for pushing call state updates to
connected browser softphones.
"""

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


class CallEventConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for real-time VoIP call events.

    URL: ws/voip/events/

    Server → client events:
        - call_ringing: New call initiated or incoming
        - call_answered: Call was answered
        - call_ended: Call completed/missed/failed
        - call_hold: Call placed on hold
    """

    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or self.user.is_anonymous:
            await self.close(code=4001)
            return

        self.tenant = self.scope.get("tenant")
        if not self.tenant:
            await self.close(code=4001)
            return

        # ``scope["tenant"]`` is resolved from the client-supplied Host header,
        # and the session cookie is host-independent — so without this check any
        # authenticated user could join another tenant's ``voip_<id>`` group and
        # receive its live call metadata (caller/callee numbers, contact/ticket
        # ids). Require an active membership of the resolved tenant, matching the
        # other tenant-scoped consumers.
        if not await self._is_tenant_member():
            await self.close(code=4003)
            return

        self.tenant_id = str(self.tenant.id)
        self.group_name = f"voip_{self.tenant_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        logger.info(
            "VoIP WebSocket connected: user=%s tenant=%s",
            self.user.email,
            self.tenant_id,
        )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name
            )

    async def receive_json(self, content, **kwargs):
        """Handle messages from the browser (currently unused)."""
        pass

    @database_sync_to_async
    def _is_tenant_member(self):
        from apps.accounts.models import TenantMembership

        return TenantMembership.objects.filter(
            user=self.user, tenant=self.tenant, is_active=True
        ).exists()

    # ------------------------------------------------------------------
    # Group event handlers — called by channel_layer.group_send()
    # ------------------------------------------------------------------

    async def call_ringing(self, event):
        await self.send_json({
            "type": "call_ringing",
            "call": event["call"],
        })

    async def call_answered(self, event):
        await self.send_json({
            "type": "call_answered",
            "call": event["call"],
        })

    async def call_ended(self, event):
        await self.send_json({
            "type": "call_ended",
            "call": event["call"],
        })

    async def call_hold(self, event):
        await self.send_json({
            "type": "call_hold",
            "call": event["call"],
        })
