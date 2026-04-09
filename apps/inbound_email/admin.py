from django.contrib import admin

from apps.inbound_email.models import InboundEmail


@admin.register(InboundEmail)
class InboundEmailAdmin(admin.ModelAdmin):
    list_display = [
        "sender_email",
        "subject",
        "inbox_status",
        "linked_ticket",
        "action_taken",
        "actioned_by",
        "actioned_at",
        "recipient_email",
        "status",
        "tenant",
        "ticket",
        "created_at",
    ]
    list_filter = ["status", "inbox_status", "action_taken", "tenant"]
    search_fields = ["sender_email", "subject", "message_id", "linked_ticket__number"]
    readonly_fields = [
        "message_id",
        "in_reply_to",
        "references",
        "raw_headers",
        "body_text",
        "body_html",
        "linked_at",
        "linked_by",
        "actioned_at",
        "actioned_by",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["tenant", "ticket", "linked_ticket"]
