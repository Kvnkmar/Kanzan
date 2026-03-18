"""
Django admin configuration for the custom_fields app.

Registers CustomFieldDefinition and CustomFieldValue with appropriate
display, search, and filter options for administrative use.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.custom_fields.models import CustomFieldDefinition, CustomFieldValue


@admin.register(CustomFieldDefinition)
class CustomFieldDefinitionAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "module",
        "field_type",
        "is_required",
        "is_active",
        "order",
        "tenant",
        "created_at",
    ]
    list_filter = ["module", "field_type", "is_required", "is_active", "tenant"]
    search_fields = ["name", "slug"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["module", "order"]
    filter_horizontal = ["visible_to_roles"]


@admin.register(CustomFieldValue)
class CustomFieldValueAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "field",
        "content_type",
        "object_id",
        "display_value_truncated",
        "tenant",
        "created_at",
    ]
    list_filter = ["field__module", "content_type", "tenant"]
    search_fields = ["field__name", "value_text"]
    readonly_fields = [
        "id",
        "field",
        "content_type",
        "object_id",
        "value_text",
        "value_number",
        "value_date",
        "value_bool",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["field"]
    ordering = ["-created_at"]

    @admin.display(description="Value")
    def display_value_truncated(self, obj):
        """Show a truncated version of the stored value."""
        value = obj.display_value
        if value is None:
            return "-"
        text = str(value)
        return text[:80] + "..." if len(text) > 80 else text
