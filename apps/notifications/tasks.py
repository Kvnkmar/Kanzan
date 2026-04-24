"""
Celery tasks for the notifications app.

Tasks:
- ``send_notification_email`` -- renders and sends an email for a notification.
- ``cleanup_old_notifications`` -- purges read notifications older than N days.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_notification_email(self, notification_id):
    """
    Send an email for the given notification.

    Loads the ``Notification`` by ID, renders a subject/body from the
    notification fields, and dispatches via Django's email backend.
    Retries up to 3 times on transient failures.

    Args:
        notification_id: UUID string of the ``Notification`` to email.
    """
    from apps.notifications.models import Notification

    try:
        notification = Notification.unscoped.select_related(
            "recipient", "tenant"
        ).get(id=notification_id)
    except Notification.DoesNotExist:
        logger.error(
            "Notification %s not found; cannot send email.",
            notification_id,
        )
        return

    recipient = notification.recipient
    if not recipient.email:
        logger.warning(
            "Recipient %s has no email address; skipping notification %s.",
            recipient.id,
            notification_id,
        )
        return

    # Skip addresses we know will bounce (seeded *.local accounts, RFC 2606
    # test domains, etc.). These would just round-trip through our own
    # inbox as DSNs, wasting retries and cluttering the bounce log.
    from apps.notifications.utils import is_undeliverable_email

    if is_undeliverable_email(recipient.email):
        logger.info(
            "Skipping notification %s: recipient %s has an undeliverable address (%s).",
            notification_id, recipient.id, recipient.email,
        )
        return

    # Resolve the ticket (if any) so we can link the outbound record and
    # build an absolute CTA URL for the email body. ``notification.data``
    # stores ``ticket_id`` for all ticket-related notification types.
    ticket = None
    ticket_id = (notification.data or {}).get("ticket_id")
    if ticket_id:
        from apps.tickets.models import Ticket

        ticket = Ticket.unscoped.filter(
            pk=ticket_id, tenant=notification.tenant,
        ).first()

    ticket_number = ticket.number if ticket else None
    if ticket_number is not None:
        subject = (
            f"[{notification.tenant.name} #{ticket_number}] {notification.title}"
        )
    else:
        subject = f"[{notification.tenant.name}] {notification.title}"

    absolute_url = _absolute_url(
        notification.tenant, (notification.data or {}).get("url", ""),
    )

    context = {
        "notification": notification,
        "recipient": recipient,
        "tenant": notification.tenant,
        "ticket": ticket,
        "cta_url": absolute_url,
    }

    # Use dedicated templates for specific notification types.
    html_template = "notifications/email/notification.html"
    txt_template = "notifications/email/notification.txt"

    if (
        notification.type == "kb_article_reviewed"
        and notification.data.get("action") == "rejected"
    ):
        html_template = "knowledge/email/article_rejected.html"
        txt_template = "knowledge/email/article_rejected.txt"
        context.update({
            "article_title": notification.data.get("article_title", ""),
            "rejection_reason": notification.data.get("reason", ""),
            "reviewer_name": notification.data.get("reviewer_name", ""),
            "reviewed_at": notification.data.get("reviewed_at", ""),
            "article_url": notification.data.get("url", ""),
        })

    try:
        html_body = render_to_string(html_template, context)
    except Exception:
        html_body = None

    try:
        plain_body = render_to_string(txt_template, context)
    except Exception:
        plain_body = notification.body or notification.title

    try:
        send_mail(
            subject=subject,
            message=plain_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient.email],
            html_message=html_body,
            fail_silently=False,
        )
        logger.info(
            "Sent notification email to %s for notification %s.",
            recipient.email,
            notification_id,
        )
    except Exception as exc:
        logger.exception(
            "Failed to send email for notification %s to %s.",
            notification_id,
            recipient.email,
        )
        raise self.retry(exc=exc)

    # Log the send to the email log so it shows up on /inbound-email/.
    # Pass ``ticket`` so the inbox shows the linked ticket badge instead
    # of "Unlinked" for ticket-related notifications.
    try:
        from apps.inbound_email.services import log_outbound_email

        log_outbound_email(
            tenant=notification.tenant,
            recipient_email=recipient.email,
            subject=subject,
            body_text=plain_body or "",
            ticket=ticket,
        )
    except Exception:
        logger.exception(
            "Failed to record outbound log for notification %s", notification_id,
        )


def _absolute_url(tenant, path):
    """
    Resolve a relative path like ``/tickets/93`` into a full URL using the
    tenant's domain (or ``{slug}.{BASE_DOMAIN}`` fallback). Returns an empty
    string when ``path`` is empty; returns ``path`` unchanged if it already
    looks absolute.
    """
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path

    base_domain = getattr(settings, "BASE_DOMAIN", "localhost")
    port = getattr(settings, "BASE_PORT", "8001")
    scheme = "https" if not settings.DEBUG else "http"

    if getattr(tenant, "domain", ""):
        host = tenant.domain
    else:
        host = f"{tenant.slug}.{base_domain}"
        if settings.DEBUG and port:
            host = f"{host}:{port}"

    if not path.startswith("/"):
        path = "/" + path
    return f"{scheme}://{host}{path}"


@shared_task(
    bind=True,
    acks_late=True,
)
def cleanup_old_notifications(self, days=90):
    """
    Delete read notifications older than ``days`` days.

    Intended to be run periodically via Celery Beat to prevent the
    notifications table from growing unboundedly.

    Args:
        days: Number of days to retain read notifications (default 90).
    """
    from apps.notifications.models import Notification

    cutoff = timezone.now() - timedelta(days=days)
    batch_size = 1000
    total_deleted = 0

    while True:
        ids = list(
            Notification.unscoped.filter(
                is_read=True,
                read_at__lt=cutoff,
            ).values_list("id", flat=True)[:batch_size]
        )
        if not ids:
            break
        count, _ = Notification.unscoped.filter(id__in=ids).delete()
        total_deleted += count

    logger.info(
        "Cleaned up %d read notifications older than %d days.",
        total_deleted,
        days,
    )

    return total_deleted
