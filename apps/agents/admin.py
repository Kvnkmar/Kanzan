"""
Django admin configuration for the agents app.

Registers AgentAvailability with appropriate display, search, and filter
options for administrative use.
"""

from django.contrib import admin

from apps.agents.models import AgentAvailability


@admin.register(AgentAvailability)
class AgentAvailabilityAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "status",
        "current_ticket_count",
        "max_concurrent_tickets",
        "last_activity",
        "tenant",
        "created_at",
    ]
    list_filter = ["status", "tenant"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    readonly_fields = ["id", "current_ticket_count", "created_at", "updated_at"]
    raw_id_fields = ["user"]
    ordering = ["-last_activity"]
