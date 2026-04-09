from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.crm.models import Activity, Reminder


@admin.register(Activity)
class ActivityAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "subject",
        "activity_type",
        "assigned_to",
        "due_at",
        "completed_at",
        "ticket",
        "contact",
        "tenant",
    ]
    list_filter = ["activity_type", "tenant"]
    search_fields = ["subject", "notes"]
    ordering = ["due_at"]
    raw_id_fields = ["ticket", "contact", "created_by", "assigned_to"]


@admin.register(Reminder)
class ReminderAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "subject",
        "priority",
        "assigned_to",
        "scheduled_at",
        "completed_at",
        "cancelled_at",
        "contact",
        "ticket",
        "tenant",
    ]
    list_filter = ["priority", "tenant"]
    search_fields = ["subject", "notes"]
    ordering = ["scheduled_at"]
    raw_id_fields = ["ticket", "contact", "created_by", "assigned_to"]
