"""
Webhook delivery service for ticket events.

Dispatches HTTP POST requests to configured webhook endpoints when ticket
events occur. Includes HMAC signature verification, retry logic, and
auto-disable after repeated failures.
"""

import hashlib
import hmac
import json
import logging

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

# Auto-disable webhook after this many consecutive failures
MAX_FAILURE_COUNT = 10
DELIVERY_TIMEOUT = 10  # seconds


def deliver_webhook(webhook, payload):
    """
    Deliver a single webhook payload.

    Returns:
        tuple: (success: bool, status_code: int | None)
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Kanzan-Webhook/1.0",
        "X-Webhook-ID": str(webhook.pk),
    }

    # Merge custom headers
    if webhook.headers and isinstance(webhook.headers, dict):
        headers.update(webhook.headers)

    body = json.dumps(payload, default=str)

    # HMAC signature
    if webhook.secret:
        signature = hmac.new(
            webhook.secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Webhook-Signature"] = f"sha256={signature}"

    try:
        resp = requests.post(
            webhook.url,
            data=body,
            headers=headers,
            timeout=DELIVERY_TIMEOUT,
        )
        status_code = resp.status_code
        success = 200 <= status_code < 300

        # Update webhook state
        webhook.last_triggered_at = timezone.now()
        webhook.last_status_code = status_code
        if success:
            webhook.failure_count = 0
        else:
            webhook.failure_count += 1
        webhook.save(update_fields=[
            "last_triggered_at", "last_status_code", "failure_count", "updated_at",
        ])

        if not success:
            logger.warning(
                "Webhook %s delivery failed (status=%d, failures=%d): %s",
                webhook.name, status_code, webhook.failure_count, webhook.url,
            )

        # Auto-disable after too many failures
        if webhook.failure_count >= MAX_FAILURE_COUNT:
            webhook.is_active = False
            webhook.save(update_fields=["is_active", "updated_at"])
            logger.error(
                "Webhook %s auto-disabled after %d consecutive failures.",
                webhook.name, webhook.failure_count,
            )

        return success, status_code

    except requests.RequestException as exc:
        webhook.last_triggered_at = timezone.now()
        webhook.last_status_code = None
        webhook.failure_count += 1
        webhook.save(update_fields=[
            "last_triggered_at", "last_status_code", "failure_count", "updated_at",
        ])

        if webhook.failure_count >= MAX_FAILURE_COUNT:
            webhook.is_active = False
            webhook.save(update_fields=["is_active", "updated_at"])

        logger.exception(
            "Webhook %s delivery error (failures=%d): %s",
            webhook.name, webhook.failure_count, exc,
        )
        return False, None


def fire_webhooks(tenant, event_type, data):
    """
    Fire all active webhooks for a tenant that subscribe to the given event.

    Called from signal handlers and service functions after ticket events.
    Dispatches asynchronously via Celery to avoid blocking the request.
    """
    from apps.tickets.models import Webhook

    webhooks = Webhook.unscoped.filter(
        tenant=tenant,
        is_active=True,
    )

    matching = [
        w for w in webhooks
        if event_type in (w.events or [])
    ]

    if not matching:
        return

    payload = {
        "event": event_type,
        "timestamp": timezone.now().isoformat(),
        "tenant_id": str(tenant.pk),
        "data": data,
    }

    # Dispatch via Celery task for async delivery
    from apps.tickets.tasks import deliver_webhook_task

    for webhook in matching:
        deliver_webhook_task.delay(str(webhook.pk), payload)
