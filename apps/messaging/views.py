"""
DRF ViewSets for the messaging app.

* ``ConversationViewSet`` -- CRUD for conversations, with actions for
  managing participants and listing messages.
* ``MessageViewSet`` -- CRUD for messages within a conversation, with
  real-time broadcast on create.
"""

import logging

from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.messaging.mentions import notify_mentions, notify_new_message, parse_mentions
from apps.messaging.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
)
from apps.accounts.permissions import IsTenantMember
from apps.messaging.serializers import (
    ConversationCreateSerializer,
    ConversationParticipantSerializer,
    ConversationSerializer,
    MessageCreateSerializer,
    MessageSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ConversationViewSet
# ---------------------------------------------------------------------------


class ConversationViewSet(viewsets.ModelViewSet):
    """
    Conversation resource scoped to the current tenant and authenticated user.

    **Standard actions:**

    - ``list``: all conversations the current user participates in.
    - ``create``: start a new direct, group, or ticket conversation.
    - ``retrieve``: conversation detail with participant list.

    **Extra actions:**

    - ``add_participant``: add a user to the conversation.
    - ``remove_participant``: remove a user from the conversation.

    Messages are handled by the nested ``MessageViewSet`` route.
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantMember]
    lookup_field = "pk"

    def get_serializer_class(self):
        if self.action == "create":
            return ConversationCreateSerializer
        if self.action == "add_participant":
            return ConversationParticipantSerializer
        return ConversationSerializer

    def get_queryset(self):
        """
        Return conversations that the current user participates in,
        scoped to the current tenant via the TenantAwareManager.
        """
        if getattr(self, "swagger_fake_view", False):
            return Conversation.objects.none()
        user = self.request.user
        # Use a subquery for participant count because the M2M filter JOIN
        # would otherwise limit the COUNT to only the current user's row.
        participant_count_sq = (
            ConversationParticipant.objects.filter(conversation=OuterRef("pk"))
            .order_by()
            .values("conversation")
            .annotate(cnt=Count("id"))
            .values("cnt")
        )
        return (
            Conversation.objects.filter(participants=user)
            .annotate(_participant_count=Subquery(participant_count_sq))
            .select_related("ticket")
            .prefetch_related(
                Prefetch(
                    "participant_details",
                    queryset=ConversationParticipant.objects.select_related("user"),
                )
            )
            .distinct()
            .order_by("-updated_at")
        )

    # ------------------------------------------------------------------
    # Standard actions
    # ------------------------------------------------------------------

    def create(self, request, *args, **kwargs):
        """Create a new conversation (DM, group, or ticket-linked)."""
        serializer = self.get_serializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        conversation = serializer.save()

        # Return the full conversation representation
        output_serializer = ConversationSerializer(
            conversation, context={"request": request}
        )
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        """
        Conversation detail including the full participant list.
        """
        instance = self.get_object()
        serializer = self.get_serializer(instance, context={"request": request})
        data = serializer.data

        # Embed participant details
        participants = instance.participant_details.select_related("user").all()
        data["participants"] = ConversationParticipantSerializer(
            participants, many=True
        ).data

        return Response(data)

    def destroy(self, request, *args, **kwargs):
        """Delete a conversation and all its messages."""
        instance = self.get_object()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Extra actions
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="add-participant")
    def add_participant(self, request, pk=None):
        """
        Add a user to the conversation.

        Expected payload::

            {"user_id": "<uuid>"}
        """
        conversation = self.get_object()

        if conversation.type == ConversationType.DIRECT:
            raise ValidationError(
                {"detail": "Cannot add participants to a direct message."}
            )

        user_id = request.data.get("user_id")
        if not user_id:
            raise ValidationError({"user_id": "This field is required."})

        from django.contrib.auth import get_user_model
        from apps.accounts.models import TenantMembership

        User = get_user_model()

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise NotFound("User not found.")

        # Verify user is a member of the current tenant
        tenant = getattr(request, "tenant", None)
        if tenant and not TenantMembership.objects.filter(
            user=user, tenant=tenant, is_active=True,
        ).exists():
            raise ValidationError(
                {"detail": "User is not a member of this tenant."}
            )

        # Check if already a participant
        if conversation.participant_details.filter(user=user).exists():
            raise ValidationError(
                {"detail": "User is already a participant in this conversation."}
            )

        participant = ConversationParticipant.objects.create(
            conversation=conversation,
            user=user,
        )

        serializer = ConversationParticipantSerializer(participant)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request, pk=None):
        """
        Remove the requesting user from the conversation.

        Direct message conversations cannot be left.
        """
        conversation = self.get_object()

        if conversation.type == ConversationType.DIRECT:
            raise ValidationError(
                {"detail": "Cannot leave a direct message conversation."}
            )

        try:
            participant = conversation.participant_details.get(user=request.user)
        except ConversationParticipant.DoesNotExist:
            raise NotFound("You are not a participant in this conversation.")

        participant.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"], url_path="search-participants")
    def search_participants(self, request):
        """
        Search tenant members for use as conversation participants.

        Any authenticated tenant member can search — this does not require
        the ``user.view`` permission so that agents can message each other.

        GET /conversations/search-participants/?search=<query>
        """
        from django.contrib.auth import get_user_model

        from apps.accounts.models import TenantMembership

        User = get_user_model()
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response([], status=status.HTTP_200_OK)

        query = request.query_params.get("search", "").strip()
        member_user_ids = TenantMembership.objects.filter(
            tenant=tenant, is_active=True
        ).values_list("user_id", flat=True)
        qs = User.objects.filter(id__in=member_user_ids).exclude(id=request.user.id)

        if query:
            qs = qs.filter(
                Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
            )

        qs = qs.order_by("first_name", "last_name")[:20]

        results = [
            {
                "id": str(u.pk),
                "email": u.email,
                "full_name": u.get_full_name() or u.email,
            }
            for u in qs
        ]
        return Response({"results": results}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="remove-participant")
    def remove_participant(self, request, pk=None):
        """
        Remove a user from the conversation.

        Expected payload::

            {"user_id": "<uuid>"}
        """
        conversation = self.get_object()

        if conversation.type == ConversationType.DIRECT:
            raise ValidationError(
                {"detail": "Cannot remove participants from a direct message."}
            )

        user_id = request.data.get("user_id")
        if not user_id:
            raise ValidationError({"user_id": "This field is required."})

        try:
            participant = conversation.participant_details.get(user_id=user_id)
        except ConversationParticipant.DoesNotExist:
            raise NotFound("Participant not found in this conversation.")

        participant.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# MessageViewSet
# ---------------------------------------------------------------------------


class MessageViewSet(viewsets.ModelViewSet):
    """
    Message resource scoped to a parent conversation.

    The conversation ID is supplied via the URL
    (``conversations/<conversation_pk>/messages/``).

    - ``list``: paginated messages for the conversation.
    - ``create``: send a new message (also broadcasts via Channels).
    - ``update / partial_update``: edit own message body.
    - ``destroy``: delete own message.
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_serializer_class(self):
        if self.action in ("create",):
            return MessageCreateSerializer
        return MessageSerializer

    def _get_conversation(self) -> Conversation:
        """
        Resolve and permission-check the parent conversation from the URL.
        """
        conversation_pk = self.kwargs.get("conversation_pk")
        try:
            conversation = Conversation.objects.get(pk=conversation_pk)
        except Conversation.DoesNotExist:
            raise NotFound("Conversation not found.")

        # Ensure the requesting user is a participant
        if not conversation.participant_details.filter(
            user=self.request.user
        ).exists():
            raise PermissionDenied(
                "You are not a participant in this conversation."
            )

        return conversation

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Message.objects.none()
        conversation = self._get_conversation()
        return (
            Message.unscoped.filter(conversation=conversation)
            .select_related("author")
            .annotate(_reply_count=Count("replies"))
            .order_by("created_at")
        )

    def perform_create(self, serializer):
        """
        Save the message with the current user as author, then broadcast
        it via Channels and process mentions.
        """
        conversation = self._get_conversation()
        message = serializer.save(
            author=self.request.user,
            conversation=conversation,
            tenant=conversation.tenant,
        )

        # Touch conversation updated_at for correct ordering
        Conversation.unscoped.filter(pk=conversation.pk).update(
            updated_at=timezone.now()
        )

        # Populate M2M mentions
        user_ids = parse_mentions(message.body)
        if user_ids:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            mentioned_users = User.objects.filter(id__in=user_ids)
            message.mentions.set(mentioned_users)

        # Dispatch mention notifications
        notify_mentions(message, conversation.tenant)

        # Dispatch new-message notifications to all other participants
        notify_new_message(message, conversation.tenant)

        # Broadcast via Channels (best-effort)
        self._broadcast_message(message)

    def perform_update(self, serializer):
        """Only the author may edit their own message."""
        message = self.get_object()
        if message.author_id != self.request.user.pk:
            raise PermissionDenied("You can only edit your own messages.")

        serializer.save(is_edited=True)

    def perform_destroy(self, instance):
        """Only the author may delete their own message."""
        if instance.author_id != self.request.user.pk:
            raise PermissionDenied("You can only delete your own messages.")
        instance.delete()

    @staticmethod
    def _broadcast_message(message):
        """
        Best-effort broadcast of a new message to the conversation's
        Channels group. Failures are logged but do not block the HTTP
        response.
        """
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            if channel_layer is None:
                return

            group_name = f"chat_{message.conversation_id}"
            payload = {
                "type": "chat_message",
                "payload": {
                    "type": "chat_message",
                    "message": {
                        "id": str(message.pk),
                        "conversation_id": str(message.conversation_id),
                        "author_id": (
                            str(message.author_id) if message.author_id else None
                        ),
                        "author_name": (
                            message.author.get_full_name()
                            if message.author
                            else "System"
                        ),
                        "body": message.body,
                        "parent_id": (
                            str(message.parent_id) if message.parent_id else None
                        ),
                        "is_edited": message.is_edited,
                        "created_at": message.created_at.isoformat(),
                    },
                },
            }
            async_to_sync(channel_layer.group_send)(group_name, payload)
        except Exception:
            logger.exception(
                "Failed to broadcast message %s to Channels group.", message.pk
            )
