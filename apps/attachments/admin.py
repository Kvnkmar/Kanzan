"""
Django admin configuration for file attachments.
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.attachments.models import Attachment


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "original_name",
        "mime_type",
        "size_display",
        "content_type",
        "object_id",
        "uploaded_by",
        "tenant",
        "created_at",
    ]
    list_filter = ["mime_type", "content_type", "created_at"]
    search_fields = [
        "original_name",
        "uploaded_by__email",
        "uploaded_by__first_name",
        "uploaded_by__last_name",
    ]
    raw_id_fields = ["uploaded_by", "tenant"]
    readonly_fields = [
        "id",
        "file_link",
        "original_name",
        "mime_type",
        "size_bytes",
        "size_display",
        "uploaded_by",
        "tenant",
        "content_type",
        "object_id",
        "created_at",
        "updated_at",
    ]

    def file_link(self, obj):
        if obj.file:
            return format_html(
                '<a href="{}" target="_blank">{}</a>',
                obj.file.url,
                obj.original_name,
            )
        return "-"
    file_link.short_description = "File"

    def size_display(self, obj):
        return obj.size_display
    size_display.short_description = "Size"

    def has_change_permission(self, request, obj=None):
        """Attachments are immutable once uploaded."""
        return False

    def get_queryset(self, request):
        return Attachment.unscoped.select_related(
            "uploaded_by", "content_type", "tenant"
        )
