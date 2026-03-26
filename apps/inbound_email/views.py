"""
Webhook endpoints for inbound email providers.

Supports SendGrid Inbound Parse and Mailgun Routes.
Both are CSRF-exempt and authenticated via shared secret in the URL.

All webhook handlers follow the same pattern:
1. Verify authentication (URL secret or provider-specific signature)
2. Parse the provider-specific payload into normalized fields
3. Normalize all Message-ID / In-Reply-To / References values (strip <>)
4. Create an InboundEmail record with status=PENDING
5. Save attachments to temporary storage
6. Queue async processing via Celery

The async processing pipeline (in services.py) handles:
- Loop/auto-reply filtering
- Tenant resolution
- Deduplication
- Ticket threading / creation
"""

import hashlib
import hmac
import logging
import time
import uuid as uuid_mod

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.inbound_email.models import InboundEmail
from apps.inbound_email.utils import (
    extract_header,
    normalize_message_id,
    normalize_references,
    parse_sender,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _verify_webhook_secret(request):
    """
    Verify the webhook secret from the URL query parameter.

    The inbound webhook URL includes ?secret=<INBOUND_EMAIL_WEBHOOK_SECRET>
    so providers don't need custom auth headers.
    """
    expected = getattr(settings, "INBOUND_EMAIL_WEBHOOK_SECRET", "")
    if not expected:
        logger.error("INBOUND_EMAIL_WEBHOOK_SECRET not configured.")
        return False
    provided = request.GET.get("secret", "")
    return hmac.compare_digest(provided, expected)


def _verify_mailgun_signature(request):
    """Verify Mailgun webhook signature using HMAC-SHA256."""
    api_key = getattr(settings, "MAILGUN_API_KEY", "")
    if not api_key:
        return False

    token = request.POST.get("token", "")
    timestamp = request.POST.get("timestamp", "")
    signature = request.POST.get("signature", "")

    if not all([token, timestamp, signature]):
        return False

    # Reject old timestamps (> 5 minutes)
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False

    expected = hmac.new(
        api_key.encode(),
        f"{timestamp}{token}".encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Webhook endpoints
# ---------------------------------------------------------------------------


@csrf_exempt
@require_POST
def sendgrid_inbound(request):
    """
    SendGrid Inbound Parse webhook.

    SendGrid posts multipart form data with these fields:
    - from, to, subject, text, html, headers, envelope
    - Optional: attachments as file uploads
    """
    if not _verify_webhook_secret(request):
        return HttpResponse(status=403)

    try:
        raw_headers = request.POST.get("headers", "")
        sender_name, sender_email = parse_sender(
            request.POST.get("from", ""),
        )

        # Extract and normalize threading headers from raw headers.
        # SendGrid delivers Message-ID, In-Reply-To, References inside
        # the raw headers blob, not as separate POST fields.
        raw_msg_id = extract_header(raw_headers, "Message-ID")
        message_id = (
            normalize_message_id(raw_msg_id)
            or f"sendgrid-{int(time.time())}"
        )

        inbound = InboundEmail.objects.create(
            message_id=message_id,
            in_reply_to=normalize_message_id(
                extract_header(raw_headers, "In-Reply-To"),
            ),
            references=normalize_references(
                extract_header(raw_headers, "References"),
            ),
            sender_email=sender_email,
            sender_name=sender_name,
            recipient_email=request.POST.get("to", ""),
            subject=request.POST.get("subject", ""),
            body_text=request.POST.get("text", ""),
            body_html=request.POST.get("html", ""),
            raw_headers=raw_headers,
            direction=InboundEmail.Direction.INBOUND,
            sender_type=InboundEmail.SenderType.CUSTOMER,
        )

        _save_inbound_attachments(request, inbound)
        _queue_processing(inbound.pk)
        return HttpResponse(status=200)

    except Exception:
        logger.exception("Error processing SendGrid inbound webhook")
        return HttpResponse(status=500)


@csrf_exempt
@require_POST
def mailgun_inbound(request):
    """
    Mailgun inbound routing webhook.

    Mailgun posts multipart form data with:
    - sender, from, recipient, subject, body-plain, body-html
    - Message-Id, In-Reply-To, References as separate POST fields
    """
    # Verify via URL secret OR Mailgun signature
    if not (_verify_webhook_secret(request) or _verify_mailgun_signature(request)):
        return HttpResponse(status=403)

    try:
        sender_name, sender_email = parse_sender(
            request.POST.get("from", request.POST.get("sender", "")),
        )

        # Mailgun provides Message-Id, In-Reply-To, References as direct
        # POST fields (not inside raw headers). Normalize consistently.
        raw_msg_id = request.POST.get("Message-Id", "")
        message_id = (
            normalize_message_id(raw_msg_id)
            or f"mailgun-{int(time.time())}"
        )

        inbound = InboundEmail.objects.create(
            message_id=message_id,
            in_reply_to=normalize_message_id(
                request.POST.get("In-Reply-To", ""),
            ),
            references=normalize_references(
                request.POST.get("References", ""),
            ),
            sender_email=sender_email,
            sender_name=sender_name,
            recipient_email=request.POST.get("recipient", ""),
            subject=request.POST.get("subject", ""),
            body_text=request.POST.get("body-plain", ""),
            body_html=request.POST.get("body-html", ""),
            raw_headers=request.POST.get("message-headers", ""),
            direction=InboundEmail.Direction.INBOUND,
            sender_type=InboundEmail.SenderType.CUSTOMER,
        )

        _save_inbound_attachments(request, inbound)
        _queue_processing(inbound.pk)
        return HttpResponse(status=200)

    except Exception:
        logger.exception("Error processing Mailgun inbound webhook")
        return HttpResponse(status=500)


# ---------------------------------------------------------------------------
# Attachment handling
# ---------------------------------------------------------------------------


def _save_inbound_attachments(request, inbound):
    """
    Save file attachments from the webhook request to temporary storage
    and record metadata on the InboundEmail record.

    Files are saved to inbound_emails/<inbound_id>/<filename> and tracked
    in the attachment_metadata JSON field for later processing by the
    async pipeline.
    """
    metadata = []
    for key, uploaded_file in request.FILES.items():
        safe_name = f"{uuid_mod.uuid4().hex[:8]}_{uploaded_file.name}"
        storage_path = f"inbound_emails/{inbound.pk}/{safe_name}"
        try:
            saved_path = default_storage.save(storage_path, uploaded_file)
            metadata.append({
                "filename": uploaded_file.name,
                "content_type": uploaded_file.content_type or "",
                "size": uploaded_file.size,
                "storage_path": saved_path,
            })
        except Exception:
            logger.warning(
                "Failed to save inbound attachment %s for email %s",
                uploaded_file.name, inbound.pk,
            )

    if metadata:
        inbound.attachment_metadata = metadata
        inbound.save(update_fields=["attachment_metadata", "updated_at"])
        logger.info(
            "Saved %d attachment(s) for inbound email %s",
            len(metadata), inbound.pk,
        )


# ---------------------------------------------------------------------------
# Async dispatch
# ---------------------------------------------------------------------------


def _queue_processing(inbound_email_id):
    """Queue the inbound email for async processing via Celery."""
    try:
        from apps.inbound_email.tasks import process_inbound_email_task

        process_inbound_email_task.delay(inbound_email_id)
    except Exception:
        logger.exception(
            "Failed to queue inbound email %s for processing. "
            "Will need manual retry.",
            inbound_email_id,
        )
