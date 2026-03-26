from rest_framework import serializers

from apps.inbound_email.models import InboundEmail


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
