"""
Billing models for the multi-tenant CRM platform.

* Plan         -- global pricing tiers with limits and feature flags
* Subscription -- per-tenant Stripe subscription state
* Invoice      -- synced copy of Stripe invoices
* UsageTracker -- per-tenant usage counters for plan-limit enforcement
"""

import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone

from main.models import TimestampedModel


class Plan(TimestampedModel):
    """
    Global billing plan definition.

    Each plan maps to a Stripe Product and contains the limits and feature
    flags that govern what a tenant on this plan can do.  Plans are NOT
    tenant-scoped -- they are shared across the whole platform.
    """

    class Tier(models.TextChoices):
        FREE = "free", "Free"
        PRO = "pro", "Pro"
        ENTERPRISE = "enterprise", "Enterprise"

    tier = models.CharField(
        max_length=20,
        choices=Tier.choices,
        unique=True,
    )
    name = models.CharField(max_length=100)
    stripe_product_id = models.CharField(max_length=255, unique=True)
    stripe_price_monthly = models.CharField(max_length=255, blank=True, default="")
    stripe_price_yearly = models.CharField(max_length=255, blank=True, default="")
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    # --- Limits (NULL = unlimited) ---
    max_users = models.PositiveIntegerField(null=True, blank=True)
    max_contacts = models.PositiveIntegerField(null=True, blank=True)
    max_tickets_per_month = models.PositiveIntegerField(null=True, blank=True)
    max_storage_mb = models.PositiveIntegerField(null=True, blank=True)
    max_custom_fields = models.PositiveIntegerField(null=True, blank=True)

    # --- Feature flags ---
    has_api_access = models.BooleanField(default=False)
    has_realtime = models.BooleanField(default=False)
    has_custom_roles = models.BooleanField(default=False)
    has_sso = models.BooleanField(default=False)
    has_sla_management = models.BooleanField(default=False)
    has_voip = models.BooleanField(
        default=False,
        help_text="Whether VoIP telephony is available on this plan.",
    )
    has_call_recording = models.BooleanField(
        default=False,
        help_text="Whether call recording is available on this plan.",
    )
    max_calls_per_month = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Monthly call limit.  NULL = unlimited.",
    )
    audit_retention_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of days to retain audit logs.  NULL = unlimited.",
    )

    class Meta:
        ordering = ["price_monthly"]
        verbose_name = "plan"
        verbose_name_plural = "plans"

    def __str__(self):
        return f"{self.name} ({self.tier})"


class Subscription(models.Model):
    """
    One-to-one link between a Tenant and its active Stripe subscription.

    The ``status`` field is kept in sync with Stripe via webhooks.
    """

    class Status(models.TextChoices):
        TRIALING = "trialing", "Trialing"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past Due"
        CANCELED = "canceled", "Canceled"
        INCOMPLETE = "incomplete", "Incomplete"
        UNPAID = "unpaid", "Unpaid"

    class BillingCycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    stripe_subscription_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    stripe_customer_id = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    billing_cycle = models.CharField(
        max_length=10,
        choices=BillingCycle.choices,
        default=BillingCycle.MONTHLY,
    )
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(null=True, blank=True)
    trial_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "subscription"
        verbose_name_plural = "subscriptions"

    def __str__(self):
        return f"{self.tenant} - {self.plan.name} ({self.status})"

    @property
    def is_active(self):
        """Return True if the subscription is in a usable state."""
        return self.status in (self.Status.ACTIVE, self.Status.TRIALING)

    @property
    def in_grace_period(self):
        """
        Return True if the subscription is past-due but still within the
        7-day grace window after ``current_period_end``.
        """
        if self.status != self.Status.PAST_DUE:
            return False
        if self.current_period_end is None:
            return False
        return timezone.now() <= self.current_period_end + timedelta(days=7)


class Invoice(models.Model):
    """
    Mirror of a Stripe Invoice, synced via webhooks.

    Provides tenants with an in-app invoice history without requiring
    round-trips to the Stripe API.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    stripe_invoice_id = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="usd")
    status = models.CharField(max_length=20)
    invoice_pdf_url = models.URLField(blank=True, default="")
    hosted_invoice_url = models.URLField(blank=True, default="")
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "invoice"
        verbose_name_plural = "invoices"

    def __str__(self):
        return f"Invoice {self.stripe_invoice_id} ({self.status})"


class UsageTracker(models.Model):
    """
    Per-tenant usage counters for the current billing period.

    Updated by application code (signals, service layer) and checked by
    ``PlanLimitChecker`` before allowing resource creation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="usage",
    )
    period_start = models.DateField()
    contacts_count = models.PositiveIntegerField(default=0)
    tickets_created = models.PositiveIntegerField(default=0)
    storage_used_mb = models.PositiveIntegerField(default=0)
    api_calls = models.PositiveIntegerField(default=0)
    calls_made = models.PositiveIntegerField(
        default=0,
        help_text="VoIP calls made in the current billing period.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "usage tracker"
        verbose_name_plural = "usage trackers"

    def __str__(self):
        return f"Usage for {self.tenant} (period {self.period_start})"
