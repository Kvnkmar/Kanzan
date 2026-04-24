"""
Outbound email service for ticket communications.

Provides a single entry point (``send_ticket_email``) for all outbound
ticket emails: agent replies, ticket created confirmations, and ad-hoc
agent emails from the UI.

All outbound emails:
- Include [#N] in the subject for inbound threading
- Set Reply-To to the tenant's inbound address
- Set proper In-Reply-To / References headers for the email thread
- Record an outbound InboundEmail record for Message-ID tracking
- Are dispatched asynchronously via Celery (transaction.on_commit)

Legacy functions (``send_ticket_reply_email``, ``send_ticket_created_email``)
are preserved for backward compatibility with existing Celery tasks and
signal handlers. They delegate to ``send_ticket_email`` internally.
"""

import logging
import uuid

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError
from django.template.loader import render_to_string

from apps.inbound_email.models import InboundEmail
from apps.inbound_email.threading import build_thread_headers
from apps.inbound_email.utils import normalize_message_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------


def get_reply_to_address(tenant):
    """
    Get the reply-to address for outbound emails.

    Uses the tenant's configured inbound email address, or falls back
    to the plus-addressing pattern: support+{slug}@{domain}.
    """
    if hasattr(tenant, "settings") and tenant.settings.inbound_email_address:
        return tenant.settings.inbound_email_address
    base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
    return f"support+{tenant.slug}@{base_domain}"


def get_from_address(tenant, display_name=None):
    """
    Get the From address for outbound emails.

    If ``display_name`` is provided (e.g. the replying agent's name),
    it is used as the RFC 5322 display-name so the customer sees
    "Nihan <support@...>" instead of "{Tenant} <support@...>".
    Falls back to "{Tenant Name} Support" when no agent is known
    (e.g. the automated ticket-created confirmation).
    """
    from email.utils import formataddr

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@kanzen.local")
    name = (display_name or "").strip() or f"{tenant.name} Support"
    return formataddr((name, from_email))


def get_ticket_url(tenant, ticket):
    """Build the full URL for a ticket."""
    base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
    port = getattr(settings, "BASE_PORT", "8001")
    scheme = "https" if not settings.DEBUG else "http"

    if tenant.domain:
        host = tenant.domain
    else:
        host = f"{tenant.slug}.{base_domain}"
        if settings.DEBUG and port:
            host = f"{host}:{port}"

    return f"{scheme}://{host}/tickets/{ticket.number}/"


def generate_message_id(tenant, ticket):
    """
    Generate a unique Message-ID for outbound emails.

    Returns the raw ID without angle brackets. Callers must wrap
    in <> when setting the Message-ID header.
    """
    base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
    unique = uuid.uuid4().hex[:12]
    return f"ticket-{ticket.number}-{unique}@{tenant.slug}.{base_domain}"


# ---------------------------------------------------------------------------
# Outbound record persistence
# ---------------------------------------------------------------------------


def record_outbound_email(
    tenant,
    ticket,
    message_id,
    recipient_email,
    subject,
    body_text="",
    sender_type=InboundEmail.SenderType.SYSTEM,
):
    """
    Create an InboundEmail record for an outbound email.

    This record serves two purposes:
    1. Message-ID storage so inbound replies can be threaded back
    2. Audit trail of all emails sent from the system

    Args:
        tenant: The Tenant instance.
        ticket: The Ticket instance this email is about.
        message_id: The raw Message-ID (without angle brackets).
        recipient_email: The email address the message was sent to.
        subject: The email subject line.
        body_text: The plain-text email body.
        sender_type: Who sent it ("system" or "agent").

    Returns:
        The created InboundEmail record, or None if creation failed.
    """
    idem_key = f"out:{tenant.pk}:{ticket.pk}:{message_id}"

    try:
        record = InboundEmail.objects.create(
            tenant=tenant,
            message_id=normalize_message_id(message_id),
            sender_email=settings.DEFAULT_FROM_EMAIL,
            recipient_email=recipient_email,
            subject=subject,
            body_text=body_text,
            direction=InboundEmail.Direction.OUTBOUND,
            sender_type=sender_type,
            status=InboundEmail.Status.SENT,
            ticket=ticket,
            idempotency_key=idem_key,
        )
        return record
    except IntegrityError:
        logger.warning(
            "Outbound email record already exists for message_id %s "
            "(ticket #%d, tenant %s). Skipping duplicate.",
            message_id,
            ticket.number,
            tenant.slug,
        )
        return None
    except Exception:
        logger.exception(
            "Failed to record outbound email for ticket #%d (message_id: %s)",
            ticket.number,
            message_id,
        )
        raise


# ---------------------------------------------------------------------------
# Core send function
# ---------------------------------------------------------------------------


def send_ticket_email(
    tenant,
    ticket,
    to_email,
    subject,
    body_text,
    body_html=None,
    sender_type=InboundEmail.SenderType.SYSTEM,
    from_name=None,
):
    """
    Single entry point for sending all outbound ticket emails.

    Builds threading headers, sends via Django email backend, and
    records the outbound Message-ID for inbound reply matching.

    Args:
        tenant: The Tenant instance.
        ticket: The Ticket instance.
        to_email: Recipient email address.
        subject: Email subject (will NOT be modified — caller must
                 include [#N] if needed).
        body_text: Plain text body.
        body_html: Optional HTML body (attached as alternative).
        sender_type: "system" for automated, "agent" for manual sends.

    Returns:
        The raw message_id string (without angle brackets) on success.

    Raises:
        Exception: If the email fails to send (caller should handle).
    """
    # Skip obviously-undeliverable recipients so we don't generate bounces
    # for seed accounts (*.local) or RFC 2606 test addresses.
    from apps.notifications.utils import is_undeliverable_email

    if is_undeliverable_email(to_email):
        logger.info(
            "Skipping ticket email to undeliverable address %s (ticket #%s).",
            to_email, ticket.number,
        )
        return None

    raw_message_id = generate_message_id(tenant, ticket)
    reply_to = get_reply_to_address(tenant)
    from_address = get_from_address(tenant, display_name=from_name)

    # Build threading headers (In-Reply-To, References)
    thread_headers = build_thread_headers(tenant, ticket, raw_message_id)

    # Add tenant metadata headers
    thread_headers["X-Ticket-Number"] = str(ticket.number)
    thread_headers["X-Tenant-Slug"] = tenant.slug

    email = EmailMultiAlternatives(
        subject=subject,
        body=body_text,
        from_email=from_address,
        to=[to_email],
        reply_to=[reply_to],
        headers=thread_headers,
    )
    if body_html:
        email.attach_alternative(body_html, "text/html")

    email.send(fail_silently=False)

    logger.info(
        "Sent email for ticket #%d to %s (type=%s, message_id=%s)",
        ticket.number,
        to_email,
        sender_type,
        raw_message_id,
    )

    # Record for threading
    record_outbound_email(
        tenant=tenant,
        ticket=ticket,
        message_id=raw_message_id,
        recipient_email=to_email,
        subject=subject,
        body_text=body_text,
        sender_type=sender_type,
    )

    return raw_message_id


# ---------------------------------------------------------------------------
# Template-based convenience functions
# ---------------------------------------------------------------------------


def send_ticket_reply_email(ticket, comment_body, agent_name, tenant):
    """
    Send a reply notification email to the ticket's contact.

    This is the legacy interface used by the Celery task
    ``send_ticket_reply_email_task``. It renders the reply template
    and delegates to ``send_ticket_email``.

    Returns True if sent, False if skipped (no contact).
    """
    contact = ticket.contact
    if not contact or not contact.email:
        logger.debug(
            "Ticket #%d has no contact email; skipping reply notification.",
            ticket.number,
        )
        return False

    subject = f"Re: [#{ticket.number}] {ticket.subject}"
    ticket_url = get_ticket_url(tenant, ticket)

    context = {
        "ticket": ticket,
        "tenant": tenant,
        "agent_name": agent_name,
        "comment_body": comment_body,
        "ticket_url": ticket_url,
    }

    plain_body = render_to_string("tickets/email/reply_notification.txt", context)
    try:
        html_body = render_to_string("tickets/email/reply_notification.html", context)
    except Exception:
        html_body = None

    try:
        send_ticket_email(
            tenant=tenant,
            ticket=ticket,
            to_email=contact.email,
            subject=subject,
            body_text=plain_body,
            body_html=html_body,
            sender_type=InboundEmail.SenderType.AGENT,
            from_name=agent_name,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to send reply email for ticket #%d to %s",
            ticket.number,
            contact.email,
        )
        return False


def send_ticket_created_email(ticket, tenant):
    """
    Send a confirmation email when a ticket is created.

    This is the legacy interface used by the Celery task
    ``send_ticket_created_email_task``. It renders the created
    template and delegates to ``send_ticket_email``.

    Returns True if sent, False if skipped (no contact).
    """
    contact = ticket.contact
    if not contact or not contact.email:
        return False

    subject = f"[#{ticket.number}] {ticket.subject}"
    ticket_url = get_ticket_url(tenant, ticket)

    context = {
        "ticket": ticket,
        "tenant": tenant,
        "ticket_url": ticket_url,
    }

    plain_body = render_to_string("tickets/email/ticket_created.txt", context)
    try:
        html_body = render_to_string("tickets/email/ticket_created.html", context)
    except Exception:
        html_body = None

    assignee_name = ""
    if ticket.assignee_id:
        assignee_name = ticket.assignee.get_full_name() or ticket.assignee.email

    try:
        send_ticket_email(
            tenant=tenant,
            ticket=ticket,
            to_email=contact.email,
            subject=subject,
            body_text=plain_body,
            body_html=html_body,
            sender_type=InboundEmail.SenderType.SYSTEM,
            from_name=assignee_name or None,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to send ticket created email for #%d to %s",
            ticket.number,
            contact.email,
        )
        return False


# ---------------------------------------------------------------------------
# Backward-compatible aliases (used by tests)
# ---------------------------------------------------------------------------

_get_reply_to_address = get_reply_to_address
_get_ticket_url = get_ticket_url
