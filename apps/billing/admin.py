"""
Django admin configuration for the billing app.
"""

from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.billing.models import Invoice, Plan, Subscription, UsageTracker


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "tier",
        "price_monthly",
        "price_yearly",
        "is_active",
        "max_users",
        "max_contacts",
    )
    list_filter = ("tier", "is_active")
    search_fields = ("name", "tier", "stripe_product_id")
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "tier",
                    "name",
                    "is_active",
                ),
            },
        ),
        (
            "Stripe",
            {
                "fields": (
                    "stripe_product_id",
                    "stripe_price_monthly",
                    "stripe_price_yearly",
                ),
            },
        ),
        (
            "Pricing",
            {
                "fields": ("price_monthly", "price_yearly"),
            },
        ),
        (
            "Limits",
            {
                "description": "Leave blank for unlimited.",
                "fields": (
                    "max_users",
                    "max_contacts",
                    "max_tickets_per_month",
                    "max_storage_mb",
                    "max_custom_fields",
                ),
            },
        ),
        (
            "Feature Flags",
            {
                "fields": (
                    "has_api_access",
                    "has_realtime",
                    "has_custom_roles",
                    "has_sso",
                    "has_sla_management",
                    "audit_retention_days",
                ),
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


@admin.register(Subscription)
class SubscriptionAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = (
        "tenant",
        "plan",
        "status",
        "billing_cycle",
        "current_period_end",
        "cancel_at_period_end",
    )
    list_filter = ("status", "billing_cycle", "cancel_at_period_end")
    search_fields = (
        "tenant__name",
        "tenant__slug",
        "stripe_subscription_id",
        "stripe_customer_id",
    )
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("tenant", "plan")
    fieldsets = (
        (
            None,
            {
                "fields": ("id", "tenant", "plan", "status", "billing_cycle"),
            },
        ),
        (
            "Stripe",
            {
                "fields": (
                    "stripe_subscription_id",
                    "stripe_customer_id",
                ),
            },
        ),
        (
            "Period",
            {
                "fields": (
                    "current_period_start",
                    "current_period_end",
                    "trial_end",
                ),
            },
        ),
        (
            "Cancellation",
            {
                "fields": ("cancel_at_period_end", "canceled_at"),
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


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "stripe_invoice_id",
        "subscription",
        "amount",
        "currency",
        "status",
        "created_at",
    )
    list_filter = ("status", "currency")
    search_fields = (
        "stripe_invoice_id",
        "subscription__tenant__name",
        "subscription__stripe_customer_id",
    )
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("subscription",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "subscription",
                    "stripe_invoice_id",
                    "status",
                ),
            },
        ),
        (
            "Amounts",
            {
                "fields": ("amount", "currency"),
            },
        ),
        (
            "Period",
            {
                "fields": ("period_start", "period_end"),
            },
        ),
        (
            "Links",
            {
                "fields": ("invoice_pdf_url", "hosted_invoice_url"),
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": ("created_at",),
            },
        ),
    )


@admin.register(UsageTracker)
class UsageTrackerAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = (
        "tenant",
        "period_start",
        "contacts_count",
        "tickets_created",
        "storage_used_mb",
        "api_calls",
        "updated_at",
    )
    search_fields = ("tenant__name", "tenant__slug")
    readonly_fields = ("id", "updated_at")
    raw_id_fields = ("tenant",)
    fieldsets = (
        (
            None,
            {
                "fields": ("id", "tenant", "period_start"),
            },
        ),
        (
            "Counters",
            {
                "fields": (
                    "contacts_count",
                    "tickets_created",
                    "storage_used_mb",
                    "api_calls",
                ),
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": ("updated_at",),
            },
        ),
    )
