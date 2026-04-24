"""
In-process SMTP server for receiving inbound email directly.

Runs an aiosmtpd server that listens on a configurable host/port,
parses incoming RFC 5322 messages, creates an ``InboundEmail`` record
per recipient, and queues the existing async processing pipeline.
No 3rd-party webhook provider required — point MX records (or a
forwarding relay) at this server to deliver mail.

Recipient validation happens at SMTP RCPT TO time: the address must
resolve to an active tenant (via plus-addressing, slug routing, or
the tenant's configured inbound_email_address). Unknown recipients
are rejected with a 550 to prevent the server being used as an
open relay.

Optional SMTP AUTH and STARTTLS are configured via settings for
submission scenarios (exposing the server for authenticated senders).
"""

import email
import logging
import uuid
from email.policy import default as default_policy

from aiosmtpd.smtp import AuthResult, LoginPassword
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.inbound_email.models import InboundEmail
from apps.inbound_email.services import resolve_tenant_from_address
from apps.inbound_email.utils import (
    normalize_message_id,
    normalize_references,
    parse_sender,
)

logger = logging.getLogger(__name__)


MAX_MESSAGE_BYTES = 25 * 1024 * 1024  # 25MB, matches DATA_UPLOAD_MAX_MEMORY_SIZE


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class KanzanSMTPHandler:
    """
    aiosmtpd handler that validates recipients against known tenants
    and feeds accepted messages into the InboundEmail pipeline.
    """

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        is_valid = await sync_to_async(
            self._recipient_is_valid, thread_sensitive=True
        )(address)
        if not is_valid:
            logger.info("SMTP rejecting recipient %s (no tenant match)", address)
            return "550 No such user here"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        raw_bytes = envelope.content
        if raw_bytes is None:
            return "451 Requested action aborted: empty body"
        if len(raw_bytes) > MAX_MESSAGE_BYTES:
            return f"552 Message exceeds size limit ({MAX_MESSAGE_BYTES} bytes)"

        try:
            msg = email.message_from_bytes(raw_bytes, policy=default_policy)
            await sync_to_async(self._ingest, thread_sensitive=True)(
                envelope.mail_from or "",
                list(envelope.rcpt_tos),
                msg,
            )
        except Exception:
            logger.exception("SMTP handle_DATA failed")
            return "451 Requested action aborted: local error in processing"
        return "250 Message accepted for delivery"

    # -- sync helpers (run inside a thread via sync_to_async) ----------------

    def _recipient_is_valid(self, recipient):
        try:
            return resolve_tenant_from_address(recipient) is not None
        except Exception:
            logger.exception("Tenant resolution failed for %s", recipient)
            return False

    def _ingest(self, mail_from, rcpt_tos, msg):
        sender_name, sender_email = parse_sender(msg.get("From", "") or mail_from)
        if not sender_email:
            sender_email = mail_from

        subject = (msg.get("Subject") or "").replace("\r", "").replace("\n", " ")
        base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
        raw_msg_id = msg.get("Message-ID", "") or msg.get("Message-Id", "")
        message_id = (
            normalize_message_id(raw_msg_id)
            or f"smtp-{uuid.uuid4().hex}@{base_domain}"
        )
        in_reply_to = normalize_message_id(msg.get("In-Reply-To", ""))
        references = normalize_references(msg.get("References", ""))

        body_text, body_html = _extract_bodies(msg)
        raw_headers = _render_headers(msg)

        for recipient in rcpt_tos:
            inbound = InboundEmail.objects.create(
                message_id=message_id,
                in_reply_to=in_reply_to,
                references=references,
                sender_email=sender_email,
                sender_name=sender_name,
                recipient_email=recipient,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                raw_headers=raw_headers,
                direction=InboundEmail.Direction.INBOUND,
                sender_type=InboundEmail.SenderType.CUSTOMER,
            )
            _save_smtp_attachments(inbound, msg)
            _queue_processing(inbound.pk)


# ---------------------------------------------------------------------------
# MIME helpers
# ---------------------------------------------------------------------------


def _extract_bodies(msg):
    """Walk a parsed email.Message and return (text_body, html_body)."""
    text_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and not text_body:
                text_body = _safe_get_content(part)
            elif ctype == "text/html" and not html_body:
                html_body = _safe_get_content(part)
    else:
        ctype = msg.get_content_type()
        content = _safe_get_content(msg)
        if ctype == "text/html":
            html_body = content
        else:
            text_body = content

    return text_body or "", html_body or ""


def _safe_get_content(part):
    """Decode a non-multipart part to a string, with a bytes fallback."""
    try:
        return part.get_content()
    except (LookupError, UnicodeDecodeError, AttributeError):
        payload = part.get_payload(decode=True) or b""
        if isinstance(payload, bytes):
            return payload.decode(errors="replace")
        return str(payload)


def _render_headers(msg):
    return "\n".join(f"{k}: {v}" for k, v in msg.items())


def _save_smtp_attachments(inbound, msg):
    """Extract attachment parts and persist them to default_storage."""
    metadata = []
    if not msg.is_multipart():
        return

    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if "attachment" not in disposition and not filename:
            continue
        filename = filename or f"attachment-{uuid.uuid4().hex[:8]}"

        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            logger.exception("Failed to decode attachment %s", filename)
            continue
        if not payload:
            continue

        safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
        storage_path = f"inbound_emails/{inbound.pk}/{safe_name}"
        try:
            saved_path = default_storage.save(storage_path, ContentFile(payload))
            metadata.append({
                "filename": filename,
                "content_type": part.get_content_type() or "application/octet-stream",
                "size": len(payload),
                "storage_path": saved_path,
            })
        except Exception:
            logger.exception("Failed to save attachment %s", filename)

    if metadata:
        inbound.attachment_metadata = metadata
        inbound.save(update_fields=["attachment_metadata", "updated_at"])


def _queue_processing(inbound_email_id):
    """Dispatch the existing Celery pipeline for this InboundEmail record."""
    try:
        from apps.inbound_email.tasks import process_inbound_email_task

        process_inbound_email_task.delay(str(inbound_email_id))
    except Exception:
        logger.exception(
            "Failed to queue inbound email %s for processing", inbound_email_id,
        )


# ---------------------------------------------------------------------------
# Optional SMTP AUTH authenticator
# ---------------------------------------------------------------------------


class StaticAuthenticator:
    """
    Accepts AUTH LOGIN / AUTH PLAIN against a fixed dict of
    ``{username: password}`` pairs loaded from settings.
    """

    def __init__(self, users):
        self.users = dict(users or {})

    def __call__(self, server, session, envelope, mechanism, auth_data):
        if not isinstance(auth_data, LoginPassword):
            return AuthResult(success=False, handled=False)
        try:
            username = auth_data.login.decode()
            password = auth_data.password.decode()
        except UnicodeDecodeError:
            return AuthResult(success=False, handled=False)
        if self.users.get(username) == password:
            return AuthResult(success=True)
        return AuthResult(success=False, handled=False)
