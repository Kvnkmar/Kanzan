"""
Django admin configuration for the analytics app.

Registers ReportDefinition, DashboardWidget, and ExportJob with appropriate
display, search, and filter options for administrative use.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.analytics.models import DashboardWidget, ExportJob, ReportDefinition


@admin.register(ReportDefinition)
class ReportDefinitionAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "name",
        "report_type",
        "created_by",
        "tenant",
        "created_at",
    ]
    list_filter = ["report_type", "tenant"]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["created_by"]
    ordering = ["-created_at"]


@admin.register(DashboardWidget)
class DashboardWidgetAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "title",
        "widget_type",
        "data_source",
        "user",
        "tenant",
        "created_at",
    ]
    list_filter = ["widget_type", "tenant"]
    search_fields = ["title", "data_source"]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["user"]
    ordering = ["-created_at"]


@admin.register(ExportJob)
class ExportJobAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "resource_type",
        "export_type",
        "status",
        "requested_by",
        "completed_at",
        "tenant",
        "created_at",
    ]
    list_filter = ["status", "export_type", "resource_type", "tenant"]
    search_fields = ["resource_type", "requested_by__email"]
    readonly_fields = [
        "id",
        "file",
        "error_message",
        "completed_at",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["requested_by", "report"]
    ordering = ["-created_at"]
