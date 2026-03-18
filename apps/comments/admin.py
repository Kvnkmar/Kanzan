"""
Django admin configuration for comments, mentions, and activity logs.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.comments.models import ActivityLog, Comment, Mention


class MentionInline(admin.TabularInline):
    model = Mention
    extra = 0
    readonly_fields = ["id", "mentioned_user", "created_at"]
    raw_id_fields = ["mentioned_user"]


@admin.register(Comment)
class CommentAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "id",
        "author",
        "content_type",
        "object_id",
        "is_internal",
        "parent",
        "created_at",
    ]
    list_filter = ["is_internal", "content_type", "created_at"]
    search_fields = ["body", "author__email", "author__first_name", "author__last_name"]
    raw_id_fields = ["author", "parent", "tenant"]
    readonly_fields = ["id", "created_at", "updated_at"]
    inlines = [MentionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("author", "content_type", "tenant")


@admin.register(Mention)
class MentionAdmin(admin.ModelAdmin):
    list_display = ["id", "comment", "mentioned_user", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["mentioned_user__email"]
    raw_id_fields = ["comment", "mentioned_user"]
    readonly_fields = ["id", "created_at"]


@admin.register(ActivityLog)
class ActivityLogAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "id",
        "tenant",
        "actor",
        "action",
        "content_type",
        "object_id",
        "created_at",
    ]
    list_filter = ["action", "content_type", "created_at"]
    search_fields = [
        "description",
        "actor__email",
        "actor__first_name",
        "actor__last_name",
    ]
    raw_id_fields = ["actor", "tenant"]
    readonly_fields = [
        "id",
        "tenant",
        "actor",
        "action",
        "content_type",
        "object_id",
        "description",
        "changes",
        "ip_address",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        """Activity logs are created programmatically, not via admin."""
        return False

    def has_change_permission(self, request, obj=None):
        """Activity logs are immutable."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Activity logs should not be deleted via admin."""
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("actor", "content_type", "tenant")
