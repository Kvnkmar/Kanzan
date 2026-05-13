"""
DRF serializers for the agents app.

Provides serializers for AgentAvailability with both read and write
representations, including a dedicated status-update serializer and
tenant-curated CustomAgentStatus CRUD serializer.
"""

from django.utils.text import slugify
from rest_framework import serializers

from apps.agents.models import (
    BUILTIN_STATUS_SLUGS,
    AgentAvailability,
    AgentStatus,
    CustomAgentStatus,
    StatusColor,
)


class CustomAgentStatusSerializer(serializers.ModelSerializer):
    """Serializer for tenant-curated custom availability statuses."""

    color_hex = serializers.CharField(read_only=True)

    class Meta:
        model = CustomAgentStatus
        fields = [
            "id",
            "slug",
            "label",
            "description",
            "color",
            "color_hex",
            "is_active",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "color_hex"]
        extra_kwargs = {
            "slug": {"required": False, "allow_blank": True},
        }

    def validate_label(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Label is required.")
        return value

    def validate_slug(self, value):
        # Slug is optional — generated from label if blank
        return (value or "").strip().lower()

    def validate_color(self, value):
        if value not in StatusColor.values:
            raise serializers.ValidationError("Unsupported color.")
        return value

    def validate(self, attrs):
        # Auto-generate slug from label when blank
        slug = attrs.get("slug") or ""
        if not slug:
            slug = slugify(attrs.get("label", ""))[:40]
        if not slug:
            raise serializers.ValidationError({"label": "Cannot derive a slug from this label."})
        if slug in BUILTIN_STATUS_SLUGS:
            raise serializers.ValidationError(
                {"slug": f"'{slug}' is reserved by a built-in status. Pick a different name."}
            )
        attrs["slug"] = slug

        # Enforce per-tenant slug uniqueness explicitly so we get a clean field error
        tenant = getattr(self.context.get("request"), "tenant", None)
        qs = CustomAgentStatus.objects.filter(slug=slug)
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {"slug": "A status with this slug already exists."}
            )
        return attrs


class _CustomStatusReadSerializer(serializers.ModelSerializer):
    """Compact representation of CustomAgentStatus embedded in availability responses."""

    color_hex = serializers.CharField(read_only=True)

    class Meta:
        model = CustomAgentStatus
        fields = ["id", "slug", "label", "description", "color", "color_hex"]


class AgentAvailabilitySerializer(serializers.ModelSerializer):
    """Full read serializer for agent availability records."""

    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.SerializerMethodField()
    is_available = serializers.BooleanField(read_only=True)
    remaining_capacity = serializers.IntegerField(read_only=True)
    custom_status = _CustomStatusReadSerializer(read_only=True)
    effective_status_key = serializers.SerializerMethodField()
    effective_status_label = serializers.SerializerMethodField()

    class Meta:
        model = AgentAvailability
        fields = [
            "id",
            "user",
            "user_email",
            "user_name",
            "status",
            "status_message",
            "max_concurrent_tickets",
            "current_ticket_count",
            "last_activity",
            "is_available",
            "remaining_capacity",
            "working_hours",
            "auto_away_outside_hours",
            "custom_status",
            "effective_status_key",
            "effective_status_label",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "current_ticket_count",
            "last_activity",
            "created_at",
            "updated_at",
        ]

    def get_user_name(self, obj):
        return obj.user.get_full_name()

    def get_effective_status_key(self, obj):
        if obj.custom_status_id and obj.custom_status:
            return obj.custom_status.slug
        return obj.status

    def get_effective_status_label(self, obj):
        if obj.custom_status_id and obj.custom_status:
            return obj.custom_status.label
        return obj.get_status_display()


class AgentStatusUpdateSerializer(serializers.Serializer):
    """Serializer for the set_status / my-status POST actions.

    Accepts either a built-in ``status`` value or a ``custom_status_id``
    pointing at a CustomAgentStatus row owned by the current tenant.
    """

    status = serializers.ChoiceField(
        choices=AgentStatus.choices,
        required=False,
        allow_blank=False,
    )
    custom_status_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if "status" not in attrs and "custom_status_id" not in attrs:
            raise serializers.ValidationError(
                "Provide either 'status' or 'custom_status_id'."
            )
        return attrs


class AgentWorkloadSerializer(serializers.Serializer):
    """Read-only serializer for the workload action response."""

    user_id = serializers.UUIDField()
    user_email = serializers.EmailField()
    user_name = serializers.CharField()
    status = serializers.CharField()
    current_ticket_count = serializers.IntegerField()
    max_concurrent_tickets = serializers.IntegerField()
    remaining_capacity = serializers.IntegerField()
    is_available = serializers.BooleanField()
