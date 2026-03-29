"""
Django Channels WebSocket consumer for real-time messaging.

Handles live chat within a conversation: sending messages, typing indicators,
and read-position tracking -- all scoped to authenticated participants.
"""

import logging
import time
from uuid import UUID

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

logger = logging.getLogger(__name__)

# WebSocket protection constants
MAX_MESSAGE_LENGTH = 10_000  # 10KB max message body
MAX_MESSAGES_PER_SECOND = 5
TYPING_COOLDOWN_SECONDS = 2


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """
    Async JSON WebSocket consumer for a single conversation.

    URL: ``ws/messaging/<conversation_id>/``

    **Connection lifecycle:**
    - ``connect``: authenticates the user, verifies they are a participant
      in the requested conversation, then joins the channel-layer group.
    - ``disconnect``: leaves the channel-layer group.

    **Client -> server actions** (via ``receive_json``):
    - ``send_message``: persist a new message and broadcast it.
    - ``typing``: broadcast a typing indicator to other participants.
    - ``mark_read``: update the caller's ``last_read_at`` timestamp.

    **Server -> client events** (via group broadcast):
    - ``chat_message``: a new or relayed message payload.
    - ``typing_indicator``: another user is typing.
    """

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self):
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.group_name = f"chat_{self.conversation_id}"
        self.user = self.scope.get("user")

        # Reject unauthenticated connections
        if not self.user or self.user.is_anonymous:
            await self.close(code=4001)
            return

        # Validate UUID format
        try:
            UUID(self.conversation_id)
        except (ValueError, AttributeError):
            await self.close(code=4002)
            return

        # Verify the user is a participant of this conversation
        is_participant = await self._is_participant()
        if not is_participant:
            await self.close(code=4003)
            return

        # Verify the conversation belongs to the tenant resolved from
        # the WebSocket Host header (prevents cross-tenant access).
        tenant = self.scope.get("tenant")
        if tenant is not None:
            conversation_tenant_match = await self._check_conversation_tenant(tenant)
            if not conversation_tenant_match:
                await self.close(code=4004)
                return

        # Join the channel-layer group and accept the connection
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        # Leave the channel-layer group on disconnect
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name
            )

    # ------------------------------------------------------------------
    # Client -> server
    # ------------------------------------------------------------------

    async def receive_json(self, content, **kwargs):
        """
        Dispatch incoming JSON payloads based on their ``action`` field.
        """
        # Rate limiting: track message timestamps per connection
        now = time.monotonic()
        if not hasattr(self, "_msg_timestamps"):
            self._msg_timestamps = []
            self._last_typing = 0.0

        # Prune old timestamps (older than 1 second)
        self._msg_timestamps = [t for t in self._msg_timestamps if now - t < 1.0]

        action = content.get("action")

        if action == "send_message":
            if len(self._msg_timestamps) >= MAX_MESSAGES_PER_SECOND:
                await self.send_json({"error": "Rate limit exceeded. Please slow down."})
                return
            self._msg_timestamps.append(now)
            await self._handle_send_message(content)
        elif action == "typing":
            if now - self._last_typing < TYPING_COOLDOWN_SECONDS:
                return  # Silently drop excess typing indicators
            self._last_typing = now
            await self._handle_typing()
        elif action == "mark_read":
            await self._handle_mark_read()
        else:
            await self.send_json(
                {"error": f"Unknown action: {action}"}
            )

    # ------------------------------------------------------------------
    # Server -> client (group event handlers)
    # ------------------------------------------------------------------

    async def chat_message(self, event):
        """Forward a chat_message group event to the WebSocket client."""
        await self.send_json(event["payload"])

    async def typing_indicator(self, event):
        """Forward a typing_indicator group event to the WebSocket client."""
        await self.send_json(event["payload"])

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _handle_send_message(self, content):
        """
        Persist a new message and broadcast it to the conversation group.

        Expected payload::

            {
                "action": "send_message",
                "body": "Hello world",
                "parent_id": null  // optional, for threaded replies
            }
        """
        body = content.get("body", "").strip()
        if not body:
            await self.send_json({"error": "Message body cannot be empty."})
            return
        if len(body) > MAX_MESSAGE_LENGTH:
            await self.send_json(
                {"error": f"Message too long. Maximum {MAX_MESSAGE_LENGTH} characters."}
            )
            return

        parent_id = content.get("parent_id")

        message_data = await self._create_message(body, parent_id)

        # Broadcast to the group
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "chat_message",
                "payload": {
                    "type": "chat_message",
                    "message": message_data,
                },
            },
        )

        # Send notifications to participants + process mentions (fire-and-forget)
        await self._notify_and_process_mentions(message_data["id"])

    async def _handle_typing(self):
        """
        Broadcast a typing indicator to the conversation group.

        The sender's own WebSocket will also receive the event; the frontend
        should filter it out based on the ``user_id`` field.
        """
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "typing_indicator",
                "payload": {
                    "type": "typing_indicator",
                    "user_id": str(self.user.pk),
                    "user_name": self.user.get_full_name() or str(self.user),
                    "conversation_id": self.conversation_id,
                },
            },
        )

    async def _handle_mark_read(self):
        """
        Update the calling user's ``last_read_at`` for this conversation.
        """
        await self._update_last_read()
        await self.send_json(
            {
                "type": "mark_read_ack",
                "conversation_id": self.conversation_id,
            }
        )

    # ------------------------------------------------------------------
    # Database helpers (sync -> async)
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _is_participant(self) -> bool:
        from apps.messaging.models import ConversationParticipant

        return ConversationParticipant.objects.filter(
            conversation_id=self.conversation_id,
            user=self.user,
        ).exists()

    @database_sync_to_async
    def _check_conversation_tenant(self, tenant) -> bool:
        from apps.messaging.models import Conversation

        return Conversation.unscoped.filter(
            pk=self.conversation_id,
            tenant_id=tenant.pk,
        ).exists()

    @database_sync_to_async
    def _create_message(self, body: str, parent_id: str | None) -> dict:
        from apps.messaging.models import Conversation, Message

        conversation = Conversation.unscoped.get(pk=self.conversation_id)

        parent = None
        if parent_id:
            try:
                parent = Message.unscoped.get(
                    pk=parent_id, conversation=conversation
                )
            except Message.DoesNotExist:
                parent = None

        message = Message(
            conversation=conversation,
            author=self.user,
            body=body,
            parent=parent,
            tenant=conversation.tenant,
        )
        message.save()

        # Touch the conversation's updated_at so ordering stays correct
        Conversation.unscoped.filter(pk=conversation.pk).update(
            updated_at=timezone.now()
        )

        return {
            "id": str(message.pk),
            "conversation_id": str(conversation.pk),
            "author_id": str(self.user.pk),
            "author_name": self.user.get_full_name() or str(self.user),
            "body": message.body,
            "parent_id": str(parent.pk) if parent else None,
            "is_edited": message.is_edited,
            "created_at": message.created_at.isoformat(),
        }

    @database_sync_to_async
    def _update_last_read(self):
        from apps.messaging.models import ConversationParticipant

        ConversationParticipant.objects.filter(
            conversation_id=self.conversation_id,
            user=self.user,
        ).update(last_read_at=timezone.now())

    @database_sync_to_async
    def _notify_and_process_mentions(self, message_id: str):
        from apps.messaging.mentions import (
            notify_mentions,
            notify_new_message,
            parse_mentions,
        )
        from apps.messaging.models import Message

        try:
            message = Message.unscoped.select_related(
                "author", "conversation__tenant"
            ).get(pk=message_id)
        except Message.DoesNotExist:
            return

        tenant = message.conversation.tenant

        # Populate the M2M mentions field
        user_ids = parse_mentions(message.body)
        if user_ids:
            from django.contrib.auth import get_user_model

            mentioned_users = get_user_model().objects.filter(id__in=user_ids)
            message.mentions.set(mentioned_users)

        # Dispatch mention notifications (specific @-mentions)
        notify_mentions(message, tenant)

        # Dispatch new-message notifications to all other participants
        notify_new_message(message, tenant)
