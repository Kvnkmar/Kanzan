"""
Django admin configuration for the tenants app.
"""

from django.contrib import admin

from apps.tenants.models import Tenant, TenantSettings


class TenantSettingsInline(admin.StackedInline):
    model = TenantSettings
    can_delete = False
    verbose_name_plural = "Settings"
    fieldsets = (
        (
            "Authentication",
            {
                "fields": (
                    "auth_method",
                    "sso_provider",
                    "sso_client_id",
                    "sso_client_secret",
                    "sso_authority_url",
                    "sso_scopes",
                ),
            },
        ),
        (
            "Locale & Display",
            {
                "fields": ("timezone", "date_format"),
            },
        ),
        (
            "Branding",
            {
                "fields": ("primary_color",),
            },
        ),
    )


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "domain", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "domain")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [TenantSettingsInline]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "name", "slug", "domain", "is_active", "logo"),
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


@admin.register(TenantSettings)
class TenantSettingsAdmin(admin.ModelAdmin):
    list_display = ("tenant", "auth_method", "timezone", "primary_color")
    list_filter = ("auth_method", "sso_provider")
    search_fields = ("tenant__name", "tenant__slug")
    readonly_fields = ("created_at", "updated_at")
