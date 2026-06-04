"""DRF serializers for the Inbox Hub.

Phase 1A surface:
- ``HubEmailListSerializer`` — compact row for the list endpoint.
- ``HubEmailDetailSerializer`` — full HubEmail + nested inbound body.
- ``ConvertToTicketSerializer`` — payload for the explicit conversion
  action. All fields optional; defaults to the matching fields on the
  HubEmail and inbound.
- ``DismissSerializer`` — payload for dismiss (reason only).
"""

from rest_framework import serializers

from apps.inbox_hub.models import HubEmail
from apps.tickets.models import Queue, Ticket, TicketStatus


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
            "department",
            "queue",
            "assignee",
            "converted_ticket",
            "converted_ticket_number",
            "dismissed_at",
            "dismissed_by",
            "dismissal_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class HubEmailListSerializer(_HubEmailBaseSerializer):
    """Compact projection used by ``GET /hub-emails/``.

    Excludes the nested body — list pages render `subject + sender + state`;
    body content is fetched on click via the detail endpoint.
    """


class HubEmailDetailSerializer(_HubEmailBaseSerializer):
    """Full projection used by ``GET /hub-emails/{id}/``.

    Adds the nested inbound (body_text + body_html + raw headers reference)
    so the detail pane can render without a second round-trip.
    """

    inbound = _NestedInboundSerializer(read_only=True)

    class Meta(_HubEmailBaseSerializer.Meta):
        fields = _HubEmailBaseSerializer.Meta.fields + ("inbound",)
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Action payloads
# ---------------------------------------------------------------------------


class ConvertToTicketSerializer(serializers.Serializer):
    """Payload for ``POST /hub-emails/{id}/convert-to-ticket/``.

    All fields optional. Each override is validated against the current
    tenant (the viewset's queryset is tenant-scoped) so cross-tenant
    primary-key probing returns a 400.
    """

    queue_id = serializers.PrimaryKeyRelatedField(
        queryset=Queue.objects.all(), source="queue",
        required=False, allow_null=True,
    )
    status_id = serializers.PrimaryKeyRelatedField(
        queryset=TicketStatus.objects.all(), source="status",
        required=False, allow_null=True,
    )
    assignee_id = serializers.PrimaryKeyRelatedField(
        queryset=Ticket._meta.get_field("assignee").related_model.objects.all(),
        source="assignee", required=False, allow_null=True,
    )
    priority = serializers.ChoiceField(
        choices=Ticket.Priority.choices, required=False, allow_blank=False,
    )


class DismissSerializer(serializers.Serializer):
    """Payload for ``POST /hub-emails/{id}/dismiss/``.

    ``reason`` is a free-form string clipped to 255 chars by the service.
    """

    reason = serializers.CharField(required=False, allow_blank=True, default="")
