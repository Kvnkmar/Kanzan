"""
WebSocket consumers for VoIP real-time events.

Provides the CallEventConsumer for pushing call state updates to
connected browser softphones.
"""

import logging

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
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close()
            return

        tenant = self.scope.get("tenant")
        if not tenant:
            await self.close()
            return

        self.tenant_id = str(tenant.id)
        self.group_name = f"voip_{self.tenant_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        logger.info(
            "VoIP WebSocket connected: user=%s tenant=%s",
            user.email,
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
