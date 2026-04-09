from rest_framework import serializers

from apps.inbound_email.models import InboundEmail


# ---------------------------------------------------------------------------
# Agent Inbox serializers
# ---------------------------------------------------------------------------


class InboxLinkedTicketSerializer(serializers.Serializer):
    """Nested representation of the linked ticket in inbox views."""

    number = serializers.IntegerField(read_only=True)
    subject = serializers.CharField(read_only=True)


class InboxEmailListSerializer(serializers.ModelSerializer):
    """Compact list view for the agent inbox."""

    body_preview = serializers.SerializerMethodField()
    linked_ticket = InboxLinkedTicketSerializer(read_only=True)

    class Meta:
        model = InboundEmail
        fields = [
            "id",
            "sender_email",
            "subject",
            "body_preview",
            "created_at",
            "inbox_status",
            "linked_ticket",
        ]

    def get_body_preview(self, obj):
        text = obj.body_text or ""
        return text[:200]


class LinkEmailSerializer(serializers.Serializer):
    """Payload for POST /inbox/{id}/link/."""

    ticket_number = serializers.IntegerField(min_value=1)


class ActionEmailSerializer(serializers.Serializer):
    """Payload for POST /inbox/{id}/action/."""

    action = serializers.ChoiceField(choices=["open", "assign", "close"])
    assignee = serializers.UUIDField(required=False, allow_null=True)


class LinkedEmailForTicketSerializer(serializers.ModelSerializer):
    """Linked email representation nested within ticket search results."""

    actioned_by_name = serializers.SerializerMethodField()

    class Meta:
        model = InboundEmail
        fields = [
            "id",
            "sender_email",
            "subject",
            "actioned_at",
            "action_taken",
            "actioned_by",
            "actioned_by_name",
        ]

    def get_actioned_by_name(self, obj):
        if obj.actioned_by:
            return obj.actioned_by.get_full_name() or str(obj.actioned_by)
        return None


# ---------------------------------------------------------------------------
# Existing serializers (inbound email log)
# ---------------------------------------------------------------------------


class InboundEmailListSerializer(serializers.ModelSerializer):
    """Compact list view for the inbound email log."""

    ticket_number = serializers.IntegerField(
        source="ticket.number", read_only=True, default=None,
    )
    ticket_subject = serializers.CharField(
        source="ticket.subject", read_only=True, default=None,
    )

    class Meta:
        model = InboundEmail
        fields = [
            "id",
            "sender_email",
            "sender_name",
            "recipient_email",
            "subject",
            "direction",
            "sender_type",
            "status",
            "ticket",
            "ticket_number",
            "ticket_subject",
            "error_message",
            "created_at",
        ]


class InboundEmailDetailSerializer(serializers.ModelSerializer):
    """Full detail view including body and headers."""

    ticket_number = serializers.IntegerField(
        source="ticket.number", read_only=True, default=None,
    )
    ticket_subject = serializers.CharField(
        source="ticket.subject", read_only=True, default=None,
    )

    class Meta:
        model = InboundEmail
        fields = [
            "id",
            "message_id",
            "in_reply_to",
            "references",
            "sender_email",
            "sender_name",
            "recipient_email",
            "subject",
            "body_text",
            "body_html",
            "raw_headers",
            "direction",
            "sender_type",
            "status",
            "error_message",
            "ticket",
            "ticket_number",
            "ticket_subject",
            "tenant",
            "created_at",
            "updated_at",
        ]
