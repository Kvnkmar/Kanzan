"""
Models for the Knowledge Base app.

Provides tenant-scoped categories and articles for internal documentation,
FAQs, and guides accessible to all authenticated tenant members.
"""

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from main.models import TenantScopedModel


class Category(TenantScopedModel):
    """A grouping for knowledge base articles."""

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=50, blank=True, default="ti-folder")
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "slug")]
        ordering = ["order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Article(TenantScopedModel):
    """A knowledge base article with draft/published workflow and review support."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_REVIEW = "pending_review", "Pending Review"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"
        FLAGGED = "flagged", "Flagged for Review"

    class Visibility(models.TextChoices):
        INTERNAL = "internal", "Internal (agents only)"
        PUBLIC = "public", "Public"

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    content = models.TextField(blank=True, default="")
    excerpt = models.TextField(blank=True, default="")
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="kb_articles",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    is_pinned = models.BooleanField(default=False)
    view_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    file = models.FileField(
        upload_to="tenants/knowledge/articles/%Y/%m/",
        null=True,
        blank=True,
        help_text="Optional file attachment (PDF, DOCX, etc.)",
    )
    file_name = models.CharField(max_length=255, blank=True, default="")

    # Review workflow fields
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kb_reviewed_articles",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")

    # Gap-fill fields
    visibility = models.CharField(
        max_length=20,
        choices=Visibility,
        default=Visibility.INTERNAL,
    )
    review_at = models.DateField(null=True, blank=True)
    search_vector = SearchVectorField(null=True)

    class Meta:
        unique_together = [("tenant", "slug")]
        ordering = ["-is_pinned", "-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "category"]),
            models.Index(fields=["tenant", "status", "submitted_at"]),
            GinIndex(fields=["search_vector"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            counter = 1
            while (
                Article.unscoped.filter(tenant_id=self.tenant_id, slug=slug)
                .exclude(pk=self.pk)
                .exists()
            ):
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug

        if self.status == self.Status.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()

        if self.status == self.Status.PENDING_REVIEW and not self.submitted_at:
            self.submitted_at = timezone.now()

        super().save(*args, **kwargs)


class KBRevision(models.Model):
    """Snapshot of an article body at a point in time."""

    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    editor = models.ForeignKey(
        "accounts.TenantMembership",
        on_delete=models.SET_NULL,
        null=True,
        related_name="kb_revisions",
    )
    body_snapshot = models.TextField()
    change_note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Revision of '{self.article.title}' at {self.created_at}"


class KBVote(models.Model):
    """Helpfulness vote on a knowledge base article."""

    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    helpful = models.BooleanField()
    session_key = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("article", "session_key")]

    def __str__(self):
        label = "Helpful" if self.helpful else "Not helpful"
        return f"{label} — {self.article.title}"


class KBSearchGap(models.Model):
    """Tracks zero-result KB searches for content gap analysis."""

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
    )
    query = models.CharField(max_length=255)
    source = models.CharField(
        max_length=20,
        choices=[("agent", "Agent"), ("portal", "Portal")],
    )
    count = models.PositiveIntegerField(default=1)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("tenant", "query", "source")]
        ordering = ["-count"]

    def __str__(self):
        return f"Gap: '{self.query}' ({self.source}) x{self.count}"


class KBTicketLink(models.Model):
    """Links a ticket to a knowledge base article."""

    ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.CASCADE,
        related_name="kb_links",
    )
    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="ticket_links",
    )
    agent = models.ForeignKey(
        "accounts.TenantMembership",
        on_delete=models.SET_NULL,
        null=True,
        related_name="kb_ticket_links",
    )
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("ticket", "article")]

    def __str__(self):
        return f"Ticket #{self.ticket_id} -> {self.article.title}"
