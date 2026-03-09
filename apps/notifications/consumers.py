"""
WebSocket consumer for real-time notification delivery.

Clients connect to ``ws/notifications/`` after authenticating via the
standard Django Channels auth middleware. Each authenticated user is
added to a per-user channel group ``notifications_{user_id}`` so that
the service layer can push notifications in real time.
"""

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """
    Async JSON WebSocket consumer for notification delivery.

    Supported inbound actions:
        - ``mark_read``: marks a single notification as read.
          Payload: ``{"action": "mark_read", "notification_id": "<uuid>"}``

    Outbound events (pushed via channel layer ``group_send``):
        - ``notification.send``: forwards a notification payload to the
          connected client.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_name = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self):
        """
        Accept the connection only for authenticated users.

        Adds the user to a personal notification group so that
        ``services.send_notification`` can fan out messages via the
        channel layer.
        """
        user = self.scope.get("user")

        if user is None or user.is_anonymous:
            await self.close()
            return

        self.group_name = f"notifications_{user.id}"

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name,
        )
        await self.accept()
        logger.debug(
            "WebSocket connected for user %s (group=%s).",
            user.id,
            self.group_name,
        )

    async def disconnect(self, close_code):
        """Remove the user from the notification group on disconnect."""
        if self.group_name:
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )
            logger.debug(
                "WebSocket disconnected (group=%s, code=%s).",
                self.group_name,
                close_code,
            )

    # ------------------------------------------------------------------
    # Inbound messages from the client
    # ------------------------------------------------------------------

    async def receive_json(self, content, **kwargs):
        """
        Handle JSON messages from the WebSocket client.

        Currently supports:
            ``{"action": "mark_read", "notification_id": "..."}``
        """
        action = content.get("action")

        if action == "mark_read":
            notification_id = content.get("notification_id")
            if notification_id:
                success = await self._mark_notification_read(notification_id)
                await self.send_json(
                    {
                        "type": "mark_read_response",
                        "notification_id": str(notification_id),
                        "success": success,
                    }
                )
        else:
            await self.send_json(
                {
                    "type": "error",
                    "message": f"Unknown action: {action}",
                }
            )

    # ------------------------------------------------------------------
    # Outbound -- channel layer handler
    # ------------------------------------------------------------------

    async def notification_send(self, event):
        """
        Handler invoked by ``group_send`` from the service layer.

        Forwards the notification payload directly to the WebSocket client.
        """
        await self.send_json(event["payload"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _mark_notification_read(self, notification_id):
        """
        Mark a notification as read for the connected user.

        Returns ``True`` on success, ``False`` if the notification does
        not exist or does not belong to the user.
        """
        from apps.notifications.models import Notification

        user = self.scope["user"]
        try:
            notification = Notification.unscoped.get(
                id=notification_id,
                recipient=user,
            )
            notification.mark_read()
            return True
        except Notification.DoesNotExist:
            logger.warning(
                "Notification %s not found for user %s.",
                notification_id,
                user.id,
            )
            return False
