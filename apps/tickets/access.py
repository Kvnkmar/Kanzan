"""Shared ticket-visibility rules for non-privileged (agent / viewer) members.

Single source of truth for *which tickets an agent may see*. Imported by the
ticket-list queryset, the sidebar badge count, dashboard analytics, kanban card
scoping and the object-level permission check so the rule can never drift apart
between those surfaces.

Rule
----
An agent / viewer sees a ticket only when:

* it is **assigned to them**, OR
* they **created it** (even after it has been handed off to another agent).

A creator keeps a ticket in view after assigning it elsewhere — e.g. an agent
who triages an inbound email, converts it to a ticket and routes it to a
teammate still finds it in their own ticket list. Admin / Manager
(hierarchy_level <= 20) bypass this entirely and see every ticket in the tenant.
"""

from django.db.models import Q


def agent_visible_tickets_q(user):
    """Return a ``Q`` matching the tickets an agent / viewer may see.

    Use as ``qs.filter(agent_visible_tickets_q(user))``.
    """
    return Q(assignee=user) | Q(created_by=user)


def agent_can_see_ticket(user, ticket):
    """Object-level equivalent of :func:`agent_visible_tickets_q`."""
    return ticket.assignee_id == user.pk or ticket.created_by_id == user.pk
