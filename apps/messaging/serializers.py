"""
DRF serializers for the messaging app.

Provides read and write serializers for conversations, participants, and
messages with computed fields for unread counts, participant counts, and
last-message previews.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from apps.messaging.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Lightweight user representation for nested serialization
# ---------------------------------------------------------------------------


class _UserBriefSerializer(serializers.ModelSerializer):
    """Minimal user info embedded in message and participant payloads."""

    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "full_name"]
        read_only_fields = fields

    def get_full_name(self, obj):
        return obj.get_full_name()


# ---------------------------------------------------------------------------
# ConversationParticipant
# ---------------------------------------------------------------------------


class ConversationParticipantSerializer(serializers.ModelSerializer):
    """Read serializer for conversation participants."""

    user = _UserBriefSerializer(read_only=True)

    class Meta:
        model = ConversationParticipant
        fields = [
            "id",
            "user",
            "last_read_at",
            "is_muted",
            "joined_at",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


class MessageSerializer(serializers.ModelSerializer):
    """Read serializer for messages with nested author info."""

    author = _UserBriefSerializer(read_only=True)
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "author",
            "body",
            "parent",
            "is_edited",
            "reply_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_reply_count(self, obj):
        """Number of direct replies to this message."""
        # Use prefetched data if available, otherwise hit the DB
        if hasattr(obj, "_reply_count"):
            return obj._reply_count
        return obj.replies.count()


class MessageCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new message.

    The ``author`` is set from the authenticated request user in the view.
    """

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "body",
            "parent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "conversation", "created_at", "updated_at"]

    def validate_parent(self, value):
        """Ensure the parent message belongs to the same conversation."""
        if value is not None:
            conversation = self.initial_data.get(
                "conversation",
                self.context.get("conversation_id"),
            )
            if conversation and str(value.conversation_id) != str(conversation):
                raise serializers.ValidationError(
                    "Parent message must belong to the same conversation."
                )
        return value

    def validate_body(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Message body cannot be empty.")
        return value.strip()


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class ConversationSerializer(serializers.ModelSerializer):
    """
    Read serializer for conversations.

    Includes computed fields:
    - ``title``: display name (other user's name for DMs, group name, ticket ref).
    - ``participant_count``: total number of participants.
    - ``participants``: list of participant details.
    - ``last_message``: preview of the most recent message.
    - ``unread_count``: number of messages the requesting user has not read.
    """

    title = serializers.SerializerMethodField()
    participant_count = serializers.SerializerMethodField()
    participants = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "type",
            "name",
            "title",
            "ticket",
            "participant_count",
            "participants",
            "last_message",
            "unread_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_title(self, obj):
        """Compute a display-friendly title based on conversation type."""
        if obj.type == ConversationType.GROUP:
            return obj.name or "Group"

        if obj.type == ConversationType.TICKET:
            if obj.ticket:
                return f"Ticket #{obj.ticket.number}"
            return "Ticket Discussion"

        # Direct message: show the other participant's name
        if obj.type == ConversationType.DIRECT:
            request = self.context.get("request")
            if request and request.user.is_authenticated:
                # Try prefetched participant_details first
                try:
                    participants = obj.participant_details.all()
                    for p in participants:
                        if p.user_id != request.user.pk:
                            return p.user.get_full_name() or p.user.email
                except Exception:
                    pass
            return "Direct Message"

        return obj.name or "Conversation"

    def get_participants(self, obj):
        """Return brief participant info for each conversation."""
        try:
            participants = obj.participant_details.all()
            return [
                {
                    "id": str(p.user_id),
                    "full_name": p.user.get_full_name() or p.user.email,
                    "email": p.user.email,
                }
                for p in participants
            ]
        except Exception:
            return []

    def get_participant_count(self, obj):
        if hasattr(obj, "_participant_count"):
            return obj._participant_count
        return obj.participant_details.count()

    def get_last_message(self, obj):
        """Return a brief preview of the latest message, or None."""
        if hasattr(obj, "_last_message"):
            msg = obj._last_message
        else:
            msg = (
                obj.messages.select_related("author")
                .order_by("-created_at")
                .first()
            )
        if msg is None:
            return None
        return {
            "id": str(msg.pk),
            "author_name": (
                msg.author.get_full_name() if msg.author else "System"
            ),
            "body": msg.body[:200],
            "created_at": msg.created_at.isoformat(),
        }

    def get_unread_count(self, obj):
        """Count messages created after the user's last_read_at."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return 0

        # Try prefetched annotation first
        if hasattr(obj, "_unread_count"):
            return obj._unread_count

        try:
            participation = obj.participant_details.get(user=request.user)
        except ConversationParticipant.DoesNotExist:
            return 0

        if participation.last_read_at is None:
            return obj.messages.count()

        return obj.messages.filter(
            created_at__gt=participation.last_read_at
        ).count()


class ConversationCreateSerializer(serializers.Serializer):
    """
    Write serializer for creating a new conversation.

    For **direct** messages supply ``user_id`` (the other participant).
    For **group** conversations supply ``name`` and ``user_ids``.
    For **ticket** conversations supply ``ticket_id`` and optionally ``user_ids``.
    """

    type = serializers.ChoiceField(choices=ConversationType.choices)
    name = serializers.CharField(max_length=255, required=False, default="")
    user_id = serializers.UUIDField(
        required=False,
        help_text="The other user for a direct message conversation.",
    )
    user_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        help_text="Participant user IDs for group or ticket conversations.",
    )
    ticket_id = serializers.UUIDField(
        required=False,
        help_text="Ticket ID for ticket-type conversations.",
    )

    def validate(self, attrs):
        conv_type = attrs["type"]

        if conv_type == ConversationType.DIRECT:
            if not attrs.get("user_id"):
                raise serializers.ValidationError(
                    {"user_id": "Required for direct message conversations."}
                )
            request = self.context.get("request")
            if request and str(attrs["user_id"]) == str(request.user.pk):
                raise serializers.ValidationError(
                    {"user_id": "Cannot create a direct message with yourself."}
                )

        elif conv_type == ConversationType.GROUP:
            if not attrs.get("name", "").strip():
                raise serializers.ValidationError(
                    {"name": "A name is required for group conversations."}
                )
            if not attrs.get("user_ids"):
                raise serializers.ValidationError(
                    {"user_ids": "At least one other participant is required."}
                )

        elif conv_type == ConversationType.TICKET:
            if not attrs.get("ticket_id"):
                raise serializers.ValidationError(
                    {"ticket_id": "Required for ticket conversations."}
                )

        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        tenant = request.tenant
        conv_type = validated_data["type"]

        if conv_type == ConversationType.DIRECT:
            return self._create_direct(validated_data, request.user, tenant)
        elif conv_type == ConversationType.GROUP:
            return self._create_group(validated_data, request.user, tenant)
        elif conv_type == ConversationType.TICKET:
            return self._create_ticket(validated_data, request.user, tenant)

    def _create_direct(self, data, current_user, tenant):
        """
        Create or return an existing DM conversation between two users.

        DMs are unique per pair of users within a tenant -- if one already
        exists it is returned instead of creating a duplicate.
        """
        other_user_id = data["user_id"]

        # Check for an existing DM between these two users in this tenant
        existing = (
            Conversation.unscoped.filter(
                tenant=tenant,
                type=ConversationType.DIRECT,
            )
            .filter(participants=current_user)
            .filter(participants__pk=other_user_id)
            .first()
        )
        if existing:
            return existing

        conversation = Conversation(
            tenant=tenant,
            type=ConversationType.DIRECT,
        )
        conversation.save()

        ConversationParticipant.objects.bulk_create([
            ConversationParticipant(
                conversation=conversation,
                user=current_user,
            ),
            ConversationParticipant(
                conversation=conversation,
                user_id=other_user_id,
            ),
        ])

        return conversation

    def _create_group(self, data, current_user, tenant):
        """Create a named group conversation with multiple participants."""
        conversation = Conversation(
            tenant=tenant,
            type=ConversationType.GROUP,
            name=data["name"].strip(),
        )
        conversation.save()

        # Always include the creator as a participant
        participant_ids = set(data.get("user_ids", []))
        participant_ids.add(current_user.pk)

        ConversationParticipant.objects.bulk_create([
            ConversationParticipant(
                conversation=conversation,
                user_id=uid,
            )
            for uid in participant_ids
        ])

        return conversation

    def _create_ticket(self, data, current_user, tenant):
        """
        Create a conversation linked to a ticket.

        If a conversation already exists for the given ticket, return it.
        """
        from apps.tickets.models import Ticket

        ticket_id = data["ticket_id"]

        # Validate that the ticket exists and belongs to this tenant
        try:
            ticket = Ticket.unscoped.get(pk=ticket_id, tenant=tenant)
        except Ticket.DoesNotExist:
            raise serializers.ValidationError(
                {"ticket_id": "Ticket not found in this tenant."}
            )

        # Return existing ticket conversation if one exists
        existing = Conversation.unscoped.filter(
            tenant=tenant,
            type=ConversationType.TICKET,
            ticket=ticket,
        ).first()
        if existing:
            # Add the current user if not already a participant
            if not existing.participant_details.filter(user=current_user).exists():
                ConversationParticipant.objects.create(
                    conversation=existing,
                    user=current_user,
                )
            return existing

        conversation = Conversation(
            tenant=tenant,
            type=ConversationType.TICKET,
            ticket=ticket,
        )
        conversation.save()

        # Include the creator and any additional specified participants
        participant_ids = set(data.get("user_ids", []))
        participant_ids.add(current_user.pk)

        ConversationParticipant.objects.bulk_create([
            ConversationParticipant(
                conversation=conversation,
                user_id=uid,
            )
            for uid in participant_ids
        ])

        return conversation
