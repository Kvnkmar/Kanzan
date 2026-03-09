"""
File upload validation for the attachments app.

Uses python-magic to detect true MIME types from file content (not relying
on client-provided Content-Type or file extension), guarding against
file type spoofing.
"""

import logging

import magic
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

# Maximum allowed file size: 25 MB
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024

# Allowed MIME types: images, PDF, plain text, CSV, and common Office formats.
ALLOWED_MIME_TYPES = frozenset(
    {
        # Images
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        # Documents
        "application/pdf",
        "text/plain",
        "text/csv",
        # Microsoft Office (legacy)
        "application/msword",  # .doc
        "application/vnd.ms-excel",  # .xls
        "application/vnd.ms-powerpoint",  # .ppt
        # Microsoft Office (OpenXML)
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
        # OpenDocument
        "application/vnd.oasis.opendocument.text",  # .odt
        "application/vnd.oasis.opendocument.spreadsheet",  # .ods
        "application/vnd.oasis.opendocument.presentation",  # .odp
    }
)


def validate_file_upload(file_obj) -> str:
    """
    Validate an uploaded file for size and MIME type safety.

    Reads the first 2048 bytes of the file to detect the real MIME type
    using libmagic, regardless of the client-reported content type or
    file extension.

    Args:
        file_obj: A Django UploadedFile (or any file-like object with
                  .size and .read()/.seek() methods).

    Returns:
        The detected MIME type string (e.g. "image/png").

    Raises:
        ValidationError: If the file exceeds the size limit or has a
                         disallowed MIME type.
    """
    # --- 1. File size check ---
    if file_obj.size > MAX_FILE_SIZE_BYTES:
        max_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
        actual_mb = file_obj.size / (1024 * 1024)
        raise ValidationError(
            f"File size {actual_mb:.1f} MB exceeds the maximum allowed size of {max_mb:.0f} MB."
        )

    if file_obj.size == 0:
        raise ValidationError("Uploaded file is empty.")

    # --- 2. MIME type detection via python-magic ---
    # Read the first 2048 bytes for magic number detection.
    file_head = file_obj.read(2048)
    file_obj.seek(0)  # Reset file pointer for downstream consumers.

    detected_mime = magic.from_buffer(file_head, mime=True)

    logger.debug(
        "File upload validation: name=%s, size=%d, detected_mime=%s",
        getattr(file_obj, "name", "unknown"),
        file_obj.size,
        detected_mime,
    )

    # --- 3. Allowed MIME type check ---
    if detected_mime not in ALLOWED_MIME_TYPES:
        raise ValidationError(
            f"File type '{detected_mime}' is not allowed. "
            f"Allowed types: images (JPEG, PNG, GIF, WebP), PDF, plain text, CSV, "
            f"and Office documents (DOC, DOCX, XLS, XLSX, PPT, PPTX, ODT, ODS, ODP)."
        )

    return detected_mime
