"""
Celery tasks for the API-keys app.

The only task here is the "API key created" notification email, sent to the
admin who minted the key. Routed to the ``crm_email`` queue via the
``apps.api_keys.tasks.send_email_*`` route added in ``main/celery.py``.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from main.context import tenant_context

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_api_key_created_email_task(self, api_key_id, recipient_email):
    """
    Plain-text email to ``recipient_email`` confirming a new API key was minted.

    The cleartext secret is **deliberately not included** — by the time this
    task runs, only the SHA-512 hash + prefix are reachable. The body explains
    who minted it, when, the prefix for forensics, and how to revoke.

    Silently no-ops if ``recipient_email`` is empty.
    """
    if not recipient_email:
        return

    from apps.api_keys.models import APIKey

    try:
        key = APIKey.unscoped.select_related("tenant", "created_by", "role").get(pk=api_key_id)
    except APIKey.DoesNotExist:
        logger.error("send_api_key_created_email_task: APIKey %s not found.", api_key_id)
        return

    tenant = key.tenant
    created_by = key.created_by
    minted_by = created_by.email if created_by else "(unknown)"

    subject = f"New API key created: {key.name}"
    body_lines = [
        f"A new CRM API key was just minted for tenant '{tenant.name}'.",
        "",
        f"  Name:      {key.name}",
        f"  Prefix:    {key.prefix}…",
        f"  Role:      {key.role.name if key.role_id else '(unknown)'}",
        f"  Minted by: {minted_by}",
        f"  Minted at: {key.created_at.isoformat()}",
        "",
        "The cleartext secret is shown only once at creation — it is NOT included",
        "in this email. If you did not mint this key, revoke it immediately at:",
        "",
        "  /settings/#apiKeysPane",
        "",
        "— CRM",
    ]
    body = "\n".join(body_lines)

    with tenant_context(tenant):
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[recipient_email],
                fail_silently=False,
            )
        except Exception as exc:  # pragma: no cover — relies on Celery infra
            logger.exception(
                "Failed to send API-key creation email for key %s (attempt %d/%d)",
                api_key_id, self.request.retries + 1, self.max_retries + 1,
            )
            raise self.retry(exc=exc)
