"""
Models for the Knowledge Base app.

Provides tenant-scoped categories and articles for internal documentation,
FAQs, and guides accessible to all authenticated tenant members.
"""

from django.conf import settings
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
    """A knowledge base article with draft/published workflow."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

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

    class Meta:
        unique_together = [("tenant", "slug")]
        ordering = ["-is_pinned", "-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "category"]),
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

        super().save(*args, **kwargs)
