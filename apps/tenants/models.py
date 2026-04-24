"""
Tenant and TenantSettings models for multi-tenant CRM platform.

The Tenant model represents an organisation (workspace) in the system.
TenantSettings holds per-tenant configuration such as SSO, branding, and locale.
"""

import datetime

from django.core.validators import RegexValidator
from django.db import models

from main.models import TimestampedModel


def default_business_days():
    return [0, 1, 2, 3, 4]  # Monday to Friday


class Tenant(TimestampedModel):
    """
    Represents a single tenant (organisation) in the multi-tenant platform.

    Each tenant is identified by a unique slug used for subdomain routing
    (e.g. "demo" -> demo.localhost) and optionally a custom domain.
    """

    name = models.CharField(max_length=255)
    slug = models.SlugField(
        max_length=63,
        unique=True,
        help_text="Subdomain identifier (e.g. 'demo' for demo.localhost).",
    )
    domain = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Optional custom domain (e.g. 'crm.acme.com').",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive tenants are denied access via middleware.",
    )
    logo = models.ImageField(
        upload_to="tenants/logos/",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "tenant"
        verbose_name_plural = "tenants"

    def __str__(self):
        return self.name


class TenantSettings(TimestampedModel):
    """
    Per-tenant configuration: authentication, branding, and locale settings.

    Created automatically via a post_save signal whenever a Tenant is created.
    """

    class AuthMethod(models.TextChoices):
        DJANGO = "django", "Django (email + password)"
        SSO = "sso", "Single Sign-On only"
        BOTH = "both", "Django + SSO"

    class SSOProvider(models.TextChoices):
        GOOGLE = "google", "Google"
        MICROSOFT = "microsoft", "Microsoft"
        OKTA = "okta", "Okta"
        CUSTOM = "custom", "Custom OIDC"

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="settings",
    )

    # --- Authentication / SSO ---
    auth_method = models.CharField(
        max_length=10,
        choices=AuthMethod.choices,
        default=AuthMethod.DJANGO,
    )
    sso_provider = models.CharField(
        max_length=20,
        choices=SSOProvider.choices,
        null=True,
        blank=True,
    )
    sso_client_id = models.CharField(max_length=255, null=True, blank=True)
    sso_client_secret = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Encrypted in production via application-level encryption.",
    )
    sso_authority_url = models.URLField(null=True, blank=True)
    sso_scopes = models.CharField(
        max_length=500,
        default="openid email profile",
    )

    # --- Locale / Display ---
    timezone = models.CharField(max_length=50, default="UTC")
    date_format = models.CharField(max_length=20, default="YYYY-MM-DD")

    # --- Business Hours (for SLA calculations) ---
    business_hours_start = models.TimeField(
        default=datetime.time(9, 0),
        help_text="Business hours start (tenant local time).",
    )
    business_hours_end = models.TimeField(
        default=datetime.time(17, 0),
        help_text="Business hours end (tenant local time).",
    )
    business_days = models.JSONField(
        default=default_business_days,
        help_text="ISO weekday integers (0=Mon, 6=Sun) that are business days.",
    )

    # --- Inbound Email ---
    inbound_email_address = models.EmailField(
        null=True,
        blank=True,
        unique=True,
        help_text="Custom inbound email address for this tenant (e.g. support@acme.com).",
    )

    # --- Closure / CSAT ---
    auto_close_days = models.PositiveIntegerField(
        default=5,
        help_text="Days after 'resolved' before auto-closing the ticket.",
    )
    csat_delay_minutes = models.PositiveIntegerField(
        default=60,
        help_text="Minutes after 'resolved' before sending CSAT survey email.",
    )

    # --- Workflow ---
    auto_transition_on_assign = models.BooleanField(
        default=True,
        help_text=(
            "When True, assigning a ticket on the default (Open) status "
            "automatically transitions it to In Progress."
        ),
    )
    auto_send_ticket_created_email = models.BooleanField(
        default=True,
        help_text=(
            "When True, the ticket-created confirmation email is sent to the "
            "contact automatically the moment a ticket is created from an "
            "inbound email. When False, agents send it manually from the "
            "ticket page (button: 'Send confirmation email')."
        ),
    )

    # --- Branding ---
    primary_color = models.CharField(
        max_length=7,
        default="#6366F1",
        validators=[
            RegexValidator(
                regex=r"^#[0-9a-fA-F]{6}$",
                message="Enter a valid hex colour code (e.g. #6366F1).",
            ),
        ],
        help_text="Hex colour code for the tenant's primary brand colour.",
    )
    accent_color = models.CharField(
        max_length=7,
        default="#F59E0B",
        validators=[
            RegexValidator(
                regex=r"^#[0-9a-fA-F]{6}$",
                message="Enter a valid hex colour code (e.g. #F59E0B).",
            ),
        ],
        help_text="Hex colour code for accent/highlight elements (badges, alerts).",
    )

    class Meta:
        verbose_name = "tenant settings"
        verbose_name_plural = "tenant settings"

    def __str__(self):
        return f"Settings for {self.tenant}"

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        if self.business_days is not None:
            if not isinstance(self.business_days, list):
                raise ValidationError({"business_days": "Must be a list of integers 0-6."})
            valid_days = set(range(7))
            for day in self.business_days:
                if not isinstance(day, int) or day not in valid_days:
                    raise ValidationError(
                        {"business_days": f"Invalid day value: {day}. Must be integers 0 (Mon) to 6 (Sun)."}
                    )
