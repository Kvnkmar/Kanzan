"""
Read-only Django admin for ``APIKey``.

The cleartext secret is never available here — only the hash + prefix. Most
fields are read-only to keep the admin from drift-editing what the service
layer is the source of truth for.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.api_keys.models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = (
        "name",
        "tenant",
        "role",
        "prefix",
        "is_active",
        "last_used_at",
        "request_count",
        "created_at",
    )
    list_filter = ("is_active", "tenant", "role")
    search_fields = ("name", "prefix")
    readonly_fields = (
        "hashed_key",
        "prefix",
        "service_user",
        "created_by",
        "request_count",
        "last_used_at",
        "last_used_ip",
        "last_used_user_agent",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("service_user", "created_by", "role")
