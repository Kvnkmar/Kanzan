"""Shared contact-context aggregation.

Builds the "who is this customer" payload consumed by both the ticket-detail
sidebar (:meth:`apps.contacts.views.ContactViewSet.context`) and the Inbox Hub
triage cockpit (:meth:`apps.inbox_hub.views.HubEmailViewSet.context`).

Centralised here so the two callers stay in lockstep and the (cached)
aggregation lives in one place. Cached per ``(tenant, contact)`` for 60s; the
cache is skipped when ``exclude_ticket`` is supplied so the ticket-detail
caller's filtered payload never pollutes the shared cache entry.
"""

from django.core.cache import cache
from django.db.models import Avg

from apps.tickets.models import Ticket

# Bumped from ``contact_context:`` so stale entries (without company/account)
# from a prior deploy don't shadow the enriched shape.
_CACHE_PREFIX = "contact_context_v2"


def build_contact_context(contact, tenant, *, exclude_ticket=None):
    """Return the context summary dict for ``contact`` within ``tenant``.

    Shape::

        {
            "contact": {id, name, email, email_bouncing, created_at,
                        company, account: {name, mrr, health_score} | None,
                        last_activity_at},
            "stats": {total_tickets, open_tickets, avg_csat, last_ticket_at},
            "recent_tickets": [{id, number, subject, status, status_color,
                                priority, created_at}, ...up to 5],
        }

    ``exclude_ticket`` (a ticket pk) omits that ticket from ``recent_tickets``
    and bypasses the cache (the ticket-detail caller passes the open ticket).
    """
    cache_key = f"{_CACHE_PREFIX}:{tenant.pk}:{contact.pk}"
    if not exclude_ticket:
        cached = cache.get(cache_key)
        if cached:
            return cached

    tickets_qs = Ticket.objects.filter(contact=contact)

    total_tickets = tickets_qs.count()
    open_tickets = tickets_qs.filter(status__is_closed=False).count()

    avg_csat_raw = tickets_qs.filter(
        csat_rating__isnull=False,
    ).aggregate(avg=Avg("csat_rating"))["avg"]
    avg_csat = round(avg_csat_raw, 1) if avg_csat_raw is not None else None

    last_ticket_at = None
    latest = (
        tickets_qs.order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if latest:
        last_ticket_at = latest.isoformat()

    recent_qs = tickets_qs.select_related("status").order_by("-created_at")
    if exclude_ticket:
        recent_qs = recent_qs.exclude(pk=exclude_ticket)
    recent_tickets = [
        {
            "id": str(t.pk),
            "number": t.number,
            "subject": t.subject,
            "status": t.status.name if t.status else None,
            "status_color": t.status.color if t.status else None,
            "priority": t.priority,
            "created_at": t.created_at.isoformat(),
        }
        for t in recent_qs[:5]
    ]

    account = None
    if contact.account_id:
        account = {
            "name": contact.account.name,
            "mrr": (
                str(contact.account.mrr)
                if contact.account.mrr is not None
                else None
            ),
            "health_score": contact.account.health_score,
        }

    data = {
        "contact": {
            "id": str(contact.pk),
            "name": contact.full_name,
            "email": contact.email,
            "email_bouncing": contact.email_bouncing,
            "created_at": contact.created_at.isoformat(),
            "company": contact.company.name if contact.company_id else None,
            "account": account,
            "last_activity_at": (
                contact.last_activity_at.isoformat()
                if contact.last_activity_at
                else None
            ),
        },
        "stats": {
            "total_tickets": total_tickets,
            "open_tickets": open_tickets,
            "avg_csat": avg_csat,
            "last_ticket_at": last_ticket_at,
        },
        "recent_tickets": recent_tickets,
    }

    if not exclude_ticket:
        cache.set(cache_key, data, 60)
    return data
