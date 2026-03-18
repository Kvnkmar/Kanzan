"""
Django admin configuration for the notifications app.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.notifications.models import Notification, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = (
        "title",
        "type",
        "recipient",
        "tenant",
        "is_read",
        "created_at",
    )
    list_filter = ("type", "is_read", "created_at")
    search_fields = (
        "title",
        "body",
        "recipient__email",
        "tenant__name",
    )
    readonly_fields = ("id", "created_at", "updated_at", "read_at")
    raw_id_fields = ("recipient", "tenant")
    ordering = ("-created_at",)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "tenant",
                    "recipient",
                    "type",
                    "title",
                    "body",
                    "data",
                ),
            },
        ),
        (
            "Read Status",
            {
                "fields": ("is_read", "read_at"),
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = (
        "user",
        "tenant",
        "notification_type",
        "in_app",
        "email",
        "created_at",
    )
    list_filter = ("notification_type", "in_app", "email")
    search_fields = (
        "user__email",
        "tenant__name",
    )
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("user", "tenant")
