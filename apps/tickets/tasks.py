"""
Celery tasks for the tickets app.

Tasks:
    check_sla_breaches
        Periodic scan for SLA violations and escalation rule execution.
        Runs every 2 minutes via Celery Beat.
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
        )

        for ticket in tickets:
            _check_ticket_sla(ticket, policy, tenant, tenant_settings, now)


def _check_ticket_sla(ticket, policy, tenant, tenant_settings, now):
    """Check a single ticket against its SLA policy and fire escalations."""
    update_fields = []

    # --- Response SLA ---
    if not ticket.sla_response_breached and ticket.first_responded_at is None:
        elapsed = _elapsed(
            ticket.created_at, now, policy, tenant_settings
        )
        if elapsed > policy.first_response_minutes:
            ticket.sla_response_breached = True
            update_fields.append("sla_response_breached")
            _fire_breach(ticket, tenant, "response_breach", policy)

    # --- Resolution SLA ---
    if not ticket.sla_resolution_breached and ticket.resolved_at is None:
        elapsed = _elapsed(
            ticket.created_at, now, policy, tenant_settings
        )
        if elapsed > policy.resolution_minutes:
            ticket.sla_resolution_breached = True
            update_fields.append("sla_resolution_breached")
            _fire_breach(ticket, tenant, "resolution_breach", policy)

    if update_fields:
        update_fields.append("updated_at")
        ticket.save(update_fields=update_fields)

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

    for rule in rules:
        rule_id_str = str(rule.id)
        if rule_id_str in fired_rule_ids:
            continue

        # Determine reference time and base SLA minutes for this trigger
        if rule.trigger == EscalationRule.Trigger.RESPONSE_BREACH:
            if ticket.first_responded_at is not None:
                continue
            reference_time = ticket.created_at
            sla_minutes = policy.first_response_minutes
        elif rule.trigger == EscalationRule.Trigger.RESOLUTION_BREACH:
            if ticket.resolved_at is not None:
                continue
            reference_time = ticket.created_at
            sla_minutes = policy.resolution_minutes
        elif rule.trigger == EscalationRule.Trigger.IDLE_TIME:
            reference_time = ticket.updated_at
            sla_minutes = 0
        else:
            continue

        elapsed = _elapsed(reference_time, now, policy, tenant_settings)

        # threshold_minutes is added to the SLA target for breach triggers,
        # or stands alone for idle_time
        target = (sla_minutes + rule.threshold_minutes) if sla_minutes else rule.threshold_minutes
        if elapsed < target:
            continue

        _execute_rule(rule, ticket, tenant, now)


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
