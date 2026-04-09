"""
Inbound email filters for loop detection, auto-replies, bounces, and spam.

These filters run BEFORE tenant resolution to reject emails that
should never create tickets, regardless of tenant. Each filter
returns (should_reject: bool, reason: str).

The ``classify_email`` function additionally labels the email as one of
"bounce", "auto_reply", or "legitimate" so the pipeline can handle
bounces differently (write BounceLog, flag Contact) from auto-replies
(silently reject).
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

# Bounce-specific sender prefixes (subset of noreply).
_BOUNCE_PREFIXES = frozenset({
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

# Subject patterns that indicate a hard bounce / DSN.
_BOUNCE_SUBJECT_PATTERNS = [
    r"^undeliverable:",
    r"^delivery (status )?notification",
    r"^mail delivery (failed|failure)",
    r"^returned mail:",
    r"^failure notice",
]

# Subject patterns that indicate an auto-reply (not a bounce).
_AUTO_REPLY_SUBJECT_PATTERNS = [
    r"^(auto|automatic)\s*(reply|response)",
    r"^out of (the\s+)?office",
    r"^ooo:",
]


def check_loop(inbound):
    """
    Detect emails sent by our own system to prevent infinite loops.
    """
    sender = (inbound.sender_email or "").lower().strip()
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "").lower().strip()

    if from_email and sender == from_email:
        return True, f"Sender matches system outbound address: {sender}"

    return False, ""


def check_noreply_sender(inbound):
    """
    Reject emails from known noreply/mailer-daemon addresses.
    """
    sender = (inbound.sender_email or "").lower().strip()

    for prefix in _NOREPLY_PREFIXES:
        if sender.startswith(prefix):
            return True, f"Sender is a noreply address: {sender}"

    return False, ""


def check_auto_reply_headers(inbound):
    """
    Detect auto-reply emails via RFC 3834 and vendor-specific headers.
    """
    raw = inbound.raw_headers or ""
    if not raw:
        return False, ""

    # RFC 3834: Auto-Submitted
    auto_submitted = extract_header(raw, "Auto-Submitted")
    if auto_submitted and auto_submitted.lower() != "no":
        return True, f"Auto-Submitted header: {auto_submitted}"

    # Vendor-specific auto-reply headers
    for header_name in _AUTO_REPLY_HEADERS[1:]:
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
    Detect auto-reply / bounce patterns in the subject line.
    """
    subject = (inbound.subject or "").strip().lower()
    if not subject:
        return False, ""

    all_patterns = _BOUNCE_SUBJECT_PATTERNS + _AUTO_REPLY_SUBJECT_PATTERNS

    for pattern in all_patterns:
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


def classify_email(inbound):
    """
    Classify an inbound email as "bounce", "auto_reply", or "legitimate".

    This is called AFTER ``run_all_filters`` has already rejected the
    email. It examines the same signals to determine whether the
    rejection was a hard bounce (which needs a BounceLog + Contact flag)
    or a soft auto-reply (which is silently dropped).

    Returns one of: ``"bounce"``, ``"auto_reply"``, ``"loop"``.
    """
    sender = (inbound.sender_email or "").lower().strip()

    # Loop detection
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "").lower().strip()
    if from_email and sender == from_email:
        return "loop"

    # Bounce-specific sender addresses
    for prefix in _BOUNCE_PREFIXES:
        if sender.startswith(prefix):
            return "bounce"

    # X-Failed-Recipients header is a strong bounce signal
    raw = inbound.raw_headers or ""
    if raw:
        failed_recip = extract_header(raw, "X-Failed-Recipients")
        if failed_recip:
            return "bounce"

    # Bounce subject patterns
    subject = (inbound.subject or "").strip().lower()
    if subject:
        for pattern in _BOUNCE_SUBJECT_PATTERNS:
            if re.match(pattern, subject):
                return "bounce"

    # Everything else that was rejected is an auto-reply
    return "auto_reply"
