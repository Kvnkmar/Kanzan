"""
Django admin configuration for the VoIP app.

Registers VoIP models with appropriate display, search, and filter
options for administrative use.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.voip.models import (
    CallLog,
    CallQueue,
    CallRecording,
    Extension,
    VoIPSettings,
)


@admin.register(VoIPSettings)
class VoIPSettingsAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "tenant",
        "asterisk_host",
        "is_active",
        "recording_enabled",
        "created_at",
    ]
    list_filter = ["is_active", "recording_enabled"]
    search_fields = ["tenant__name"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(Extension)
class ExtensionAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "extension_number",
        "user",
        "sip_username",
        "is_active",
        "registered_at",
        "tenant",
    ]
    list_filter = ["is_active", "tenant"]
    search_fields = ["extension_number", "sip_username", "user__email"]
    readonly_fields = ["id", "registered_at", "created_at", "updated_at"]
    raw_id_fields = ["user"]


@admin.register(CallLog)
class CallLogAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "direction",
        "status",
        "caller_number",
        "callee_number",
        "duration_seconds",
        "started_at",
        "tenant",
    ]
    list_filter = ["direction", "status", "tenant"]
    search_fields = ["caller_number", "callee_number", "asterisk_channel_id"]
    readonly_fields = [
        "id",
        "asterisk_channel_id",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["caller_extension", "callee_extension", "contact", "ticket"]
    date_hierarchy = "started_at"
    ordering = ["-started_at"]


@admin.register(CallRecording)
class CallRecordingAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "call_log",
        "duration_seconds",
        "size_bytes",
        "mime_type",
        "created_at",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["call_log"]


@admin.register(CallQueue)
class CallQueueAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "name",
        "strategy",
        "timeout_seconds",
        "is_active",
        "tenant",
    ]
    list_filter = ["strategy", "is_active", "tenant"]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    filter_horizontal = ["members"]
