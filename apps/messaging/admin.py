"""
Django admin configuration for the messaging app.
"""

from django.contrib import admin

from apps.messaging.models import (
    Conversation,
    ConversationParticipant,
    Message,
)


class ConversationParticipantInline(admin.TabularInline):
    model = ConversationParticipant
    extra = 0
    readonly_fields = ("id", "joined_at")
    raw_id_fields = ("user",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "name", "ticket", "tenant", "created_at", "updated_at")
    list_filter = ("type", "tenant")
    search_fields = ("name", "id")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("ticket", "tenant")
    inlines = [ConversationParticipantInline]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "tenant", "type", "name", "ticket"),
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


@admin.register(ConversationParticipant)
class ConversationParticipantAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "user", "is_muted", "last_read_at", "joined_at")
    list_filter = ("is_muted",)
    search_fields = ("user__email", "conversation__name")
    readonly_fields = ("id", "joined_at")
    raw_id_fields = ("conversation", "user")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "author", "short_body", "is_edited", "created_at")
    list_filter = ("is_edited", "tenant")
    search_fields = ("body", "author__email")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("conversation", "author", "parent", "tenant")

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "tenant", "conversation", "author", "body", "parent"),
            },
        ),
        (
            "Status",
            {
                "fields": ("is_edited",),
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

    @admin.display(description="Body")
    def short_body(self, obj):
        return obj.body[:80] if obj.body else ""
