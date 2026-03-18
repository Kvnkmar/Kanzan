"""
Django admin configuration for the tickets app.

Registers all ticket models with sensible list displays, filters, and
search fields for back-office administration.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.tickets.models import (
    CannedResponse,
    EscalationRule,
    Queue,
    SavedView,
    SLAPolicy,
    Ticket,
    TicketAssignment,
    TicketCategory,
    TicketStatus,
)


@admin.register(TicketStatus)
class TicketStatusAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["name", "slug", "color", "order", "is_closed", "is_default", "tenant"]
    list_filter = ["is_closed", "is_default", "tenant"]
    search_fields = ["name", "slug"]
    ordering = ["tenant", "order"]


@admin.register(Queue)
class QueueAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["name", "default_assignee", "auto_assign", "tenant"]
    list_filter = ["auto_assign", "tenant"]
    search_fields = ["name"]
    ordering = ["tenant", "name"]


@admin.register(TicketCategory)
class TicketCategoryAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["name", "slug", "color", "order", "is_active", "tenant"]
    list_filter = ["is_active", "tenant"]
    search_fields = ["name", "slug"]
    ordering = ["tenant", "order", "name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Ticket)
class TicketAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "number",
        "subject",
        "status",
        "priority",
        "assignee",
        "queue",
        "created_by",
        "created_at",
        "tenant",
    ]
    list_filter = ["priority", "status", "queue", "tenant"]
    search_fields = ["subject", "description", "number"]
    readonly_fields = ["number", "resolved_at", "closed_at", "created_at", "updated_at"]
    ordering = ["tenant", "-created_at"]
    raw_id_fields = ["assignee", "created_by", "contact", "company", "status", "queue"]


@admin.register(SLAPolicy)
class SLAPolicyAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "name",
        "priority",
        "first_response_minutes",
        "resolution_minutes",
        "business_hours_only",
        "is_active",
        "tenant",
    ]
    list_filter = ["priority", "is_active", "business_hours_only", "tenant"]
    search_fields = ["name"]
    ordering = ["tenant", "priority"]


@admin.register(EscalationRule)
class EscalationRuleAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "sla_policy",
        "trigger",
        "threshold_minutes",
        "action",
        "target_user",
        "order",
        "tenant",
    ]
    list_filter = ["trigger", "action", "tenant"]
    ordering = ["tenant", "sla_policy", "order"]
    raw_id_fields = ["sla_policy", "target_user", "target_role"]


@admin.register(TicketAssignment)
class TicketAssignmentAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["ticket", "assigned_to", "assigned_by", "created_at", "tenant"]
    list_filter = ["tenant"]
    search_fields = ["ticket__subject", "ticket__number"]
    readonly_fields = ["created_at"]
    ordering = ["tenant", "-created_at"]
    raw_id_fields = ["ticket", "assigned_to", "assigned_by"]


@admin.register(CannedResponse)
class CannedResponseAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["title", "category", "shortcut", "is_shared", "usage_count", "created_by", "tenant"]
    list_filter = ["category", "is_shared", "tenant"]
    search_fields = ["title", "content", "shortcut"]
    ordering = ["-usage_count"]
    raw_id_fields = ["created_by"]


@admin.register(SavedView)
class SavedViewAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["name", "resource_type", "user", "is_default", "is_pinned", "tenant"]
    list_filter = ["resource_type", "is_default", "is_pinned", "tenant"]
    search_fields = ["name"]
    ordering = ["tenant", "resource_type", "name"]
    raw_id_fields = ["user"]
