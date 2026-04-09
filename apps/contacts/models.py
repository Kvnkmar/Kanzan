"""
Models for the contacts app.

Provides Company, Contact, ContactGroup, and ContactEvent models for managing
CRM contacts. All models are tenant-scoped, ensuring strict data isolation
between tenants.
"""

from django.conf import settings
from django.db import models

from main.models import TenantScopedModel


class Account(TenantScopedModel):
    """
    Represents a CRM account (customer organisation) within a tenant.

    Accounts track commercial relationships and health metrics for pipeline
    management. Contacts may optionally be linked to an Account for
    account-based selling and support workflows.
    """

    name = models.CharField(max_length=255)
    industry = models.CharField(max_length=100, null=True, blank=True)
    company_size = models.CharField(max_length=50, null=True, blank=True)
    website = models.URLField(null=True, blank=True)
    mrr = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Monthly recurring revenue.",
    )
    health_score = models.PositiveSmallIntegerField(
        default=50,
        help_text="Account health score (0-100).",
    )

    class Meta:
        verbose_name = "account"
        verbose_name_plural = "accounts"
        ordering = ["-created_at"]
        unique_together = [("tenant", "name")]

    def __str__(self):
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        if self.health_score is not None and not (0 <= self.health_score <= 100):
            raise ValidationError(
                {"health_score": "Health score must be between 0 and 100."}
            )


class Company(TenantScopedModel):
    """
    Represents an organisation or business entity within a tenant.

    Companies serve as the parent entity for contacts, enabling grouping
    of individuals by their employer or affiliated organisation.
    """

    class Size(models.TextChoices):
        SMALL = "small", "Small"
        MEDIUM = "medium", "Medium"
        LARGE = "large", "Large"
        ENTERPRISE = "enterprise", "Enterprise"

    name = models.CharField(max_length=255)
    domain = models.CharField(max_length=255, null=True, blank=True)
    industry = models.CharField(max_length=100, null=True, blank=True)
    size = models.CharField(
        max_length=20,
        choices=Size.choices,
        null=True,
        blank=True,
    )
    phone = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    address = models.TextField(blank=True, default="")
    website = models.URLField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    custom_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary key-value data for tenant-defined custom fields.",
    )

    class Meta:
        verbose_name = "company"
        verbose_name_plural = "companies"
        ordering = ["-created_at"]
        unique_together = [("tenant", "name")]

    def __str__(self):
        return self.name


class Contact(TenantScopedModel):
    """
    Represents an individual person within a tenant's CRM.

    Contacts may optionally be associated with a Company and can belong
    to multiple ContactGroups for segmentation and targeted outreach.
    """

    class Source(models.TextChoices):
        WEB = "web", "Web"
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"
        REFERRAL = "referral", "Referral"
        SOCIAL = "social", "Social"
        OTHER = "other", "Other"

    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=20, null=True, blank=True)
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contacts",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contacts",
        help_text="CRM account this contact belongs to.",
    )
    job_title = models.CharField(max_length=100, null=True, blank=True)
    source = models.CharField(
        max_length=50,
        choices=Source.choices,
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    email_bouncing = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Set True when a hard bounce is detected for this contact's email.",
    )
    custom_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary key-value data for tenant-defined custom fields.",
    )
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Timestamp of the most recent contact event (auto-updated).",
    )
    lead_score = models.PositiveSmallIntegerField(
        default=50,
        help_text="Lead score (0-100), calculated nightly.",
    )

    class Meta:
        verbose_name = "contact"
        verbose_name_plural = "contacts"
        ordering = ["-created_at"]
        unique_together = [("tenant", "email")]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class ContactGroup(TenantScopedModel):
    """
    A named group (segment) of contacts within a tenant.

    Used for organising contacts into logical segments such as
    mailing lists, sales pipelines, or marketing campaigns.
    """

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    contacts = models.ManyToManyField(
        Contact,
        blank=True,
        related_name="groups",
    )

    class Meta:
        verbose_name = "contact group"
        verbose_name_plural = "contact groups"
        ordering = ["-created_at"]
        unique_together = [("tenant", "name")]

    def __str__(self):
        return self.name


class ContactEvent(TenantScopedModel):
    """
    Unified, append-only event log for a contact's 360° timeline.

    Aggregates events from tickets, activities, inbound email, and manual
    entries into a single chronological stream.
    """

    class Source(models.TextChoices):
        TICKET = "ticket", "Ticket"
        ACTIVITY = "activity", "Activity"
        EMAIL = "email", "Email"
        MANUAL = "manual", "Manual"

    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(
        max_length=50,
        help_text="Type of event (e.g. created, assigned, status_changed, commented).",
    )
    description = models.TextField(
        blank=True,
        default="",
    )
    metadata = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_events",
    )
    occurred_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
    )

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(
                fields=["contact", "occurred_at"],
                name="contactevent_contact_occurred",
            ),
            models.Index(
                fields=["tenant", "source"],
                name="contactevent_tenant_source",
            ),
        ]
        verbose_name = "contact event"
        verbose_name_plural = "contact events"

    def __str__(self):
        return f"[{self.contact}] {self.event_type} ({self.source})"
