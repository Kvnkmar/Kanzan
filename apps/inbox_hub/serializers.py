"""DRF serializers for the Inbox Hub.

Phase 1A surface:
- ``HubEmailListSerializer`` — compact row for the list endpoint.
- ``HubEmailDetailSerializer`` — full HubEmail + nested inbound body.
- ``ConvertToTicketSerializer`` — payload for the explicit conversion
  action. All fields optional; defaults to the matching fields on the
  HubEmail and inbound.
- ``DismissSerializer`` — payload for dismiss (reason only).
"""

from django.contrib.auth import get_user_model
from django.utils.html import strip_tags
from django.utils.text import Truncator
from rest_framework import serializers

from apps.inbox_hub.models import (
    Department,
    HubEmail,
    HubEmailNote,
    HubEmailSLA,
    QueueRouting,
    RoutingRule,
)
from apps.tickets.models import Ticket

User = get_user_model()


# ---------------------------------------------------------------------------
# Inbound (nested, read-only) — surfaces the email body and sender on the
# HubEmail detail without forcing every consumer to chase a second API call.
# ---------------------------------------------------------------------------


class _NestedInboundSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    sender_email = serializers.CharField(read_only=True)
    sender_name = serializers.CharField(read_only=True)
    recipient_email = serializers.CharField(read_only=True)
    subject = serializers.CharField(read_only=True)
    body_text = serializers.CharField(read_only=True)
    body_html = serializers.CharField(read_only=True)
    message_id = serializers.CharField(read_only=True)
    received_at = serializers.DateTimeField(source="created_at", read_only=True)


# ---------------------------------------------------------------------------
# HubEmail
# ---------------------------------------------------------------------------


class _HubEmailBaseSerializer(serializers.ModelSerializer):
    """Common read-side fields shared by list + detail."""

    subject = serializers.CharField(source="inbound.subject", read_only=True)
    sender_email = serializers.CharField(source="inbound.sender_email", read_only=True)
    sender_name = serializers.CharField(source="inbound.sender_name", read_only=True)
    received_at = serializers.DateTimeField(source="inbound.created_at", read_only=True)
    converted_ticket_number = serializers.IntegerField(
        source="converted_ticket.number", read_only=True, allow_null=True,
    )
    assignee_name = serializers.SerializerMethodField()
    # Drives the triage cockpit's customer-context fetch straight off a list
    # row (the contact FK is already select_related'd in the viewset).
    contact_id = serializers.UUIDField(read_only=True, allow_null=True)
    snippet = serializers.SerializerMethodField()
    has_attachments = serializers.SerializerMethodField()
    attachment_count = serializers.SerializerMethodField()

    class Meta:
        model = HubEmail
        fields = (
            "id",
            "state",
            "priority",
            "category",
            "tags",
            "subject",
            "sender_email",
            "sender_name",
            "received_at",
            "snippet",
            "has_attachments",
            "attachment_count",
            "contact_id",
            "department",
            "queue",
            "assignee",
            "assignee_name",
            "converted_ticket",
            "converted_ticket_number",
            "dismissed_at",
            "dismissed_by",
            "dismissal_reason",
            # Promoted from detail → base so list rows can render the
            # wait-time / SLA badge and back the "SLA at risk" lens.
            "sla_response_due_at",
            "response_breached",
            "first_responded_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_assignee_name(self, obj):
        if obj.assignee_id and obj.assignee:
            return obj.assignee.get_full_name() or obj.assignee.email
        return ""

    def get_snippet(self, obj):
        """First ~140 chars of the email body as collapsed plain text."""
        inbound = obj.inbound
        text = (inbound.body_text or "").strip()
        if not text and inbound.body_html:
            text = strip_tags(inbound.body_html)
        text = " ".join(text.split())
        return Truncator(text).chars(140)

    def _attachment_meta(self, obj):
        meta = obj.inbound.attachment_metadata
        return meta if isinstance(meta, list) else []

    def get_has_attachments(self, obj):
        return bool(self._attachment_meta(obj))

    def get_attachment_count(self, obj):
        return len(self._attachment_meta(obj))


class HubEmailListSerializer(_HubEmailBaseSerializer):
    """Compact projection used by ``GET /hub-emails/``.

    Excludes the nested body — list pages render `subject + sender + state`;
    body content is fetched on click via the detail endpoint.
    """


class HubEmailNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = HubEmailNote
        fields = ("id", "body", "author", "author_name", "created_at")
        read_only_fields = fields

    def get_author_name(self, obj):
        if obj.author_id and obj.author:
            return obj.author.get_full_name() or obj.author.email
        return ""


class HubEmailDetailSerializer(_HubEmailBaseSerializer):
    """Full projection used by ``GET /hub-emails/{id}/``.

    Adds the nested inbound (body_text + body_html + raw headers reference)
    so the detail pane can render without a second round-trip, plus the
    triage notes, escalation state, and SLA fields the workspace needs.
    """

    inbound = _NestedInboundSerializer(read_only=True)
    notes = HubEmailNoteSerializer(many=True, read_only=True)
    # Full attachment list (with authed download URLs) so the triage cockpit can
    # render images the customer sent inline. The base serializer only exposes
    # the has/count summary used by list rows.
    attachments = serializers.SerializerMethodField()

    class Meta(_HubEmailBaseSerializer.Meta):
        # NB: sla_response_due_at / response_breached / first_responded_at are
        # already on the base serializer (promoted for the list) — only the
        # detail-exclusive SLA/escalation fields are added here.
        fields = _HubEmailBaseSerializer.Meta.fields + (
            "inbound",
            "notes",
            "attachments",
            "escalation_count",
            "escalated_to",
            "first_assigned_at",
            "sla_resolution_due_at",
            "resolution_breached",
        )
        read_only_fields = fields

    def get_attachments(self, obj):
        """Customer-sent files, with index-addressed authed download URLs
        pointing at this viewset's ``attachment`` action (see
        :func:`apps.inbound_email.attachments.serialize_attachments`)."""
        from apps.inbound_email.attachments import serialize_attachments

        return serialize_attachments(
            obj.inbound,
            lambda i: f"/api/v1/inbox-hub/hub-emails/{obj.id}/attachment/?i={i}",
        )


# ---------------------------------------------------------------------------
# Action payloads
# ---------------------------------------------------------------------------


class ConvertToTicketSerializer(serializers.Serializer):
    """Schema for ``POST /hub-emails/{id}/convert-to-ticket/``.

    Documents the full override payload the cockpit's "Convert to ticket"
    panel sends. All fields optional — blank/absent ones fall back to the
    email-derived defaults. NB: the action does **not** validate through this
    serializer; it delegates to the shared
    :func:`apps.inbound_email.ticket_overrides.build_ticket_overrides` so the
    Hub and the Emails-page form stay in lock-step (tenant-scoped FK lookups,
    closed-status rejection, active-member assignee, aware due_date). This
    class exists for the OpenAPI schema / browsable API only.
    """

    subject = serializers.CharField(required=False, max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.ChoiceField(
        choices=Ticket.Priority.choices, required=False, allow_blank=False,
    )
    category = serializers.CharField(required=False, max_length=100)
    queue = serializers.UUIDField(required=False, help_text="Queue (subcategory) pk.")
    status = serializers.UUIDField(required=False, help_text="Open TicketStatus pk.")
    assignee = serializers.UUIDField(required=False, help_text="Active member user pk.")
    due_date = serializers.DateTimeField(required=False)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False,
    )


class DismissSerializer(serializers.Serializer):
    """Payload for ``POST /hub-emails/{id}/dismiss/``.

    ``reason`` is a free-form string clipped to 255 chars by the service.
    """

    reason = serializers.CharField(required=False, allow_blank=True, default="")


class AssignSerializer(serializers.Serializer):
    """Payload for assign / reassign — ``assignee_id`` + optional reason."""

    assignee_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source="assignee",
    )
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class EscalateSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class TransitionSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=HubEmail.State.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class NoteSerializer(serializers.Serializer):
    body = serializers.CharField()


# ---------------------------------------------------------------------------
# Config / admin serializers (Department / RoutingRule / SLA / QueueRouting)
# ---------------------------------------------------------------------------


class DepartmentSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()
    member_ids = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = (
            "id", "name", "slug", "description", "lead",
            "default_queue", "business_hours", "is_active",
            "member_count", "member_ids", "created_at", "updated_at",
        )
        read_only_fields = ("id", "member_count", "member_ids", "created_at", "updated_at")

    def get_member_count(self, obj):
        return obj.memberships.count()

    def get_member_ids(self, obj):
        return [str(uid) for uid in obj.memberships.values_list("user_id", flat=True)]


class RoutingRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoutingRule
        fields = (
            "id", "name", "order", "is_active", "match",
            "department", "queue", "category", "priority", "stop_on_match",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class HubEmailSLASerializer(serializers.ModelSerializer):
    class Meta:
        model = HubEmailSLA
        fields = (
            "id", "queue", "department", "priority",
            "response_minutes", "resolution_minutes", "escalation_minutes",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        queue = attrs.get("queue")
        department = attrs.get("department")
        if not queue and not department:
            raise serializers.ValidationError(
                "Provide either a queue or a department for the SLA policy."
            )
        return attrs


class QueueRoutingSerializer(serializers.ModelSerializer):
    class Meta:
        model = QueueRouting
        fields = (
            "id", "queue", "strategy_code", "leave_unassigned_when_no_match",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
