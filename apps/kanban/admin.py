"""
Django admin configuration for the kanban app.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.kanban.models import Board, CardPosition, Column


class ColumnInline(admin.TabularInline):
    model = Column
    extra = 0
    ordering = ["order"]
    fields = ("name", "order", "status", "wip_limit", "color")
    readonly_fields = ("id",)


@admin.register(Board)
class BoardAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ("name", "resource_type", "is_default", "tenant", "created_by", "created_at")
    list_filter = ("resource_type", "is_default")
    search_fields = ("name", "tenant__name")
    readonly_fields = ("id", "tenant", "created_at", "updated_at")
    raw_id_fields = ("created_by",)
    inlines = [ColumnInline]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "tenant", "name", "resource_type", "is_default", "created_by"),
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


@admin.register(Column)
class ColumnAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ("name", "board", "order", "status", "wip_limit", "color")
    list_filter = ("board",)
    search_fields = ("name", "board__name")
    readonly_fields = ("id", "tenant", "created_at", "updated_at")
    raw_id_fields = ("board", "status")
    ordering = ("board", "order")

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "tenant", "board", "name", "order", "status", "wip_limit", "color"),
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


@admin.register(CardPosition)
class CardPositionAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ("id", "column", "content_type", "object_id", "order", "tenant")
    list_filter = ("content_type", "column__board")
    search_fields = ("object_id",)
    readonly_fields = ("id", "tenant", "created_at", "updated_at")
    raw_id_fields = ("column", "content_type")
    ordering = ("column", "order")

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "tenant", "column", "content_type", "object_id", "order"),
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
