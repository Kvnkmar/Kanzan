"""Shared ticket-visibility rules for non-privileged (agent / viewer) members.

Single source of truth for *which tickets an agent may see*. Imported by the
ticket-list queryset, the sidebar badge count, dashboard analytics, kanban card
scoping and the object-level permission check so the rule can never drift apart
between those surfaces.

Rule
----
An agent / viewer sees a ticket only when:

* it is **assigned to them**, OR
* they **created it and it has not been handed off** (``assignee`` is null).

Once a ticket is assigned to a *different* agent it leaves the creator's view —
it is now that other agent's responsibility. Admin / Manager (hierarchy_level
<= 20) bypass this entirely and see every ticket in the tenant.
"""

from django.db.models import Q


def agent_visible_tickets_q(user):
    """Return a ``Q`` matching the tickets an agent / viewer may see.

    Use as ``qs.filter(agent_visible_tickets_q(user))``.
    """
    return Q(assignee=user) | (Q(created_by=user) & Q(assignee__isnull=True))


def agent_can_see_ticket(user, ticket):
    """Object-level equivalent of :func:`agent_visible_tickets_q`."""
    if ticket.assignee_id == user.pk:
        return True
    return ticket.created_by_id == user.pk and ticket.assignee_id is None
