"""Shared helpers for surfacing + streaming customer-sent email attachments.

Inbound files are saved to ``default_storage`` and described in
``InboundEmail.attachment_metadata`` — a list of
``{filename, content_type, size, storage_path}`` dicts. These helpers turn that
metadata into renderable rows (with authed, index-addressed download URLs) and
stream the bytes back safely. Used by both the Inbox Hub triage cockpit and the
agent Emails page so the two surfaces behave identically.
"""

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404

# Raster image types we are willing to render inline in an <img>. SVG is
# deliberately excluded — inline SVG can carry script, so it is served as a
# forced download (see ``stream_attachment``) and rendered as a file chip.
INLINE_IMAGE_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/bmp",
})


def serialize_attachments(inbound, url_for):
    """Serialise ``inbound.attachment_metadata`` into renderable rows.

    ``url_for`` is a ``callable(index) -> str`` that builds the download URL for
    a given attachment (each surface points at its own viewset action). The
    URL is index-addressed so the raw storage path is never exposed.
    """
    meta = inbound.attachment_metadata
    if not isinstance(meta, list):
        return []
    out = []
    for index, entry in enumerate(meta):
        if not isinstance(entry, dict):
            continue
        content_type = (entry.get("content_type") or "").lower()
        out.append({
            "index": index,
            "filename": entry.get("filename") or f"attachment-{index + 1}",
            "content_type": content_type,
            "size": entry.get("size") or 0,
            "is_image": content_type in INLINE_IMAGE_TYPES,
            "url": url_for(index),
        })
    return out


def stream_attachment(inbound, raw_index, *, force_download=False):
    """Return a ``FileResponse`` for the attachment at ``raw_index``.

    Raises ``Http404`` for a bad/out-of-range index or a file that no longer
    exists (expected once an email is converted to a ticket — the bytes move to
    a Ticket ``Attachment`` and the inbound storage path is deleted).

    Safe raster images are served inline so they can be embedded in an
    ``<img>``; everything else is forced to download with ``nosniff`` to avoid
    inline-script execution from a spoofed content type. ``force_download``
    forces the attachment disposition for any type.
    """
    meta_list = inbound.attachment_metadata
    if not isinstance(meta_list, list):
        raise Http404("No attachments on this email.")

    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        raise Http404("Invalid attachment index.")
    if index < 0 or index >= len(meta_list):
        raise Http404("Attachment not found.")

    meta = meta_list[index] or {}
    storage_path = meta.get("storage_path")
    if not storage_path or not default_storage.exists(storage_path):
        raise Http404("Attachment file is no longer available.")

    content_type = (meta.get("content_type") or "application/octet-stream").lower()
    inline = content_type in INLINE_IMAGE_TYPES and not force_download

    response = FileResponse(
        default_storage.open(storage_path, "rb"),
        as_attachment=not inline,
        filename=meta.get("filename") or f"attachment-{index + 1}",
        content_type=content_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
