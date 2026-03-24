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

    subject = f"[{notification.tenant.name}] {notification.title}"

    # Attempt to render an HTML template; fall back to plain text.
    context = {
        "notification": notification,
        "recipient": recipient,
        "tenant": notification.tenant,
    }
    try:
        html_body = render_to_string(
            "notifications/email/notification.html",
            context,
        )
    except Exception:
        html_body = None

    try:
        plain_body = render_to_string(
            "notifications/email/notification.txt",
            context,
        )
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

    deleted_count, _ = Notification.unscoped.filter(
        is_read=True,
        read_at__lt=cutoff,
    ).delete()

    logger.info(
        "Cleaned up %d read notifications older than %d days.",
        deleted_count,
        days,
    )

    return deleted_count
