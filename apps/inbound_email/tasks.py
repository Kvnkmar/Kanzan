"""
Celery tasks for inbound email processing.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, acks_late=True)
def fetch_inbound_emails_task(self):
    """
    Poll the configured IMAP mailbox for unread messages.

    Each message is converted into an ``InboundEmail`` row and queued for
    async processing by ``process_inbound_email_task``, matching the same
    pipeline used by the in-process SMTP server.

    Scheduled by Celery Beat every 60s. Silently no-ops if IMAP settings
    are not configured.
    """
    from apps.inbound_email.imap_poller import poll_once

    try:
        count = poll_once()
        if count:
            logger.info("IMAP poll ingested %d message(s).", count)
        return count
    except Exception:
        logger.exception("IMAP poll failed")
        raise


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def process_inbound_email_task(self, inbound_email_id):
    """
    Process an inbound email asynchronously.

    Retries up to 3 times with 30s delay on failure.
    """
    from apps.inbound_email.services import process_inbound_email

    try:
        process_inbound_email(str(inbound_email_id))
    except Exception as exc:
        logger.exception(
            "Failed to process inbound email %s (attempt %d/%d)",
            inbound_email_id,
            self.request.retries + 1,
            self.max_retries + 1,
        )
        raise self.retry(exc=exc)
