"""
Inbound email filters for loop detection, auto-replies, and spam.

These filters run BEFORE tenant resolution to reject emails that
should never create tickets, regardless of tenant. Each filter
returns (should_reject: bool, reason: str).
"""

import logging
import re

from django.conf import settings

from apps.inbound_email.utils import extract_header

logger = logging.getLogger(__name__)

# Addresses that should never create tickets or replies.
_NOREPLY_PREFIXES = frozenset({
    "noreply@",
    "no-reply@",
    "no_reply@",
    "do-not-reply@",
    "donotreply@",
    "mailer-daemon@",
    "postmaster@",
    "mailerdaemon@",
})

# Auto-reply header names (RFC 3834 and common vendor extensions).
_AUTO_REPLY_HEADERS = (
    "Auto-Submitted",
    "X-Auto-Response-Suppress",
    "X-Autoreply",
    "X-Autorespond",
    "X-Mail-Autoreply",
)

# Precedence header values that indicate bulk/automated mail.
_BULK_PRECEDENCE = frozenset({"bulk", "junk", "list", "auto_reply"})


def check_loop(inbound):
    """
    Detect emails sent by our own system to prevent infinite loops.

    If the system sends an outbound email (e.g., ticket confirmation)
    and it bounces back through the inbound webhook, this filter
    catches it before it creates another ticket.

    Args:
        inbound: An InboundEmail instance (unsaved fields are fine).

    Returns:
        (should_reject, reason) tuple.
    """
    sender = (inbound.sender_email or "").lower().strip()
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "").lower().strip()

    if from_email and sender == from_email:
        return True, f"Sender matches system outbound address: {sender}"

    return False, ""


def check_noreply_sender(inbound):
    """
    Reject emails from known noreply/mailer-daemon addresses.

    These are automated system emails (bounce notifications, noreply
    confirmations) that should never create tickets.
    """
    sender = (inbound.sender_email or "").lower().strip()

    for prefix in _NOREPLY_PREFIXES:
        if sender.startswith(prefix):
            return True, f"Sender is a noreply address: {sender}"

    return False, ""


def check_auto_reply_headers(inbound):
    """
    Detect auto-reply emails via RFC 3834 and vendor-specific headers.

    Checks:
    - Auto-Submitted header (RFC 3834): reject if not "no"
    - X-Auto-Response-Suppress: any value means auto-reply
    - X-Autoreply / X-Autorespond / X-Mail-Autoreply: presence means auto-reply
    - Precedence: bulk/junk/list
    """
    raw = inbound.raw_headers or ""
    if not raw:
        return False, ""

    # RFC 3834: Auto-Submitted
    auto_submitted = extract_header(raw, "Auto-Submitted")
    if auto_submitted and auto_submitted.lower() != "no":
        return True, f"Auto-Submitted header: {auto_submitted}"

    # Vendor-specific auto-reply headers
    for header_name in _AUTO_REPLY_HEADERS[1:]:  # skip Auto-Submitted, already checked
        value = extract_header(raw, header_name)
        if value:
            return True, f"{header_name} header present: {value}"

    # Precedence header
    precedence = extract_header(raw, "Precedence").lower()
    if precedence in _BULK_PRECEDENCE:
        return True, f"Precedence header: {precedence}"

    return False, ""


def check_subject_auto_reply(inbound):
    """
    Detect auto-reply patterns in the subject line.

    Catches "Out of Office", "Automatic reply", "Auto:", etc.
    This is a fallback for email clients that don't set proper headers.
    """
    subject = (inbound.subject or "").strip().lower()
    if not subject:
        return False, ""

    auto_patterns = [
        r"^(auto|automatic)\s*(reply|response)",
        r"^out of (the\s+)?office",
        r"^ooo:",
        r"^undeliverable:",
        r"^delivery (status )?notification",
        r"^mail delivery (failed|failure)",
        r"^returned mail:",
        r"^failure notice",
    ]

    for pattern in auto_patterns:
        if re.match(pattern, subject):
            return True, f"Subject matches auto-reply pattern: {inbound.subject[:80]}"

    return False, ""


def run_all_filters(inbound):
    """
    Run all inbound email filters in order.

    Returns (should_reject, reason). Short-circuits on first rejection.
    The order is intentional: cheapest checks first.
    """
    filters = [
        check_loop,
        check_noreply_sender,
        check_auto_reply_headers,
        check_subject_auto_reply,
    ]

    for filter_fn in filters:
        should_reject, reason = filter_fn(inbound)
        if should_reject:
            logger.info(
                "Inbound email %s rejected by filter %s: %s",
                inbound.pk,
                filter_fn.__name__,
                reason,
            )
            return True, reason

    return False, ""
