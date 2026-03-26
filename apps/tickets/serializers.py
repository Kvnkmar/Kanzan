"""
DRF serializers for the tickets app.

Provides lightweight list, full detail, and validated create/update
serializers for tickets plus standard serializers for supporting models.
"""

from rest_framework import serializers

from apps.contacts.models import Contact
from apps.tickets.models import (
    CannedResponse,
    EscalationRule,
    Queue,
    SavedView,
    SLAPolicy,
    Ticket,
    TicketActivity,
    TicketAssignment,
    TicketCategory,
    TicketStatus,
)


# ---------------------------------------------------------------------------
# TicketStatus
# ---------------------------------------------------------------------------


class TicketStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketStatus
        fields = [
            "id",
            "name",
            "slug",
            "color",
            "order",
            "is_closed",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class QueueSerializer(serializers.ModelSerializer):
    class Meta:
        model = Queue
        fields = [
            "id",
            "name",
            "description",
            "default_assignee",
            "auto_assign",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# SLAPolicy
# ---------------------------------------------------------------------------


class SLAPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SLAPolicy
        fields = [
            "id",
            "name",
            "priority",
            "first_response_minutes",
            "resolution_minutes",
            "business_hours_only",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# EscalationRule
# ---------------------------------------------------------------------------


class EscalationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = EscalationRule
        fields = [
            "id",
            "sla_policy",
            "trigger",
            "threshold_minutes",
            "action",
            "target_user",
            "target_role",
            "notify_message",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# TicketCategory
# ---------------------------------------------------------------------------


class TicketCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketCategory
        fields = [
            "id",
            "name",
            "slug",
            "color",
            "order",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# TicketAssignment
# ---------------------------------------------------------------------------


class TicketAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketAssignment
        fields = [
            "id",
            "ticket",
            "assigned_to",
            "assigned_by",
            "note",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ---------------------------------------------------------------------------
# Ticket -- list (lightweight)
# ---------------------------------------------------------------------------


class TicketListSerializer(serializers.ModelSerializer):
    """Compact representation used for list endpoints and search results."""

    status_name = serializers.CharField(source="status.name", read_only=True)
    status_color = serializers.CharField(source="status.color", read_only=True)
    is_closed = serializers.BooleanField(source="status.is_closed", read_only=True)
    assignee_name = serializers.SerializerMethodField()
    assigned_by_name = serializers.SerializerMethodField()
    queue_name = serializers.CharField(source="queue.name", read_only=True, default=None)
    contact_name = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id",
            "number",
            "subject",
            "description",
            "status",
            "status_name",
            "status_color",
            "is_closed",
            "priority",
            "assignee",
            "assignee_name",
            "assigned_by",
            "assigned_by_name",
            "assigned_at",
            "queue",
            "queue_name",
            "contact",
            "contact_name",
            "category",
            "due_date",
            "sla_response_breached",
            "sla_resolution_breached",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_assignee_name(self, obj):
        if obj.assignee:
            full = f"{obj.assignee.first_name} {obj.assignee.last_name}".strip()
            return full or str(obj.assignee)
        return None

    def get_assigned_by_name(self, obj):
        if obj.assigned_by:
            full = f"{obj.assigned_by.first_name} {obj.assigned_by.last_name}".strip()
            return full or str(obj.assigned_by)
        return None

    def get_contact_name(self, obj):
        if obj.contact:
            full = f"{obj.contact.first_name} {obj.contact.last_name}".strip()
            return full or str(obj.contact)
        return None


# ---------------------------------------------------------------------------
# Contact info (lightweight, embedded in ticket detail)
# ---------------------------------------------------------------------------


class TicketContactInfoSerializer(serializers.ModelSerializer):
    """Lightweight contact info embedded in ticket detail responses."""

    full_name = serializers.SerializerMethodField()
    company_name = serializers.CharField(
        source="company.name", default=None, read_only=True
    )

    class Meta:
        model = Contact
        fields = [
            "id",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "job_title",
            "company",
            "company_name",
        ]
        read_only_fields = fields

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or str(obj)


# ---------------------------------------------------------------------------
# Ticket -- detail (full, nested)
# ---------------------------------------------------------------------------


class TicketDetailSerializer(serializers.ModelSerializer):
    """Full ticket representation with nested related objects."""

    status = TicketStatusSerializer(read_only=True)
    assignee_name = serializers.SerializerMethodField()
    assigned_by_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    contact_name = serializers.SerializerMethodField()
    contact_detail = TicketContactInfoSerializer(source="contact", read_only=True)
    queue_name = serializers.CharField(source="queue.name", default=None, read_only=True)
    assignments = TicketAssignmentSerializer(many=True, read_only=True)
    sla_status = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id",
            "number",
            "subject",
            "description",
            "status",
            "priority",
            "category",
            "queue",
            "queue_name",
            "contact",
            "contact_name",
            "contact_detail",
            "company",
            "assignee",
            "assignee_name",
            "assigned_by",
            "assigned_by_name",
            "assigned_at",
            "created_by",
            "created_by_name",
            "due_date",
            "resolved_at",
            "closed_at",
            "first_responded_at",
            "sla_response_breached",
            "sla_resolution_breached",
            "sla_status",
            "tags",
            "custom_data",
            "assignments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_assignee_name(self, obj):
        if obj.assignee:
            full = f"{obj.assignee.first_name} {obj.assignee.last_name}".strip()
            return full or str(obj.assignee)
        return None

    def get_assigned_by_name(self, obj):
        if obj.assigned_by:
            full = f"{obj.assigned_by.first_name} {obj.assigned_by.last_name}".strip()
            return full or str(obj.assigned_by)
        return None

    def get_contact_name(self, obj):
        if obj.contact:
            full = f"{obj.contact.first_name} {obj.contact.last_name}".strip()
            return full or str(obj.contact)
        return None

    def get_created_by_name(self, obj):
        if obj.created_by:
            full = f"{obj.created_by.first_name} {obj.created_by.last_name}".strip()
            return full or str(obj.created_by)
        return None

    def get_sla_status(self, obj):
        """Return SLA compliance info for the ticket's priority, or None."""
        from django.utils import timezone as tz

        from apps.tickets.models import SLAPolicy
        from apps.tickets.sla import elapsed_business_minutes

        try:
            policy = SLAPolicy.objects.filter(
                priority=obj.priority, is_active=True
            ).first()
        except Exception:
            return None

        if not policy:
            return None

        now = tz.now()
        tenant_settings = getattr(obj.tenant, "settings", None)

        result = {
            "policy_name": policy.name,
            "first_response_target_minutes": policy.first_response_minutes,
            "resolution_target_minutes": policy.resolution_minutes,
            "response_breached": obj.sla_response_breached,
            "resolution_breached": obj.sla_resolution_breached,
        }

        # Response elapsed
        resp_end = obj.first_responded_at or now
        if policy.business_hours_only and tenant_settings:
            resp_elapsed = elapsed_business_minutes(
                obj.created_at, resp_end, tenant_settings
            )
        else:
            resp_elapsed = (resp_end - obj.created_at).total_seconds() / 60
        result["response_elapsed_minutes"] = round(resp_elapsed, 1)

        # Resolution elapsed
        res_end = obj.resolved_at or now
        if policy.business_hours_only and tenant_settings:
            res_elapsed = elapsed_business_minutes(
                obj.created_at, res_end, tenant_settings
            )
        else:
            res_elapsed = (res_end - obj.created_at).total_seconds() / 60
        result["resolution_elapsed_minutes"] = round(res_elapsed, 1)

        return result


# ---------------------------------------------------------------------------
# Ticket -- create / update
# ---------------------------------------------------------------------------


class TicketCreateSerializer(serializers.ModelSerializer):
    """
    Validates and creates a new ticket.

    Ensures the chosen ``status`` belongs to the same tenant as the request
    context. If no status is supplied the tenant's default status is used.
    """

    status = serializers.PrimaryKeyRelatedField(
        queryset=TicketStatus.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Ticket
        fields = [
            "id",
            "number",
            "subject",
            "description",
            "status",
            "priority",
            "category",
            "queue",
            "contact",
            "company",
            "assignee",
            "due_date",
            "tags",
            "custom_data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "number", "created_at", "updated_at"]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_status(self, value):
        """Ensure the status belongs to the current tenant."""
        request = self.context.get("request")
        if request and hasattr(request, "tenant"):
            if value.tenant_id != request.tenant.id:
                raise serializers.ValidationError(
                    "The selected status does not belong to this tenant."
                )
        return value

    def validate_queue(self, value):
        """Ensure the queue belongs to the current tenant."""
        if value is None:
            return value
        request = self.context.get("request")
        if request and hasattr(request, "tenant"):
            if value.tenant_id != request.tenant.id:
                raise serializers.ValidationError(
                    "The selected queue does not belong to this tenant."
                )
        return value

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create(self, validated_data):
        request = self.context.get("request")

        # Set created_by from authenticated user.
        validated_data["created_by"] = request.user

        # Auto-assign to creator if no assignee specified.
        if "assignee" not in validated_data or validated_data.get("assignee") is None:
            validated_data["assignee"] = request.user

        # Fall back to tenant default status when none provided.
        if "status" not in validated_data or validated_data["status"] is None:
            default_status = TicketStatus.objects.filter(is_default=True).first()
            if default_status is None:
                raise serializers.ValidationError(
                    {"status": "No default status configured for this tenant."}
                )
            validated_data["status"] = default_status

        return super().create(validated_data)


# ---------------------------------------------------------------------------
# TicketActivity (timeline)
# ---------------------------------------------------------------------------


class TicketActivitySerializer(serializers.ModelSerializer):
    """Serializer for the ticket timeline displayed in the ticket detail UI."""

    actor_name = serializers.SerializerMethodField()
    event_display = serializers.CharField(source="get_event_display", read_only=True)

    class Meta:
        model = TicketActivity
        fields = [
            "id",
            "ticket",
            "actor",
            "actor_name",
            "event",
            "event_display",
            "message",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields

    def get_actor_name(self, obj):
        if obj.actor:
            full = f"{obj.actor.first_name} {obj.actor.last_name}".strip()
            return full or str(obj.actor)
        return "System"


# ---------------------------------------------------------------------------
# CannedResponse
# ---------------------------------------------------------------------------


class CannedResponseSerializer(serializers.ModelSerializer):
    """Serializer for canned response CRUD."""

    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CannedResponse
        fields = [
            "id",
            "title",
            "content",
            "category",
            "shortcut",
            "is_shared",
            "usage_count",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "usage_count", "created_by", "created_at", "updated_at"]

    def get_created_by_name(self, obj):
        if obj.created_by:
            full = f"{obj.created_by.first_name} {obj.created_by.last_name}".strip()
            return full or obj.created_by.email
        return None

    def validate_shortcut(self, value):
        if not value:
            return ""
        value = value.strip()
        if not value.startswith("/"):
            value = f"/{value}"
        # Check uniqueness within tenant
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        qs = CannedResponse.objects.filter(tenant=tenant, shortcut=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "This shortcut is already in use within your workspace."
            )
        return value


# ---------------------------------------------------------------------------
# SavedView
# ---------------------------------------------------------------------------


class SavedViewSerializer(serializers.ModelSerializer):
    """Serializer for saved view CRUD."""

    class Meta:
        model = SavedView
        fields = [
            "id",
            "name",
            "resource_type",
            "filters",
            "sort_field",
            "user",
            "is_default",
            "is_pinned",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]

    def validate_name(self, value):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        resource_type = self.initial_data.get(
            "resource_type",
            self.instance.resource_type if self.instance else None,
        )
        qs = SavedView.objects.filter(
            tenant=tenant, user=request.user, name=value, resource_type=resource_type,
        )
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "You already have a saved view with this name for this resource type."
            )
        return value


# ---------------------------------------------------------------------------
# Email (send from ticket, link to ticket)
# ---------------------------------------------------------------------------


class TicketSendEmailSerializer(serializers.Serializer):
    """Validates an agent's request to send an email from a ticket."""

    to = serializers.EmailField()
    subject = serializers.CharField(max_length=998)
    body = serializers.CharField()


class TicketLinkEmailSerializer(serializers.Serializer):
    """Validates a request to link an existing inbound email to a ticket."""

    email_id = serializers.UUIDField()


class TicketEmailListSerializer(serializers.Serializer):
    """Read-only serializer for emails linked to a ticket."""

    id = serializers.UUIDField()
    message_id = serializers.CharField()
    sender_email = serializers.EmailField()
    sender_name = serializers.CharField()
    recipient_email = serializers.EmailField()
    subject = serializers.CharField()
    body_text = serializers.CharField()
    direction = serializers.CharField()
    sender_type = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
