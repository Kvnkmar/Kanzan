"""
Models for the contacts app.

Provides Company, Contact, and ContactGroup models for managing CRM contacts.
All models are tenant-scoped, ensuring strict data isolation between tenants.
"""

from django.db import models

from main.models import TenantScopedModel


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
    job_title = models.CharField(max_length=100, null=True, blank=True)
    source = models.CharField(
        max_length=50,
        choices=Source.choices,
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    custom_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary key-value data for tenant-defined custom fields.",
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
