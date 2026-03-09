"""
Service layer for the attachments app.

Provides the dynamic upload path generator and a high-level helper
for creating validated attachment records.
"""

import logging
import uuid as uuid_mod

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.attachments.validators import validate_file_upload

logger = logging.getLogger(__name__)


def upload_path(instance, filename: str) -> str:
    """
    Generate a tenant-scoped upload path for attachment files.

    Produces paths like:
        tenants/<tenant_uuid>/attachments/2025/06/<random_uuid>_<filename>

    The random UUID prefix prevents filename collisions while preserving
    the original filename for human readability in storage.

    Args:
        instance: The Attachment model instance (tenant must be set).
        filename: The original filename from the upload.

    Returns:
        The relative path string for Django's FileField storage.
    """
    now = timezone.now()
    safe_filename = f"{uuid_mod.uuid4().hex[:12]}_{filename}"
    return (
        f"tenants/{instance.tenant_id}/attachments/"
        f"{now.strftime('%Y')}/{now.strftime('%m')}/{safe_filename}"
    )


def create_attachment(tenant, user, file_obj, content_object):
    """
    Validate and create an Attachment record for the given file.

    This is the primary service entry point for programmatic attachment
    creation (e.g. from API views, import scripts, or background tasks).

    Args:
        tenant: The Tenant instance this attachment belongs to.
        user: The User who is uploading the file.
        file_obj: A Django UploadedFile instance.
        content_object: The Django model instance to attach the file to.

    Returns:
        The created Attachment instance.

    Raises:
        ValidationError: If the file fails size or MIME type validation.
    """
    # Import here to avoid circular import (models.py imports upload_path
    # from this module).
    from apps.attachments.models import Attachment

    detected_mime = validate_file_upload(file_obj)

    content_type = ContentType.objects.get_for_model(content_object)

    attachment = Attachment(
        tenant=tenant,
        content_type=content_type,
        object_id=content_object.pk,
        file=file_obj,
        original_name=file_obj.name,
        mime_type=detected_mime,
        size_bytes=file_obj.size,
        uploaded_by=user,
    )
    attachment.save()

    logger.info(
        "Attachment created: tenant=%s user=%s file=%s mime=%s size=%d object=%s:%s",
        tenant.pk,
        user.pk,
        file_obj.name,
        detected_mime,
        file_obj.size,
        content_type.model,
        content_object.pk,
    )

    return attachment
