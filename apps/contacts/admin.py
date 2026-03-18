"""
Django admin configuration for the contacts app.

Registers Company, Contact, and ContactGroup with appropriate display,
search, and filter options for administrative use.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.contacts.models import Company, Contact, ContactGroup


@admin.register(Company)
class CompanyAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "name",
        "domain",
        "industry",
        "size",
        "email",
        "tenant",
        "created_at",
    ]
    list_filter = ["size", "industry", "tenant"]
    search_fields = ["name", "domain", "email"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]


@admin.register(Contact)
class ContactAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "full_name",
        "email",
        "phone",
        "company",
        "source",
        "is_active",
        "tenant",
        "created_at",
    ]
    list_filter = ["is_active", "source", "company", "tenant"]
    search_fields = [
        "first_name",
        "last_name",
        "email",
        "company__name",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]
    raw_id_fields = ["company"]

    @admin.display(description="Full Name")
    def full_name(self, obj):
        return obj.full_name


@admin.register(ContactGroup)
class ContactGroupAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = [
        "name",
        "contact_count",
        "tenant",
        "created_at",
    ]
    list_filter = ["tenant"]
    search_fields = ["name", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]
    filter_horizontal = ["contacts"]

    @admin.display(description="Contacts")
    def contact_count(self, obj):
        return obj.contacts.count()
