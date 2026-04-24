"""
IMAP poller — pulls new messages from a shared mailbox (e.g. Gmail)
and feeds them into the InboundEmail pipeline.

This lets customers reply to outbound ticket emails using their normal
email client without requiring us to expose port 25 on a public domain.
The polled mailbox acts as the tenant's inbound address.

Flow:
  1. Connect to IMAP_HOST:IMAP_PORT (SSL by default)
  2. LOGIN with IMAP_USER / IMAP_PASSWORD
  3. SELECT IMAP_MAILBOX (default INBOX)
  4. Read the persisted UID watermark for this mailbox. If UIDVALIDITY
     has changed (mailbox reset) or we have no state yet, initialise to
     (UIDNEXT - 1) so we skip historical backfill.
  5. UID SEARCH for messages with UID > watermark. This ingests every
     new message regardless of its ``\\Seen`` flag — so an operator
     clicking the message in Gmail to verify it arrived will NOT
     prevent ticketing.
  6. For each: FETCH the RFC822 body, parse, create InboundEmail row,
     queue processing task, and bump the watermark.

Duplicate protection: ``InboundEmail.message_id`` is unique-scoped
downstream, so retry loops and edge-case re-fetches never create
duplicate tickets.

Tenant resolution and ticket threading happen downstream in
process_inbound_email(), exactly like for messages arriving via the
SMTP server.
"""

import email
import imaplib
import logging
import uuid
from email.policy import default as default_policy

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from apps.inbound_email.models import IMAPPollState, InboundEmail
from apps.inbound_email.utils import (
    normalize_message_id,
    normalize_references,
    parse_sender,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def poll_once():
    """
    Connect, fetch messages whose UID is above the stored watermark,
    ingest each one, bump the watermark, disconnect.

    Returns the number of messages successfully ingested. Returns 0 and
    logs a debug line if IMAP_HOST is unset (feature disabled).
    """
    host = getattr(settings, "IMAP_HOST", "")
    user = getattr(settings, "IMAP_USER", "")
    password = getattr(settings, "IMAP_PASSWORD", "")
    if not (host and user and password):
        logger.debug("IMAP poll skipped — IMAP_HOST/USER/PASSWORD not configured.")
        return 0

    port = getattr(settings, "IMAP_PORT", 993)
    use_ssl = getattr(settings, "IMAP_USE_SSL", True)
    mailbox = getattr(settings, "IMAP_MAILBOX", "INBOX")

    conn = None
    ingested = 0
    try:
        conn = _connect(host, port, user, password, use_ssl)
        typ, select_resp = conn.select(mailbox, readonly=False)
        if typ != "OK":
            logger.warning("IMAP SELECT %s failed: %s", mailbox, select_resp)
            return 0

        uid_validity = _read_uidvalidity(conn, mailbox)
        state = _load_state(host, user, mailbox, uid_validity)

        # First run on this mailbox, or UIDVALIDITY changed (rare; implies
        # the mailbox was reset). Skip historical messages by anchoring at
        # UIDNEXT - 1 so we only pick up genuinely new mail.
        if state.last_uid == 0:
            uidnext = _read_uidnext(conn, mailbox)
            if uidnext > 1:
                state.last_uid = uidnext - 1
                state.save(update_fields=["last_uid", "updated_at"])
                logger.info(
                    "IMAP poll: initialised watermark for %s/%s to UID %d (no backfill).",
                    user, mailbox, state.last_uid,
                )

        # Search strictly above the watermark. "UID N:*" includes any UID
        # >= N; we add 1 so the stored last_uid is "last processed", not
        # "next to try".
        search_start = state.last_uid + 1
        typ, data = conn.uid("SEARCH", None, f"UID {search_start}:*")
        if typ != "OK":
            logger.warning("IMAP UID SEARCH failed: %s", data)
            return 0

        raw_uids = [u for u in (data[0] or b"").split() if u]
        # Filter: IMAP "N:*" returns at least the highest UID even when
        # nothing matches, so drop anything <= watermark.
        try:
            new_uids = sorted(
                (u for u in raw_uids if int(u) > state.last_uid),
                key=lambda x: int(x),
            )
        except ValueError:
            logger.warning("IMAP UID SEARCH returned non-numeric UIDs: %s", raw_uids)
            return 0

        if not new_uids:
            logger.debug("IMAP poll: no new messages (watermark=%d).", state.last_uid)
            return 0

        logger.info(
            "IMAP poll: %d new message(s) in %s (watermark=%d).",
            len(new_uids), mailbox, state.last_uid,
        )

        for uid in new_uids:
            uid_int = int(uid)
            try:
                if _ingest_one(conn, uid):
                    ingested += 1
            except Exception:
                # Never let one bad message block the rest of the batch.
                # We still advance the watermark so we don't get stuck
                # retrying a poisoned message forever. The message stays
                # in the mailbox and can be re-ingested manually.
                logger.exception("Failed to ingest IMAP message uid=%s", uid)
            # Advance the watermark as we go — if the process dies
            # mid-batch we resume from the last good UID next poll.
            if uid_int > state.last_uid:
                state.last_uid = uid_int
                state.save(update_fields=["last_uid", "updated_at"])

        return ingested
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn.logout()
            except Exception:
                pass


def _load_state(host, user, mailbox, uid_validity):
    """Fetch or create poll-state row; reset watermark if UIDVALIDITY changed."""
    with transaction.atomic():
        state, created = IMAPPollState.objects.select_for_update().get_or_create(
            host=host, user=user, mailbox=mailbox,
            defaults={"uid_validity": uid_validity, "last_uid": 0},
        )
        if not created and state.uid_validity != uid_validity:
            logger.warning(
                "IMAP UIDVALIDITY changed for %s/%s (%d → %d); resetting watermark.",
                user, mailbox, state.uid_validity, uid_validity,
            )
            state.uid_validity = uid_validity
            state.last_uid = 0
            state.save(update_fields=["uid_validity", "last_uid", "updated_at"])
    return state


def _read_uidvalidity(conn, mailbox):
    """Read UIDVALIDITY from the untagged SELECT response."""
    return _read_untagged_int(conn, "UIDVALIDITY")


def _read_uidnext(conn, mailbox):
    """Return the mailbox UIDNEXT (UID the next new message will get)."""
    return _read_untagged_int(conn, "UIDNEXT") or 1


def _read_untagged_int(conn, name):
    """
    Read an integer-valued untagged response captured by SELECT.

    SELECT returns ``* OK [UIDVALIDITY 1]``, ``* OK [UIDNEXT 370]`` etc.
    as untagged responses; imaplib stashes them in ``untagged_responses``.
    STATUS is not a reliable alternative because many servers forbid
    STATUS on the currently-selected mailbox.
    """
    raw_entries = getattr(conn, "untagged_responses", {}).get(name, [])
    for raw in raw_entries:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode(errors="replace")
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if digits:
            try:
                return int(digits)
            except ValueError:
                continue
    return 0


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def _connect(host, port, user, password, use_ssl):
    cls = imaplib.IMAP4_SSL if use_ssl else imaplib.IMAP4
    conn = cls(host, port)
    conn.login(user, password)
    return conn


# ---------------------------------------------------------------------------
# Per-message ingest
# ---------------------------------------------------------------------------


def _ingest_one(conn, uid):
    """Fetch one message by UID, create InboundEmail, queue processing."""
    typ, raw_data = conn.uid("FETCH", uid, "(RFC822)")
    if typ != "OK" or not raw_data:
        logger.warning("IMAP UID FETCH failed for uid=%s: %s", uid, raw_data)
        return False

    # Response is a list: [(b"UID (RFC822 {N}", b"<raw bytes>"), b")"]
    raw_bytes = None
    for item in raw_data:
        if isinstance(item, tuple) and len(item) >= 2:
            raw_bytes = item[1]
            break
    if not raw_bytes:
        logger.warning("IMAP FETCH returned no body for uid=%s", uid)
        return False

    msg = email.message_from_bytes(raw_bytes, policy=default_policy)

    # Dedup before creating a row — matches the SMTP server's pattern so
    # both ingestion paths share the same Message-ID space.
    raw_msg_id = msg.get("Message-ID", "") or msg.get("Message-Id", "")
    message_id = (
        normalize_message_id(raw_msg_id)
        or f"imap-{uuid.uuid4().hex}@{getattr(settings, 'BASE_DOMAIN', 'localhost')}"
    )
    if InboundEmail.objects.filter(message_id=message_id).exists():
        logger.info("IMAP skip uid=%s — message_id %s already ingested.", uid, message_id)
        _mark_seen(conn, uid)
        return False

    sender_name, sender_email = parse_sender(msg.get("From", ""))
    subject = (msg.get("Subject") or "").replace("\r", "").replace("\n", " ")

    # Recipient: prefer Delivered-To / To so the existing tenant-resolution
    # logic (custom inbound address) can find the right tenant.
    recipient = (
        msg.get("Delivered-To")
        or msg.get("X-Delivered-To")
        or msg.get("To")
        or getattr(settings, "IMAP_USER", "")
    )
    recipient = (recipient or "").split(",")[0].strip()
    _, parsed_rcpt = parse_sender(recipient)
    if parsed_rcpt:
        recipient = parsed_rcpt

    body_text, body_html = _extract_bodies(msg)

    inbound = InboundEmail.objects.create(
        message_id=message_id,
        in_reply_to=normalize_message_id(msg.get("In-Reply-To", "")),
        references=normalize_references(msg.get("References", "")),
        sender_email=sender_email or "",
        sender_name=sender_name or "",
        recipient_email=recipient,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        raw_headers="\n".join(f"{k}: {v}" for k, v in msg.items()),
        direction=InboundEmail.Direction.INBOUND,
        sender_type=InboundEmail.SenderType.CUSTOMER,
    )
    _save_attachments(inbound, msg)

    # Mark the mailbox copy as read BEFORE queueing, so a retry of the
    # Celery task doesn't double-pick the same UID.
    _mark_seen(conn, uid)

    try:
        from apps.inbound_email.tasks import process_inbound_email_task

        process_inbound_email_task.delay(str(inbound.pk))
    except Exception:
        logger.exception(
            "IMAP ingest: failed to queue processing for InboundEmail %s", inbound.pk,
        )

    logger.info(
        "IMAP ingested uid=%s from=%s subject=%r → InboundEmail %s",
        uid.decode() if isinstance(uid, bytes) else uid,
        sender_email,
        subject[:80],
        inbound.pk,
    )
    return True


def _mark_seen(conn, uid):
    """Flag the message as \\Seen. No longer load-bearing (we track by UID
    watermark now), but keeps the mailbox visually tidy for operators."""
    try:
        conn.uid("STORE", uid, "+FLAGS", "\\Seen")
    except Exception:
        logger.exception("Failed to mark IMAP uid=%s as seen", uid)


# ---------------------------------------------------------------------------
# MIME helpers (parallel to smtp_server._extract_bodies)
# ---------------------------------------------------------------------------


def _extract_bodies(msg):
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
                text_body = _safe_content(part)
            elif ctype == "text/html" and not html_body:
                html_body = _safe_content(part)
    else:
        content = _safe_content(msg)
        if msg.get_content_type() == "text/html":
            html_body = content
        else:
            text_body = content
    return text_body or "", html_body or ""


def _safe_content(part):
    try:
        return part.get_content()
    except (LookupError, UnicodeDecodeError, AttributeError):
        payload = part.get_payload(decode=True) or b""
        if isinstance(payload, bytes):
            return payload.decode(errors="replace")
        return str(payload)


def _save_attachments(inbound, msg):
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
            logger.exception("Failed to decode IMAP attachment %s", filename)
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
            logger.exception("Failed to save IMAP attachment %s", filename)
    if metadata:
        inbound.attachment_metadata = metadata
        inbound.save(update_fields=["attachment_metadata", "updated_at"])
