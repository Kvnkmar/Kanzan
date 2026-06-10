"""Celery tasks for the Inbox Hub.

``check_hub_sla_breaches`` is the durability layer over auto-assignment: it
watches parked emails against their seeded SLA deadlines and, when a response
deadline passes, flags the breach and escalates to the department lead. A
warning fires once as the deadline approaches (deduped via a marker in
``auto_classification_data`` so we don't need a dedicated column).
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def check_hub_sla_breaches():
    """Flag response/resolution SLA breaches and escalate response breaches.

    Cross-tenant; idempotent per email (breach flags prevent re-firing).
    Returns a summary dict for observability/tests.
    """
    from apps.inbox_hub.models import HubEmail
    from apps.inbox_hub.services import escalate_hub_email

    now = timezone.now()
    warning_window = timedelta(minutes=getattr(settings, "HUB_SLA_WARNING_MINUTES", 15))
    active_states = [
        HubEmail.State.NEW,
        HubEmail.State.ASSIGNED,
        HubEmail.State.IN_PROGRESS,
        HubEmail.State.PENDING_AGENT,
        HubEmail.State.ESCALATED,
    ]

    qs = (
        HubEmail.unscoped
        .filter(state__in=active_states, sla_response_due_at__isnull=False)
        .select_related("tenant", "assignee", "department")
    )

    breached = warned = res_breached = 0
    for he in qs.iterator(chunk_size=200):
        # --- Response SLA ---
        if not he.response_breached and he.first_responded_at is None:
            if now >= he.sla_response_due_at:
                HubEmail.unscoped.filter(pk=he.pk).update(response_breached=True, updated_at=now)
                he.response_breached = True
                _notify_sla(he, breached=True)
                try:
                    escalate_hub_email(he, actor=None, reason="SLA response breached")
                except Exception:
                    logger.exception("Auto-escalate on SLA breach failed for %s", he.pk)
                breached += 1
            elif (he.sla_response_due_at - now) <= warning_window:
                data = he.auto_classification_data or {}
                if not data.get("sla_warning_sent"):
                    data["sla_warning_sent"] = True
                    HubEmail.unscoped.filter(pk=he.pk).update(
                        auto_classification_data=data, updated_at=now,
                    )
                    _notify_sla(he, breached=False)
                    warned += 1

        # --- Resolution SLA (flag only; no auto-escalate) ---
        if (
            not he.resolution_breached
            and he.sla_resolution_due_at is not None
            and now >= he.sla_resolution_due_at
        ):
            HubEmail.unscoped.filter(pk=he.pk).update(resolution_breached=True, updated_at=now)
            res_breached += 1

    if breached or warned or res_breached:
        logger.info(
            "check_hub_sla_breaches: %d response breach(es), %d warning(s), %d resolution breach(es).",
            breached, warned, res_breached,
        )
    return {"response_breached": breached, "warned": warned, "resolution_breached": res_breached}


def _notify_sla(hub_email, *, breached):
    """Notify the assignee (and department lead) of an SLA event."""
    from apps.notifications.models import NotificationType
    from apps.notifications.services import send_notification

    tenant = hub_email.tenant
    n_type = (
        NotificationType.HUB_EMAIL_SLA_BREACHED if breached
        else NotificationType.HUB_EMAIL_SLA_BREACH_WARNING
    )
    title = "Email SLA breached" if breached else "Email SLA breach approaching"
    try:
        subject = hub_email.inbound.subject or ""
    except Exception:
        subject = ""

    recipients = []
    if hub_email.assignee_id:
        recipients.append(hub_email.assignee)
    lead = hub_email.department.lead if hub_email.department_id else None
    if lead is not None and lead.pk != getattr(hub_email.assignee, "pk", None):
        recipients.append(lead)

    for recipient in recipients:
        try:
            send_notification(
                tenant=tenant,
                recipient=recipient,
                notification_type=n_type,
                title=title,
                body=subject or "(no subject)",
                data={"hub_email_id": str(hub_email.pk), "url": "/inbox-hub/"},
            )
        except Exception:
            logger.exception("Failed to send SLA notification for %s", hub_email.pk)
