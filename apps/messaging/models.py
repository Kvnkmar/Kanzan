"""
Messaging models for the multi-tenant CRM platform.

Provides real-time conversations (direct messages, group chats, ticket
discussions) with threaded replies and mention support.
"""

import uuid

from django.conf import settings
from django.db import models

from main.models import TenantScopedModel


class ConversationType(models.TextChoices):
    DIRECT = "direct", "Direct Message"
    GROUP = "group", "Group Chat"
    TICKET = "ticket", "Ticket Discussion"


class Conversation(TenantScopedModel):
    """
    A conversation between two or more users within a tenant.

    Supports three types:
    - **direct**: one-to-one private message between two users.
    - **group**: named group channel with multiple participants.
    - **ticket**: discussion thread attached to a support ticket.
    """

    type = models.CharField(
        max_length=20,
        choices=ConversationType.choices,
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Display name for group conversations.",
    )
    ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="Associated ticket for ticket-type conversations.",
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="ConversationParticipant",
        related_name="conversations",
    )

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "conversation"
        verbose_name_plural = "conversations"

    def __str__(self):
        if self.name:
            return self.name
        if self.type == ConversationType.TICKET and self.ticket_id:
            return f"Ticket discussion ({self.ticket_id})"
        return f"Conversation {self.pk}"


class ConversationParticipant(models.Model):
    """
    Through model linking a user to a conversation with per-user metadata
    such as mute state and read-position tracking.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="participant_details",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_participations",
    )
    last_read_at = models.DateTimeField(null=True, blank=True)
    is_muted = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("conversation", "user")]
        ordering = ["joined_at"]
        verbose_name = "conversation participant"
        verbose_name_plural = "conversation participants"

    def __str__(self):
        return f"{self.user} in {self.conversation}"


class Message(TenantScopedModel):
    """
    A single message within a conversation.

    Supports threaded replies via ``parent``, @-mentions via the ``mentions``
    M2M, and distinguishes system-generated messages by a null ``author``.
    """

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
        help_text="Null for system-generated messages.",
    )
    body = models.TextField()
    mentions = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="mentioned_in_messages",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
        help_text="Parent message for threaded replies.",
    )
    is_edited = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["conversation", "created_at"],
                name="msg_conv_created_idx",
            ),
        ]
        verbose_name = "message"
        verbose_name_plural = "messages"

    def __str__(self):
        author_label = self.author or "System"
        preview = self.body[:50] if self.body else ""
        return f"{author_label}: {preview}"
