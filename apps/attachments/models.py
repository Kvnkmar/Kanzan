"""
Models for the secure file attachment system.

Attachment supports polymorphic association to any model via GenericForeignKey,
with tenant-scoped file storage paths and MIME type validation.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.attachments.services import upload_path
from main.models import TenantScopedModel


class Attachment(TenantScopedModel):
    """
    A file attachment linked to any tenant-scoped entity via GenericForeignKey.

    Files are stored in tenant-scoped paths to ensure isolation:
        tenants/<tenant_id>/attachments/YYYY/MM/<filename>

    MIME type is validated server-side via python-magic to prevent spoofing.
    """

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    object_id = models.UUIDField()
    content_object = GenericForeignKey("content_type", "object_id")

    file = models.FileField(upload_to=upload_path)
    original_name = models.CharField(
        max_length=255,
        help_text="Original filename as uploaded by the user.",
    )
    mime_type = models.CharField(
        max_length=100,
        help_text="MIME type detected via python-magic (not from the client).",
    )
    size_bytes = models.PositiveIntegerField(
        help_text="File size in bytes.",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="uploaded_attachments",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["content_type", "object_id"],
                name="attachment_ct_obj_idx",
            ),
        ]
        verbose_name = "attachment"
        verbose_name_plural = "attachments"

    def __str__(self):
        return f"{self.original_name} ({self.mime_type}, {self.size_display})"

    @property
    def size_display(self) -> str:
        """Human-readable file size."""
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        else:
            return f"{self.size_bytes / (1024 * 1024):.1f} MB"
