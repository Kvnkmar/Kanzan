"""
DRF serializers for the billing app.

Provides read-only representations for Plans, Subscriptions, Invoices,
and Usage data consumed by the tenant-facing billing dashboard.
"""

from rest_framework import serializers

from apps.billing.models import Invoice, Plan, Subscription, UsageTracker


class PlanSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for listing available billing plans.

    Exposes pricing, limits, and feature flags so the frontend can
    render a plan-comparison table.
    """

    class Meta:
        model = Plan
        fields = [
            "id",
            "tier",
            "name",
            "stripe_product_id",
            "stripe_price_monthly",
            "stripe_price_yearly",
            "price_monthly",
            "price_yearly",
            "is_active",
            "max_users",
            "max_contacts",
            "max_tickets_per_month",
            "max_storage_mb",
            "max_custom_fields",
            "has_api_access",
            "has_realtime",
            "has_custom_roles",
            "has_sso",
            "has_sla_management",
            "audit_retention_days",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SubscriptionSerializer(serializers.ModelSerializer):
    """
    Serializer for the tenant's subscription.

    Nests a read-only ``plan`` representation and exposes computed
    properties (``is_active``, ``in_grace_period``).
    """

    plan = PlanSerializer(read_only=True)
    plan_slug = serializers.CharField(source="plan.tier", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    in_grace_period = serializers.BooleanField(read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "tenant",
            "plan",
            "plan_slug",
            "plan_name",
            "stripe_subscription_id",
            "stripe_customer_id",
            "status",
            "billing_cycle",
            "current_period_start",
            "current_period_end",
            "cancel_at_period_end",
            "canceled_at",
            "trial_end",
            "is_active",
            "in_grace_period",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class InvoiceSerializer(serializers.ModelSerializer):
    """Read-only serializer for synced Stripe invoices."""

    class Meta:
        model = Invoice
        fields = [
            "id",
            "subscription",
            "stripe_invoice_id",
            "amount",
            "currency",
            "status",
            "invoice_pdf_url",
            "hosted_invoice_url",
            "period_start",
            "period_end",
            "created_at",
        ]
        read_only_fields = fields


class UsageSerializer(serializers.ModelSerializer):
    """Read-only serializer for tenant usage counters."""

    class Meta:
        model = UsageTracker
        fields = [
            "id",
            "tenant",
            "period_start",
            "contacts_count",
            "tickets_created",
            "storage_used_mb",
            "api_calls",
            "updated_at",
        ]
        read_only_fields = fields
