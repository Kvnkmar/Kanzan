"""
Business-logic services for the agents app.

Provides functions for finding available agents, recalculating workloads,
and auto-assigning tickets to the best-fit agent.
"""

import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.agents.models import AgentAvailability, AgentStatus

logger = logging.getLogger(__name__)


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
