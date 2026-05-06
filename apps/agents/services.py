"""
Business-logic services for the agents app.

Provides functions for finding available agents, recalculating workloads,
and auto-assigning tickets to the best-fit agent.
"""

import logging

from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.utils import timezone

from apps.agents.models import AgentAvailability, AgentStatus

logger = logging.getLogger(__name__)


# Role hierarchy level that defines a pure "Agent" (not Admin/Manager).
# See accounts.Role: Admin=10, Manager=20, Agent=30.
AGENT_ROLE_LEVEL = 30


def get_available_agent(tenant, queue=None):
    """
    Return the available agent with the lowest workload for the given tenant.

    An agent is considered available if:
        - Their status is ``online``
        - Their ``current_ticket_count`` is below ``max_concurrent_tickets``

    When ``queue`` is provided and has a ``default_assignee``, that user is
    preferred if they are available.

    .. note::
        The returned agent is **not** locked for update. Callers performing
        mutations (e.g. ``auto_assign_ticket``) must re-fetch the agent
        row with ``select_for_update()`` inside a ``transaction.atomic()``
        block and re-verify capacity before proceeding.

    Returns:
        AgentAvailability instance or None if no agents are available.
    """
    base_qs = AgentAvailability.unscoped.filter(
        tenant=tenant,
        status=AgentStatus.ONLINE,
        current_ticket_count__lt=F("max_concurrent_tickets"),
    )

    # Prefer the queue's default assignee if set and available.
    if queue is not None and queue.default_assignee_id:
        preferred = base_qs.filter(user_id=queue.default_assignee_id).first()
        if preferred is not None:
            return preferred

    # Fall back to the agent with the lowest current workload.
    return base_qs.order_by("current_ticket_count").first()


def update_ticket_count(agent_availability):
    """
    Recalculate ``current_ticket_count`` from actual open assigned tickets.

    Queries the Ticket model for open tickets assigned to this agent's user
    within the same tenant and updates the count accordingly.
    """
    from apps.tickets.models import Ticket, TicketStatus

    closed_status_ids = list(
        TicketStatus.unscoped.filter(
            tenant=agent_availability.tenant, is_closed=True
        ).values_list("id", flat=True)
    )

    actual_count = Ticket.unscoped.filter(
        tenant=agent_availability.tenant,
        assignee=agent_availability.user,
    ).exclude(
        status_id__in=closed_status_ids
    ).count()

    if agent_availability.current_ticket_count != actual_count:
        logger.info(
            "Updating ticket count for agent %s (tenant %s): %d -> %d",
            agent_availability.user.email,
            agent_availability.tenant,
            agent_availability.current_ticket_count,
            actual_count,
        )
        agent_availability.current_ticket_count = actual_count
        agent_availability.save(
            update_fields=["current_ticket_count", "updated_at"]
        )

    return actual_count


def auto_assign_ticket(ticket):
    """
    Find the best available agent and assign the ticket to them.

    Steps:
        1. Find the agent with the lowest workload who is online and has capacity.
        2. Assign the ticket to that agent.
        3. Increment the agent's ``current_ticket_count``.
        4. Create a TicketAssignment audit record.

    Returns:
        The assigned user, or None if no agents were available.
    """
    from apps.tickets.models import TicketAssignment

    agent = get_available_agent(ticket.tenant, queue=ticket.queue)

    if agent is None:
        logger.warning(
            "No available agents for ticket #%s in tenant %s.",
            ticket.number,
            ticket.tenant,
        )
        return None

    with transaction.atomic():
        # Re-fetch with a row-level lock to prevent concurrent assignments
        # from exceeding the agent's capacity.
        locked_agent = (
            AgentAvailability.unscoped
            .select_for_update()
            .filter(pk=agent.pk)
            .first()
        )

        if (
            locked_agent is None
            or locked_agent.status != AgentStatus.ONLINE
            or locked_agent.current_ticket_count >= locked_agent.max_concurrent_tickets
        ):
            logger.warning(
                "Agent %s no longer available after locking (ticket #%s, tenant %s).",
                agent.user.email,
                ticket.number,
                ticket.tenant,
            )
            return None

        # Assign the ticket.
        ticket.assignee = locked_agent.user
        ticket.save(update_fields=["assignee", "updated_at"])

        # Update agent workload.
        locked_agent.current_ticket_count = F("current_ticket_count") + 1
        locked_agent.last_activity = timezone.now()
        locked_agent.save(update_fields=["current_ticket_count", "last_activity", "updated_at"])
        locked_agent.refresh_from_db()

        # Record assignment history.
        TicketAssignment.objects.create(
            ticket=ticket,
            assigned_to=locked_agent.user,
            assigned_by=None,
            note="Auto-assigned based on agent availability and workload.",
            tenant=ticket.tenant,
        )

    logger.info(
        "Ticket #%s auto-assigned to %s (tenant %s, workload: %d/%d).",
        ticket.number,
        locked_agent.user.email,
        ticket.tenant,
        locked_agent.current_ticket_count,
        locked_agent.max_concurrent_tickets,
    )

    return locked_agent.user


# ---------------------------------------------------------------------------
# Inbound-email auto-assign: load-then-fairness rotation
# ---------------------------------------------------------------------------


def pick_email_agent(tenant):
    """
    Select the next Agent-role user who should receive an inbound-email ticket
    in ``tenant``. Returns the ``User`` instance, or ``None`` if none are
    eligible.

    Selection policy (ordered):
        1. Must be an active tenant member with role ``hierarchy_level == 30``
           (pure Agent). Admins and Managers are intentionally excluded so
           auto-assignment does not pull leadership into front-line triage.
        2. Must not be marked ``offline``. Agents with no ``AgentAvailability``
           row at all are treated as eligible — the tenant may never have
           configured availability, and we still want to route mail rather
           than leave it unassigned.
        3. Among eligible agents, pick the one with the fewest **open**
           tickets (load balancing).
        4. Ties are broken by least-recently-assigned (``TicketAssignment``
           audit timestamp) so new agents — who have never been assigned
           anything — go first, and repeated ties rotate fairly.

    The query is purely read-only; callers are expected to commit the
    assignment inside a transaction. Returning the User rather than the
    availability row avoids coupling callers to the presence of an
    ``AgentAvailability`` record.
    """
    from apps.accounts.models import TenantMembership
    from apps.tickets.models import TicketStatus

    closed_status_ids = list(
        TicketStatus.unscoped.filter(tenant=tenant, is_closed=True)
        .values_list("id", flat=True)
    )

    offline_user_ids = list(
        AgentAvailability.unscoped.filter(
            tenant=tenant, status=AgentStatus.OFFLINE,
        ).values_list("user_id", flat=True)
    )

    memberships = (
        TenantMembership.objects
        .filter(
            tenant=tenant,
            is_active=True,
            role__hierarchy_level=AGENT_ROLE_LEVEL,
        )
        .exclude(user_id__in=offline_user_ids)
        .select_related("user")
        .annotate(
            open_ticket_count=Count(
                "user__assigned_tickets",
                filter=(
                    Q(user__assigned_tickets__tenant=tenant)
                    & ~Q(user__assigned_tickets__status_id__in=closed_status_ids)
                    & Q(user__assigned_tickets__is_deleted=False)
                ),
                distinct=True,
            ),
            last_assigned_at=Max(
                "user__ticket_assignments__created_at",
                filter=Q(user__ticket_assignments__tenant=tenant),
            ),
        )
        # NULLS FIRST so agents who have never been assigned a ticket
        # are picked before those who already have a history, keeping
        # rotation fair at cold-start.
        .order_by("open_ticket_count", F("last_assigned_at").asc(nulls_first=True))
    )

    chosen = memberships.first()
    return chosen.user if chosen else None


def auto_assign_email_ticket(ticket):
    """
    Assign ``ticket`` to the next eligible Agent using the load-then-fairness
    policy in :func:`pick_email_agent`. Intended to be called immediately
    after a ticket is created from an inbound customer email, when the
    tenant has ``auto_assign_inbound_email_tickets`` enabled.

    No-ops (returns ``None``) when:
        - the ticket is already assigned,
        - no eligible agent exists.

    Records a ``TicketAssignment`` audit row with a note identifying this as
    an automatic routing decision so the audit trail is clear to reviewers.
    Nudges the agent's ``AgentAvailability.current_ticket_count`` if a row
    exists, so workload-based queries converge without waiting for the
    reconcile job.

    Returns the assigned ``User`` or ``None``.
    """
    from apps.tickets.models import TicketAssignment

    if ticket.assignee_id is not None:
        return None

    user = pick_email_agent(ticket.tenant)
    if user is None:
        logger.info(
            "Email auto-assign: no eligible agent for ticket #%s in tenant %s — "
            "leaving unassigned.",
            ticket.number, ticket.tenant,
        )
        return None

    with transaction.atomic():
        ticket.assignee = user
        ticket.save(update_fields=["assignee", "updated_at"])

        TicketAssignment.objects.create(
            ticket=ticket,
            assigned_to=user,
            assigned_by=None,
            note="Auto-assigned from inbound email (load + fairness).",
            tenant=ticket.tenant,
        )

        # Best-effort workload nudge. If the agent has never set availability,
        # there is no row to update — the reconcile job will create one when
        # the agent logs in.
        updated = AgentAvailability.unscoped.filter(
            tenant=ticket.tenant, user=user,
        ).update(
            current_ticket_count=F("current_ticket_count") + 1,
            last_activity=timezone.now(),
        )

    logger.info(
        "Email auto-assign: ticket #%s → %s (tenant %s, availability_updated=%d).",
        ticket.number, user.email, ticket.tenant, updated,
    )
    return user
