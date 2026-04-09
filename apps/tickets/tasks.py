"""
Celery tasks for the tickets app.

Tasks:
    check_sla_breaches
        Periodic scan for SLA violations and escalation rule execution.
        Runs every 2 minutes via Celery Beat.

    check_overdue_tickets
        Periodic scan for tickets past their due_date. Sends TICKET_OVERDUE
        notifications to assignees and admins. Deduplicates per ticket per day.
        Runs every 15 minutes via Celery Beat.
"""

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, acks_late=True)
def check_sla_breaches(self):
    """
    Scan all active tenants for open tickets with SLA breaches.

    For each tenant with active SLA policies:
    1. Find open tickets matching each policy's priority.
    2. Check first-response and resolution deadlines.
    3. Mark breach flags on tickets that have exceeded their SLA.
    4. Send breach notifications (SLA_BREACH type).
    5. Execute matching escalation rules (with dedup via TicketActivity).

    Uses ``Model.unscoped`` throughout since Celery tasks run without
    thread-local tenant context.
    """
    from apps.tenants.models import Tenant

    now = timezone.now()
    active_tenants = (
        Tenant.objects.filter(is_active=True).select_related("settings")
    )

    for tenant in active_tenants:
        try:
            _check_tenant_sla(tenant, now)
        except Exception:
            logger.exception("SLA check failed for tenant %s", tenant.slug)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_tenant_sla(tenant, now):
    """Check SLA breaches for a single tenant."""
    from apps.tickets.models import SLAPolicy, Ticket, TicketStatus

    policies = SLAPolicy.unscoped.filter(tenant=tenant, is_active=True)
    if not policies.exists():
        return

    closed_status_ids = list(
        TicketStatus.unscoped.filter(tenant=tenant, is_closed=True)
        .values_list("id", flat=True)
    )

    tenant_settings = getattr(tenant, "settings", None)

    for policy in policies:
        tickets = (
            Ticket.unscoped.filter(tenant=tenant, priority=policy.priority)
            .exclude(status_id__in=closed_status_ids)
            .select_related("status", "assignee", "created_by")
            .iterator(chunk_size=200)
        )

        for ticket in tickets:
            _check_ticket_sla(ticket, policy, tenant, tenant_settings, now)


def _check_ticket_sla(ticket, policy, tenant, tenant_settings, now):
    """Check a single ticket against its SLA policy and fire escalations."""
    from apps.tickets.sla import get_effective_elapsed_minutes

    update_fields = []

    # Compute pause-adjusted elapsed time once for both checks
    effective_elapsed = get_effective_elapsed_minutes(ticket, policy, tenant, now)

    # --- Response SLA ---
    response_breached = False
    if not ticket.sla_response_breached and ticket.first_responded_at is None:
        if effective_elapsed > policy.first_response_minutes:
            ticket.sla_response_breached = True
            update_fields.append("sla_response_breached")
            response_breached = True

    # --- Resolution SLA ---
    resolution_breached = False
    if not ticket.sla_resolution_breached and ticket.resolved_at is None:
        if effective_elapsed > policy.resolution_minutes:
            ticket.sla_resolution_breached = True
            update_fields.append("sla_resolution_breached")
            resolution_breached = True

    # Persist breach flags BEFORE sending notifications to prevent
    # duplicate notifications on task retry.
    if update_fields:
        update_fields.append("updated_at")
        ticket.save(update_fields=update_fields)

    if response_breached:
        _fire_breach(ticket, tenant, "response_breach", policy)
    if resolution_breached:
        _fire_breach(ticket, tenant, "resolution_breach", policy)

    # --- Escalation rules ---
    _check_escalation_rules(ticket, policy, tenant, tenant_settings, now)


def _elapsed(start_utc, end_utc, policy, tenant_settings):
    """Return elapsed minutes, respecting business_hours_only flag."""
    if policy.business_hours_only and tenant_settings:
        from apps.tickets.sla import elapsed_business_minutes

        return elapsed_business_minutes(start_utc, end_utc, tenant_settings)
    return (end_utc - start_utc).total_seconds() / 60


def _fire_breach(ticket, tenant, breach_type, policy):
    """Send SLA breach notification to assignee and admins."""
    from apps.accounts.models import TenantMembership
    from apps.notifications.models import NotificationType
    from apps.notifications.services import send_notification
    from apps.tickets.models import TicketActivity

    breach_label = "Response" if breach_type == "response_breach" else "Resolution"

    # Notify assignee
    if ticket.assignee:
        send_notification(
            tenant=tenant,
            recipient=ticket.assignee,
            notification_type=NotificationType.SLA_BREACH,
            title=f"SLA {breach_label} Breach: Ticket #{ticket.number}",
            body=(
                f'{breach_label} SLA breached for "{ticket.subject}" '
                f"(policy: {policy.name})."
            ),
            data={
                "ticket_id": str(ticket.id),
                "ticket_number": ticket.number,
                "breach_type": breach_type,
                "policy_name": policy.name,
            },
        )

    # Notify tenant admins (up to 5, excluding assignee)
    admin_memberships = (
        TenantMembership.objects.filter(
            tenant=tenant, is_active=True, role__hierarchy_level__lte=20,
        )
        .select_related("user")
    )
    if ticket.assignee_id:
        admin_memberships = admin_memberships.exclude(user_id=ticket.assignee_id)

    for membership in admin_memberships[:5]:
        send_notification(
            tenant=tenant,
            recipient=membership.user,
            notification_type=NotificationType.SLA_BREACH,
            title=f"SLA {breach_label} Breach: Ticket #{ticket.number}",
            body=f'{breach_label} SLA breached for "{ticket.subject}".',
            data={
                "ticket_id": str(ticket.id),
                "ticket_number": ticket.number,
                "breach_type": breach_type,
            },
        )

    # Log to ticket timeline
    TicketActivity(
        tenant=tenant,
        ticket=ticket,
        actor=None,
        event=TicketActivity.Event.ESCALATED,
        message=f"SLA {breach_label} breached (policy: {policy.name})",
        metadata={
            "breach_type": breach_type,
            "policy_id": str(policy.id),
            "policy_name": policy.name,
        },
    ).save()

    logger.info(
        "SLA %s breach for ticket #%s (tenant: %s, policy: %s)",
        breach_label.lower(),
        ticket.number,
        tenant.slug,
        policy.name,
    )


def _check_escalation_rules(ticket, policy, tenant, tenant_settings, now):
    """
    Execute escalation rules for a ticket.

    Dedup: Before executing a rule, check if a TicketActivity with
    ``event=ESCALATED`` already exists for this ticket with the rule's
    ID in ``metadata.escalation_rule_id``.
    """
    from apps.tickets.models import EscalationRule, TicketActivity

    rules = EscalationRule.unscoped.filter(sla_policy=policy).order_by("order")
    if not rules.exists():
        return

    # Pre-fetch already-fired rule IDs
    fired_rule_ids = set()
    past_escalations = TicketActivity.unscoped.filter(
        ticket=ticket, event=TicketActivity.Event.ESCALATED,
    ).values_list("metadata", flat=True)
    for meta in past_escalations:
        if isinstance(meta, dict) and "escalation_rule_id" in meta:
            fired_rule_ids.add(meta["escalation_rule_id"])

    from apps.tickets.sla import get_effective_elapsed_minutes

    # Pre-compute effective elapsed for breach triggers (pause-aware)
    effective_elapsed = get_effective_elapsed_minutes(ticket, policy, tenant, now)

    for rule in rules:
        rule_id_str = str(rule.id)
        if rule_id_str in fired_rule_ids:
            continue

        # Determine reference time and base SLA minutes for this trigger
        if rule.trigger == EscalationRule.Trigger.RESPONSE_BREACH:
            if ticket.first_responded_at is not None:
                continue
            sla_minutes = policy.first_response_minutes
            elapsed = effective_elapsed
        elif rule.trigger == EscalationRule.Trigger.RESOLUTION_BREACH:
            if ticket.resolved_at is not None:
                continue
            sla_minutes = policy.resolution_minutes
            elapsed = effective_elapsed
        elif rule.trigger == EscalationRule.Trigger.IDLE_TIME:
            # Idle time uses raw elapsed from last update (not pause-adjusted)
            elapsed = _elapsed(ticket.updated_at, now, policy, tenant_settings)
            sla_minutes = 0
        else:
            continue

        # threshold_minutes is added to the SLA target for breach triggers,
        # or stands alone for idle_time
        target = (sla_minutes + rule.threshold_minutes) if sla_minutes else rule.threshold_minutes
        if elapsed < target:
            continue

        _execute_rule(rule, ticket, tenant, now)


@shared_task(bind=True, max_retries=1, acks_late=True)
def check_overdue_tickets(self):
    """
    Periodic scan for overdue tickets across all active tenants.

    For each tenant:
    1. Find open tickets whose ``due_date`` has passed.
    2. Send a TICKET_OVERDUE notification to the assignee.
    3. Notify tenant admins/managers (up to 5).
    4. Dedup: only one overdue notification per ticket per day (checks
       existing Notification records for today).

    Uses ``Model.unscoped`` since Celery tasks lack thread-local tenant
    context.
    """
    from apps.tenants.models import Tenant

    now = timezone.now()
    active_tenants = (
        Tenant.objects.filter(is_active=True).select_related("settings")
    )

    for tenant in active_tenants:
        try:
            _check_tenant_overdue(tenant, now)
        except Exception:
            logger.exception(
                "Overdue ticket check failed for tenant %s", tenant.slug
            )


def _check_tenant_overdue(tenant, now):
    """Send overdue reminders for a single tenant (due_date + follow_up_due_at)."""
    from apps.accounts.models import TenantMembership
    from apps.notifications.models import Notification, NotificationType
    from apps.notifications.services import send_notification
    from apps.tickets.models import Ticket, TicketActivity, TicketStatus

    closed_status_ids = list(
        TicketStatus.unscoped.filter(tenant=tenant, is_closed=True)
        .values_list("id", flat=True)
    )

    # Dedup window: start of today (UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Cache admin memberships for the tenant
    admin_memberships = list(
        TenantMembership.objects.filter(
            tenant=tenant, is_active=True, role__hierarchy_level__lte=20,
        ).select_related("user")[:5]
    )

    # --- Due date overdue ---
    overdue_tickets = (
        Ticket.unscoped.filter(
            tenant=tenant,
            due_date__lt=now,
            due_date__isnull=False,
        )
        .exclude(status_id__in=closed_status_ids)
        .select_related("status", "assignee")
    )

    if overdue_tickets.exists():
        already_notified_ids = set(
            Notification.unscoped.filter(
                tenant=tenant,
                type=NotificationType.TICKET_OVERDUE,
                created_at__gte=today_start,
            )
            .values_list("data__ticket_id", flat=True)
        )

        for ticket in overdue_tickets:
            ticket_id_str = str(ticket.id)
            if ticket_id_str in already_notified_ids:
                continue

            overdue_delta = now - ticket.due_date
            overdue_hours = int(overdue_delta.total_seconds() / 3600)
            if overdue_hours < 1:
                overdue_label = f"{int(overdue_delta.total_seconds() / 60)}m"
            elif overdue_hours < 24:
                overdue_label = f"{overdue_hours}h"
            else:
                overdue_label = f"{overdue_hours // 24}d {overdue_hours % 24}h"

            notif_data = {
                "ticket_id": ticket_id_str,
                "ticket_number": ticket.number,
                "overdue_by": overdue_label,
            }

            if ticket.assignee:
                send_notification(
                    tenant=tenant,
                    recipient=ticket.assignee,
                    notification_type=NotificationType.TICKET_OVERDUE,
                    title=f"Overdue: Ticket #{ticket.number}",
                    body=(
                        f'"{ticket.subject}" is overdue by {overdue_label}. '
                        f"It was due {ticket.due_date.strftime('%b %d, %Y %H:%M')}."
                    ),
                    data=notif_data,
                )

            for membership in admin_memberships:
                if ticket.assignee_id and membership.user_id == ticket.assignee_id:
                    continue
                send_notification(
                    tenant=tenant,
                    recipient=membership.user,
                    notification_type=NotificationType.TICKET_OVERDUE,
                    title=f"Overdue: Ticket #{ticket.number}",
                    body=(
                        f'"{ticket.subject}" assigned to '
                        f"{ticket.assignee.get_full_name() if ticket.assignee else 'Unassigned'} "
                        f"is overdue by {overdue_label}."
                    ),
                    data=notif_data,
                )

            logger.info(
                "Overdue notification sent for ticket #%s (tenant: %s, overdue by: %s)",
                ticket.number,
                tenant.slug,
                overdue_label,
            )

    # --- Follow-up overdue ---
    followup_tickets = (
        Ticket.unscoped.filter(
            tenant=tenant,
            follow_up_due_at__lt=now,
            follow_up_due_at__isnull=False,
        )
        .exclude(status_id__in=closed_status_ids)
        .select_related("status", "assignee")
    )

    if not followup_tickets.exists():
        return

    already_followup_notified_ids = set(
        Notification.unscoped.filter(
            tenant=tenant,
            type=NotificationType.TICKET_FOLLOWUP_OVERDUE,
            created_at__gte=today_start,
        )
        .values_list("data__ticket_id", flat=True)
    )

    for ticket in followup_tickets:
        ticket_id_str = str(ticket.id)
        if ticket_id_str in already_followup_notified_ids:
            continue

        if not ticket.assignee:
            continue

        overdue_delta = now - ticket.follow_up_due_at
        overdue_hours = int(overdue_delta.total_seconds() / 3600)
        if overdue_hours < 1:
            overdue_label = f"{int(overdue_delta.total_seconds() / 60)}m"
        elif overdue_hours < 24:
            overdue_label = f"{overdue_hours}h"
        else:
            overdue_label = f"{overdue_hours // 24}d {overdue_hours % 24}h"

        send_notification(
            tenant=tenant,
            recipient=ticket.assignee,
            notification_type=NotificationType.TICKET_FOLLOWUP_OVERDUE,
            title=f"Follow-up overdue: Ticket #{ticket.number}",
            body=(
                f'Follow-up for "{ticket.subject}" is overdue by {overdue_label}. '
                f"It was due {ticket.follow_up_due_at.strftime('%b %d, %Y %H:%M')}."
            ),
            data={
                "ticket_id": ticket_id_str,
                "ticket_number": ticket.number,
                "overdue_by": overdue_label,
            },
        )

        logger.info(
            "Follow-up overdue notification sent for ticket #%s (tenant: %s, overdue by: %s)",
            ticket.number,
            tenant.slug,
            overdue_label,
        )


def _execute_rule(rule, ticket, tenant, now):
    """Execute a single escalation rule action."""
    from apps.accounts.models import TenantMembership
    from apps.notifications.models import NotificationType
    from apps.notifications.services import send_notification
    from apps.tickets.models import EscalationRule, TicketActivity

    action = rule.action
    message_parts = []

    if action == EscalationRule.Action.ASSIGN:
        target_user = rule.target_user
        if not target_user and rule.target_role:
            membership = (
                TenantMembership.objects.filter(
                    tenant=tenant, role=rule.target_role, is_active=True,
                )
                .select_related("user")
                .first()
            )
            target_user = membership.user if membership else None

        if target_user:
            old_name = (
                ticket.assignee.get_full_name()
                if ticket.assignee
                else "Unassigned"
            )
            ticket.assignee = target_user
            ticket.assigned_at = now
            ticket.save(update_fields=["assignee", "assigned_at", "updated_at"])
            message_parts.append(
                f"Re-assigned from {old_name} to {target_user.get_full_name()}"
            )

    elif action == EscalationRule.Action.NOTIFY:
        recipients = []
        if rule.target_user:
            recipients.append(rule.target_user)
        elif rule.target_role:
            memberships = TenantMembership.objects.filter(
                tenant=tenant, role=rule.target_role, is_active=True,
            ).select_related("user")[:10]
            recipients = [m.user for m in memberships]
        elif ticket.assignee:
            recipients.append(ticket.assignee)

        notify_body = rule.notify_message or (
            f"Escalation triggered for ticket #{ticket.number}: {ticket.subject}"
        )
        for recipient in recipients:
            send_notification(
                tenant=tenant,
                recipient=recipient,
                notification_type=NotificationType.SLA_BREACH,
                title=f"Escalation: Ticket #{ticket.number}",
                body=notify_body,
                data={
                    "ticket_id": str(ticket.id),
                    "ticket_number": ticket.number,
                    "escalation_rule_id": str(rule.id),
                },
            )
        message_parts.append(
            f"Notification sent to {len(recipients)} recipient(s)"
        )

    elif action == EscalationRule.Action.CHANGE_PRIORITY:
        priority_order = ["low", "medium", "high", "urgent"]
        try:
            current_idx = priority_order.index(ticket.priority)
        except ValueError:
            logger.warning(
                "Ticket #%s has unexpected priority '%s'; defaulting to lowest for escalation.",
                ticket.number,
                ticket.priority,
            )
            current_idx = 0
        if current_idx < len(priority_order) - 1:
            old_priority = ticket.priority
            ticket.priority = priority_order[current_idx + 1]
            ticket.save(update_fields=["priority", "updated_at"])
            message_parts.append(
                f"Priority escalated from {old_priority} to {ticket.priority}"
            )

    # Log the escalation with rule ID for dedup
    TicketActivity(
        tenant=tenant,
        ticket=ticket,
        actor=None,
        event=TicketActivity.Event.ESCALATED,
        message=(
            f"Escalation rule fired: {rule.get_trigger_display()} -> "
            f"{rule.get_action_display()}. "
            + "; ".join(message_parts)
        ),
        metadata={
            "escalation_rule_id": str(rule.id),
            "trigger": rule.trigger,
            "action": action,
            "threshold_minutes": rule.threshold_minutes,
        },
    ).save()

    logger.info(
        "Escalation rule %s fired for ticket #%s (tenant: %s)",
        rule.id,
        ticket.number,
        tenant.slug,
    )


# ---------------------------------------------------------------------------
# Outbound email tasks
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_ticket_reply_email_task(self, ticket_id, comment_body, agent_name, tenant_id):
    """
    Send an outbound reply email to the ticket's contact.

    Delegates to send_ticket_reply_email() which routes through the
    single send_ticket_email() entry point. The idempotency_key on the
    outbound InboundEmail record prevents duplicate sends on Celery retry.
    """
    from apps.tenants.models import Tenant
    from apps.tickets.email_service import send_ticket_reply_email
    from apps.tickets.models import Ticket
    from main.context import tenant_context

    try:
        ticket = Ticket.unscoped.select_related("contact").get(pk=ticket_id)
        tenant = Tenant.objects.get(pk=tenant_id)
    except (Ticket.DoesNotExist, Tenant.DoesNotExist):
        logger.error(
            "send_ticket_reply_email_task: ticket %s or tenant %s not found.",
            ticket_id, tenant_id,
        )
        return

    with tenant_context(tenant):
        try:
            send_ticket_reply_email(ticket, comment_body, agent_name, tenant)
        except Exception as exc:
            logger.exception(
                "Failed to send reply email for ticket %s (attempt %d/%d)",
                ticket_id, self.request.retries + 1, self.max_retries + 1,
            )
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_ticket_created_email_task(self, ticket_id, tenant_id):
    """
    Send a confirmation email to the contact when a ticket is created.

    Delegates to send_ticket_created_email() which routes through the
    single send_ticket_email() entry point.
    """
    from apps.tenants.models import Tenant
    from apps.tickets.email_service import send_ticket_created_email
    from apps.tickets.models import Ticket
    from main.context import tenant_context

    try:
        ticket = Ticket.unscoped.select_related("contact").get(pk=ticket_id)
        tenant = Tenant.objects.get(pk=tenant_id)
    except (Ticket.DoesNotExist, Tenant.DoesNotExist):
        logger.error(
            "send_ticket_created_email_task: ticket %s or tenant %s not found.",
            ticket_id, tenant_id,
        )
        return

    with tenant_context(tenant):
        try:
            send_ticket_created_email(ticket, tenant)
        except Exception as exc:
            logger.exception(
                "Failed to send ticket created email for ticket %s (attempt %d/%d)",
                ticket_id, self.request.retries + 1, self.max_retries + 1,
            )
            raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_ticket_email_task(self, ticket_id, tenant_id, to_email, subject, body_text,
                           body_html=None, sender_type="system"):
    """
    Generic outbound email task for agent-initiated sends.

    This is the async wrapper used by the send_email view action
    so agents don't block on SMTP delivery.
    """
    from apps.inbound_email.models import InboundEmail
    from apps.tenants.models import Tenant
    from apps.tickets.email_service import send_ticket_email
    from apps.tickets.models import Ticket
    from main.context import tenant_context

    try:
        ticket = Ticket.unscoped.get(pk=ticket_id)
        tenant = Tenant.objects.get(pk=tenant_id)
    except (Ticket.DoesNotExist, Tenant.DoesNotExist):
        logger.error(
            "send_ticket_email_task: ticket %s or tenant %s not found.",
            ticket_id, tenant_id,
        )
        return

    with tenant_context(tenant):
        try:
            send_ticket_email(
                tenant=tenant,
                ticket=ticket,
                to_email=to_email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                sender_type=sender_type,
            )
        except Exception as exc:
            logger.exception(
                "Failed to send email for ticket %s to %s (attempt %d/%d)",
                ticket_id, to_email,
                self.request.retries + 1, self.max_retries + 1,
            )
            raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Auto-close task (Phase 4)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=0,
    acks_late=True,
)
def auto_close_ticket(self, ticket_id):
    """
    Auto-close a ticket that has been in 'resolved' status for N days.

    Idempotency guards:
    1. Ticket must still be in 'resolved' status (not reopened).
    2. ``ticket.auto_close_task_id`` must match ``self.request.id``
       (authoritative guard — handles the case where revoke() was
       best-effort and a stale task executes).
    """
    from apps.tickets.models import Ticket, TicketActivity, TicketStatus
    from main.context import tenant_context

    try:
        ticket = Ticket.unscoped.select_related("status", "tenant").get(pk=ticket_id)
    except Ticket.DoesNotExist:
        logger.warning("auto_close_ticket: ticket %s not found.", ticket_id)
        return

    # Guard 1: still in resolved status?
    if not ticket.status or ticket.status.slug != "resolved":
        logger.info(
            "auto_close_ticket: ticket #%s is no longer resolved (status=%s), skipping.",
            ticket.number,
            ticket.status.slug if ticket.status else None,
        )
        return

    # Guard 2: is this the authoritative task?
    if ticket.auto_close_task_id != self.request.id:
        logger.info(
            "auto_close_ticket: task ID mismatch for ticket #%s "
            "(expected=%s, got=%s), skipping.",
            ticket.number,
            ticket.auto_close_task_id,
            self.request.id,
        )
        return

    tenant = ticket.tenant

    with tenant_context(tenant):
        closed_status = (
            TicketStatus.objects.filter(is_closed=True)
            .order_by("order")
            .first()
        )
        if closed_status is None:
            logger.error(
                "auto_close_ticket: no closed status for tenant %s.", tenant.slug,
            )
            return

        from apps.tickets.services import change_ticket_status

        change_ticket_status(ticket, closed_status, actor=None)

        # Log auto-close event
        from apps.tickets.services import _create_ticket_activity

        _create_ticket_activity(
            ticket,
            actor=None,
            event=TicketActivity.Event.AUTO_CLOSED,
            message="Ticket auto-closed after resolved period expired.",
            metadata={"task_id": self.request.id},
        )

        # Clear the task ID
        Ticket.unscoped.filter(pk=ticket.pk).update(auto_close_task_id=None)

    logger.info(
        "auto_close_ticket: ticket #%s auto-closed (tenant: %s).",
        ticket.number,
        tenant.slug,
    )


# ---------------------------------------------------------------------------
# CSAT survey email task (Phase 4)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_csat_survey_email(self, ticket_id, tenant_id):
    """
    Send a CSAT survey email to the ticket's contact.

    Idempotent: skips if the ticket is no longer in 'resolved' status
    (was reopened before the delay elapsed) or if CSAT was already submitted.
    """
    from django.conf import settings as django_settings
    from django.core import signing
    from django.core.mail import send_mail
    from django.template.loader import render_to_string

    from apps.tenants.models import Tenant
    from apps.tickets.models import Ticket
    from main.context import tenant_context

    try:
        ticket = Ticket.unscoped.select_related(
            "status", "tenant", "contact",
        ).get(pk=ticket_id)
        tenant = Tenant.objects.select_related("settings").get(pk=tenant_id)
    except (Ticket.DoesNotExist, Tenant.DoesNotExist):
        logger.warning(
            "send_csat_survey_email: ticket %s or tenant %s not found.",
            ticket_id, tenant_id,
        )
        return

    # Guard: only send if still resolved (not reopened)
    if not ticket.status or ticket.status.slug != "resolved":
        logger.info(
            "send_csat_survey_email: ticket #%s no longer resolved, skipping.",
            ticket.number,
        )
        return

    # Guard: already submitted
    if ticket.csat_rating is not None:
        logger.info(
            "send_csat_survey_email: ticket #%s already has CSAT, skipping.",
            ticket.number,
        )
        return

    # Guard: must have a contact with an email
    if not ticket.contact or not ticket.contact.email:
        logger.info(
            "send_csat_survey_email: ticket #%s has no contact email, skipping.",
            ticket.number,
        )
        return

    # Generate signed token
    token = signing.dumps(
        {"t": str(ticket.pk), "n": str(tenant.pk)},
        salt="csat",
    )

    base_domain = getattr(django_settings, "BASE_DOMAIN", "localhost:8001")
    scheme = "https" if not base_domain.startswith("localhost") else "http"
    survey_url = (
        f"{scheme}://{tenant.slug}.{base_domain}"
        f"/tickets/{ticket.number}/csat/?token={token}"
    )

    tenant_name = tenant.name

    with tenant_context(tenant):
        try:
            context = {
                "ticket": ticket,
                "tenant_name": tenant_name,
                "survey_url": survey_url,
                "contact_name": (
                    ticket.contact.first_name
                    or ticket.contact.email.split("@")[0]
                ),
            }

            subject = f"How did we do? Ticket #{ticket.number}"
            html_body = render_to_string(
                "tickets/email/csat_survey.html", context,
            )
            text_body = render_to_string(
                "tickets/email/csat_survey.txt", context,
            )

            from_email = getattr(
                django_settings, "DEFAULT_FROM_EMAIL", "noreply@kanzan.io",
            )

            send_mail(
                subject=subject,
                message=text_body,
                from_email=from_email,
                recipient_list=[ticket.contact.email],
                html_message=html_body,
                fail_silently=False,
            )

            logger.info(
                "CSAT survey sent for ticket #%s to %s.",
                ticket.number,
                ticket.contact.email,
            )

        except Exception as exc:
            logger.exception(
                "Failed to send CSAT email for ticket #%s (attempt %d/%d)",
                ticket.number,
                self.request.retries + 1,
                self.max_retries + 1,
            )
            raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=1, acks_late=True)
def propagate_sla_policy_change_task(self, policy_id, tenant_id, ticket_ids):
    """
    Async bulk recalculation of SLA deadlines after an SLAPolicy is edited.

    Called when >50 tickets are affected to avoid blocking the web request.
    """
    from apps.tenants.models import Tenant
    from apps.tickets.models import SLAPolicy
    from apps.tickets.signals import _apply_policy_to_tickets
    from main.context import tenant_context

    try:
        tenant = Tenant.objects.get(pk=tenant_id)
        policy = SLAPolicy.unscoped.get(pk=policy_id)
    except (Tenant.DoesNotExist, SLAPolicy.DoesNotExist):
        logger.error(
            "propagate_sla_policy_change_task: policy %s or tenant %s not found.",
            policy_id, tenant_id,
        )
        return

    with tenant_context(tenant):
        _apply_policy_to_tickets(policy, ticket_ids)

    logger.info(
        "Async SLA propagation complete for %d tickets (policy %s).",
        len(ticket_ids), policy.name,
    )
