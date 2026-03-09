"""
DRF serializers for comments, mentions, and activity logs.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from apps.attachments.models import Attachment
from apps.attachments.serializers import AttachmentSerializer
from apps.comments.models import ActivityLog, Comment, Mention
from apps.comments.services import parse_mentions

User = get_user_model()


class MentionSerializer(serializers.ModelSerializer):
    """Read-only serializer for mentions embedded in comment responses."""

    user_id = serializers.UUIDField(source="mentioned_user_id", read_only=True)
    user_name = serializers.SerializerMethodField()
    user_email = serializers.EmailField(
        source="mentioned_user.email", read_only=True
    )

    class Meta:
        model = Mention
        fields = ["id", "user_id", "user_name", "user_email", "created_at"]
        read_only_fields = fields

    def get_user_name(self, obj) -> str:
        return obj.mentioned_user.get_full_name()


class AuthorSerializer(serializers.Serializer):
    """Lightweight read-only serializer for embedding author info."""

    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    full_name = serializers.SerializerMethodField()
    avatar = serializers.ImageField(read_only=True)

    def get_full_name(self, obj) -> str:
        return obj.get_full_name()


class CommentSerializer(serializers.ModelSerializer):
    """
    Read serializer for comments. Includes nested author info, mentions,
    attachments, and a count of replies for threading UI.
    """

    author = AuthorSerializer(read_only=True)
    mentions = MentionSerializer(many=True, read_only=True)
    attachments = serializers.SerializerMethodField()
    reply_count = serializers.IntegerField(read_only=True)
    content_type = serializers.SlugRelatedField(
        slug_field="model",
        read_only=True,
    )
    is_reply = serializers.BooleanField(read_only=True)

    class Meta:
        model = Comment
        fields = [
            "id",
            "content_type",
            "object_id",
            "author",
            "body",
            "is_internal",
            "parent",
            "is_reply",
            "reply_count",
            "mentions",
            "attachments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_attachments(self, obj):
        ct = ContentType.objects.get_for_model(Comment)
        qs = Attachment.objects.filter(
            content_type=ct, object_id=obj.pk
        ).select_related("uploaded_by")
        return AttachmentSerializer(qs, many=True, context=self.context).data


class CommentCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating comments. Automatically parses @mentions
    from the body and creates Mention objects.

    The author is set from request.user and the tenant from request.tenant.
    """

    content_type = serializers.CharField(
        help_text="App label and model name in 'app_label.model' format (e.g. 'tickets.ticket').",
    )
    mentions = MentionSerializer(many=True, read_only=True)

    class Meta:
        model = Comment
        fields = [
            "id",
            "content_type",
            "object_id",
            "body",
            "is_internal",
            "parent",
            "mentions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "mentions", "created_at", "updated_at"]

    def validate_content_type(self, value: str) -> ContentType:
        """Resolve 'app_label.model' string to a ContentType instance."""
        try:
            app_label, model = value.strip().lower().split(".")
        except ValueError:
            raise serializers.ValidationError(
                "content_type must be in 'app_label.model' format (e.g. 'tickets.ticket')."
            )

        try:
            return ContentType.objects.get(app_label=app_label, model=model)
        except ContentType.DoesNotExist:
            raise serializers.ValidationError(
                f"Content type '{app_label}.{model}' does not exist."
            )

    def validate_parent(self, value):
        """Ensure the parent comment belongs to the same content object."""
        if value is None:
            return value

        # We'll do the full cross-check in validate() where we have all fields.
        return value

    def validate(self, attrs):
        """Cross-field validation: parent must target the same content object."""
        parent = attrs.get("parent")
        if parent is not None:
            content_type = attrs.get("content_type")
            object_id = attrs.get("object_id")

            if parent.content_type != content_type or parent.object_id != object_id:
                raise serializers.ValidationError(
                    {"parent": "Parent comment must belong to the same content object."}
                )

            # Prevent deeply nested threading (max 1 level of replies).
            if parent.parent_id is not None:
                raise serializers.ValidationError(
                    {"parent": "Replies to replies are not allowed. Reply to the top-level comment instead."}
                )

        return attrs

    def create(self, validated_data):
        """Create comment, parse mentions, and create Mention objects."""
        request = self.context["request"]
        validated_data["author"] = request.user
        validated_data["tenant"] = request.tenant

        comment = Comment.objects.create(**validated_data)

        # Parse and create mentions
        mentioned_user_ids = parse_mentions(comment.body)
        if mentioned_user_ids:
            existing_users = User.objects.filter(
                id__in=mentioned_user_ids
            ).values_list("id", flat=True)

            mentions = [
                Mention(comment=comment, mentioned_user_id=user_id)
                for user_id in existing_users
            ]
            Mention.objects.bulk_create(mentions, ignore_conflicts=True)

        return comment


class ActivityLogSerializer(serializers.ModelSerializer):
    """Read-only serializer for activity log entries."""

    actor_name = serializers.SerializerMethodField()
    actor_email = serializers.EmailField(
        source="actor.email", read_only=True, default=None
    )
    content_type = serializers.SlugRelatedField(
        slug_field="model",
        read_only=True,
    )
    action_display = serializers.CharField(
        source="get_action_display", read_only=True
    )

    class Meta:
        model = ActivityLog
        fields = [
            "id",
            "content_type",
            "object_id",
            "actor",
            "actor_name",
            "actor_email",
            "action",
            "action_display",
            "description",
            "changes",
            "ip_address",
            "created_at",
        ]
        read_only_fields = fields

    def get_actor_name(self, obj) -> str | None:
        if obj.actor is not None:
            return obj.actor.get_full_name()
        return None
