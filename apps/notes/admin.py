from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.notes.models import QuickNote


@admin.register(QuickNote)
class QuickNoteAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["user", "content_preview", "color", "is_pinned", "tenant", "updated_at"]
    list_filter = ["color", "is_pinned", "tenant"]
    search_fields = ["user__email", "content"]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["user"]

    def content_preview(self, obj):
        return obj.content[:60] + "..." if len(obj.content) > 60 else obj.content
    content_preview.short_description = "Content"
