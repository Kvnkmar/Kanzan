from django.contrib import admin

from apps.inbound_email.models import InboundEmail


@admin.register(InboundEmail)
class InboundEmailAdmin(admin.ModelAdmin):
    list_display = [
        "sender_email",
        "subject",
        "recipient_email",
        "status",
        "tenant",
        "ticket",
        "created_at",
    ]
    list_filter = ["status", "tenant"]
    search_fields = ["sender_email", "subject", "message_id"]
    readonly_fields = [
        "message_id",
        "in_reply_to",
        "references",
        "raw_headers",
        "body_text",
        "body_html",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["tenant", "ticket"]
