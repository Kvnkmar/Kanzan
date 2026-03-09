"""
Business-logic services for the analytics app.

Provides aggregation functions for ticket statistics, agent performance
metrics, and SLA compliance rates. All functions accept a tenant instance
and optional date-range filters for time-bounded reporting.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, F, Q
from django.utils import timezone

from apps.tickets.models import SLAPolicy, Ticket, TicketStatus

logger = logging.getLogger(__name__)


def _apply_user_filter(qs, tenant, user):
    """Restrict queryset to the user's own tickets if they are a viewer."""
    if user is None or user.is_superuser:
        return qs
    from apps.accounts.models import TenantMembership

    membership = (
        TenantMembership.objects.select_related("role")
        .filter(user=user, tenant=tenant, is_active=True)
        .first()
    )
    if membership and membership.role.hierarchy_level > 20:
        qs = qs.filter(Q(created_by=user) | Q(assignee=user))
    return qs


def get_ticket_stats(tenant, date_from=None, date_to=None, user=None):
    """
    Return aggregated ticket statistics for a tenant.

    Returns a dict with:
        - open_count: number of currently open tickets
        - closed_count: number of closed tickets in the date range
        - avg_resolution_time: average resolution time in hours (or None)
        - by_priority: dict mapping priority -> count
        - by_status: dict mapping status name -> count
    """
    base_qs = Ticket.unscoped.filter(tenant=tenant)
    base_qs = _apply_user_filter(base_qs, tenant, user)

    if date_from:
        base_qs = base_qs.filter(created_at__gte=date_from)
    if date_to:
        base_qs = base_qs.filter(created_at__lte=date_to)

    # Determine which statuses are "closed" for this tenant.
    closed_status_ids = list(
        TicketStatus.unscoped.filter(tenant=tenant, is_closed=True)
        .values_list("id", flat=True)
    )

    open_count = base_qs.exclude(status_id__in=closed_status_ids).count()
    closed_count = base_qs.filter(status_id__in=closed_status_ids).count()

    # Average resolution time for tickets that have resolved_at set.
    resolved_qs = base_qs.filter(resolved_at__isnull=False)
    avg_resolution = resolved_qs.aggregate(
        avg_time=Avg(F("resolved_at") - F("created_at"))
    )["avg_time"]

    avg_resolution_hours = None
    if avg_resolution is not None:
        avg_resolution_hours = round(
            avg_resolution.total_seconds() / 3600, 2
        )

    # Breakdown by priority.
    by_priority = {}
    priority_rows = base_qs.values("priority").annotate(count=Count("id"))
    for row in priority_rows:
        by_priority[row["priority"]] = row["count"]

    # Breakdown by status.
    by_status = {}
    status_rows = (
        base_qs.values("status__name")
        .annotate(count=Count("id"))
        .order_by("status__name")
    )
    for row in status_rows:
        status_name = row["status__name"] or "Unassigned"
        by_status[status_name] = row["count"]

    return {
        "open_count": open_count,
        "closed_count": closed_count,
        "avg_resolution_time": avg_resolution_hours,
        "by_priority": by_priority,
        "by_status": by_status,
    }


def get_agent_performance(tenant, date_from=None, date_to=None):
    """
    Return per-agent performance metrics for a tenant.

    Returns a dict with:
        - agents: list of dicts, each containing:
            - user_id
            - user_email
            - user_name
            - total_tickets: tickets assigned in the period
            - closed_tickets: tickets resolved in the period
            - avg_resolution_hours: average resolution time in hours (or None)
    """
    base_qs = Ticket.unscoped.filter(
        tenant=tenant,
        assignee__isnull=False,
    )

    if date_from:
        base_qs = base_qs.filter(created_at__gte=date_from)
    if date_to:
        base_qs = base_qs.filter(created_at__lte=date_to)

    closed_status_ids = list(
        TicketStatus.unscoped.filter(tenant=tenant, is_closed=True)
        .values_list("id", flat=True)
    )

    agent_rows = (
        base_qs.values(
            "assignee__id",
            "assignee__email",
            "assignee__first_name",
            "assignee__last_name",
        )
        .annotate(
            total_tickets=Count("id"),
            closed_tickets=Count("id", filter=Q(status_id__in=closed_status_ids)),
            avg_resolution=Avg(
                F("resolved_at") - F("created_at"),
                filter=Q(resolved_at__isnull=False),
            ),
        )
        .order_by("-total_tickets")
    )

    agents = []
    for row in agent_rows:
        first = row["assignee__first_name"] or ""
        last = row["assignee__last_name"] or ""
        avg_res_hours = None
        if row["avg_resolution"] is not None:
            avg_res_hours = round(
                row["avg_resolution"].total_seconds() / 3600, 2
            )
        agents.append(
            {
                "user_id": str(row["assignee__id"]),
                "user_email": row["assignee__email"],
                "user_name": f"{first} {last}".strip(),
                "total_tickets": row["total_tickets"],
                "closed_tickets": row["closed_tickets"],
                "avg_resolution_hours": avg_res_hours,
            }
        )

    return {"agents": agents}


def get_sla_compliance(tenant, date_from=None, date_to=None):
    """
    Return SLA compliance rates for a tenant.

    For each active SLA policy, calculates the percentage of tickets (matching
    the policy's priority) that were resolved within the target resolution time.

    Returns a dict with:
        - policies: list of dicts, each containing:
            - policy_name
            - priority
            - total_tickets
            - compliant_tickets
            - compliance_rate (percentage as a float, 0-100)
    """
    sla_policies = SLAPolicy.unscoped.filter(tenant=tenant, is_active=True)

    base_qs = Ticket.unscoped.filter(tenant=tenant)
    if date_from:
        base_qs = base_qs.filter(created_at__gte=date_from)
    if date_to:
        base_qs = base_qs.filter(created_at__lte=date_to)

    policies = []
    for policy in sla_policies:
        priority_tickets = base_qs.filter(priority=policy.priority)
        total = priority_tickets.count()

        if total == 0:
            policies.append(
                {
                    "policy_name": policy.name,
                    "priority": policy.priority,
                    "total_tickets": 0,
                    "compliant_tickets": 0,
                    "compliance_rate": 100.0,
                    "response_compliant_tickets": 0,
                    "response_compliance_rate": 100.0,
                    "response_breached_count": 0,
                    "resolution_breached_count": 0,
                }
            )
            continue

        # Resolution compliance: resolved within the SLA window.
        resolution_delta = timedelta(minutes=policy.resolution_minutes)
        resolution_compliant = priority_tickets.filter(
            resolved_at__isnull=False,
            resolved_at__lte=F("created_at") + resolution_delta,
        ).count()

        resolution_rate = (
            round((resolution_compliant / total) * 100, 2) if total > 0 else 0.0
        )

        # Response compliance: first response within the SLA window.
        response_delta = timedelta(minutes=policy.first_response_minutes)
        responded = priority_tickets.filter(first_responded_at__isnull=False).count()
        response_compliant = priority_tickets.filter(
            first_responded_at__isnull=False,
            first_responded_at__lte=F("created_at") + response_delta,
        ).count()
        response_rate = (
            round((response_compliant / responded) * 100, 2)
            if responded > 0
            else 100.0
        )

        # Breach counts (set by the periodic SLA scanner task)
        response_breached = priority_tickets.filter(
            sla_response_breached=True
        ).count()
        resolution_breached = priority_tickets.filter(
            sla_resolution_breached=True
        ).count()

        policies.append(
            {
                "policy_name": policy.name,
                "priority": policy.priority,
                "total_tickets": total,
                "compliant_tickets": resolution_compliant,
                "compliance_rate": resolution_rate,
                "response_compliant_tickets": response_compliant,
                "response_compliance_rate": response_rate,
                "response_breached_count": response_breached,
                "resolution_breached_count": resolution_breached,
            }
        )

    return {"policies": policies}
