"""
Outbound email service for ticket notifications to contacts.

Sends emails to the ticket's contact when:
1. A ticket is created (confirmation email)
2. An agent replies with a public comment

All outbound emails include [#N] in the subject line so that
customer replies are threaded back via the inbound email system.
The Reply-To header is set to the tenant's inbound email address.
"""

import logging
import uuid

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _get_reply_to_address(tenant):
    """
    Get the reply-to address for outbound emails.

    Uses the tenant's configured inbound email address, or falls back
    to the plus-addressing pattern: support+{slug}@{domain}.
    """
    if hasattr(tenant, "settings") and tenant.settings.inbound_email_address:
        return tenant.settings.inbound_email_address

    base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
    return f"support+{tenant.slug}@{base_domain}"


def _get_from_address(tenant):
    """
    Get the From address for outbound emails.

    Uses the tenant name as the display name with the default from email.
    """
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@kanzan.local")
    return f"{tenant.name} <{from_email}>"


def _get_ticket_url(tenant, ticket):
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


def _generate_message_id(tenant, ticket):
    """Generate a unique Message-ID for outbound emails."""
    base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
    unique = uuid.uuid4().hex[:12]
    return f"<ticket-{ticket.number}-{unique}@{tenant.slug}.{base_domain}>"


def send_ticket_reply_email(ticket, comment_body, agent_name, tenant):
    """
    Send an email to the ticket's contact when an agent replies.

    Args:
        ticket: The Ticket instance (with contact pre-fetched).
        comment_body: The text content of the agent's reply.
        agent_name: Display name of the agent who replied.
        tenant: The Tenant instance.

    Returns:
        True if email was sent, False otherwise.
    """
    contact = ticket.contact
    if not contact or not contact.email:
        logger.debug(
            "Ticket #%d has no contact email; skipping reply notification.",
            ticket.number,
        )
        return False

    subject = f"Re: [#{ticket.number}] {ticket.subject}"
    reply_to = _get_reply_to_address(tenant)
    from_address = _get_from_address(tenant)
    ticket_url = _get_ticket_url(tenant, ticket)
    message_id = _generate_message_id(tenant, ticket)

    context = {
        "ticket": ticket,
        "tenant": tenant,
        "agent_name": agent_name,
        "comment_body": comment_body,
        "ticket_url": ticket_url,
    }

    # Render templates
    plain_body = render_to_string(
        "tickets/email/reply_notification.txt", context,
    )
    try:
        html_body = render_to_string(
            "tickets/email/reply_notification.html", context,
        )
    except Exception:
        html_body = None

    email = EmailMultiAlternatives(
        subject=subject,
        body=plain_body,
        from_email=from_address,
        to=[contact.email],
        reply_to=[reply_to],
        headers={
            "Message-ID": message_id,
            "X-Ticket-Number": str(ticket.number),
            "X-Tenant-Slug": tenant.slug,
        },
    )
    if html_body:
        email.attach_alternative(html_body, "text/html")

    try:
        email.send(fail_silently=False)
        logger.info(
            "Sent reply notification for ticket #%d to %s",
            ticket.number, contact.email,
        )

        # Store the outbound message_id so inbound replies can thread
        _record_outbound_message_id(
            tenant, ticket, message_id, contact.email, body_text=plain_body,
        )

        return True
    except Exception:
        logger.exception(
            "Failed to send reply email for ticket #%d to %s",
            ticket.number, contact.email,
        )
        return False


def send_ticket_created_email(ticket, tenant):
    """
    Send a confirmation email to the contact when a ticket is created.

    Args:
        ticket: The Ticket instance.
        tenant: The Tenant instance.

    Returns:
        True if email was sent, False otherwise.
    """
    contact = ticket.contact
    if not contact or not contact.email:
        return False

    subject = f"[#{ticket.number}] {ticket.subject}"
    reply_to = _get_reply_to_address(tenant)
    from_address = _get_from_address(tenant)
    ticket_url = _get_ticket_url(tenant, ticket)
    message_id = _generate_message_id(tenant, ticket)

    context = {
        "ticket": ticket,
        "tenant": tenant,
        "ticket_url": ticket_url,
    }

    plain_body = render_to_string(
        "tickets/email/ticket_created.txt", context,
    )
    try:
        html_body = render_to_string(
            "tickets/email/ticket_created.html", context,
        )
    except Exception:
        html_body = None

    email = EmailMultiAlternatives(
        subject=subject,
        body=plain_body,
        from_email=from_address,
        to=[contact.email],
        reply_to=[reply_to],
        headers={
            "Message-ID": message_id,
            "X-Ticket-Number": str(ticket.number),
            "X-Tenant-Slug": tenant.slug,
        },
    )
    if html_body:
        email.attach_alternative(html_body, "text/html")

    try:
        email.send(fail_silently=False)
        logger.info(
            "Sent ticket created notification for #%d to %s",
            ticket.number, contact.email,
        )
        _record_outbound_message_id(
            tenant, ticket, message_id, contact.email, body_text=plain_body,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to send ticket created email for #%d to %s",
            ticket.number, contact.email,
        )
        return False


def _record_outbound_message_id(
    tenant, ticket, message_id, recipient_email, body_text=""
):
    """
    Store the outbound Message-ID as an InboundEmail record so that
    when the customer replies, the In-Reply-To / References headers
    can be matched back to this ticket.
    """
    try:
        from apps.inbound_email.models import InboundEmail

        InboundEmail.objects.create(
            tenant=tenant,
            message_id=message_id.strip("<>"),
            sender_email=settings.DEFAULT_FROM_EMAIL,
            recipient_email=recipient_email,
            subject=f"[#{ticket.number}] {ticket.subject}",
            body_text=body_text,
            status=InboundEmail.Status.REPLY_ADDED,
            ticket=ticket,
        )
    except Exception:
        logger.exception(
            "Failed to record outbound message_id for ticket #%d",
            ticket.number,
        )
