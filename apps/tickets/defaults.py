"""Default ticket configuration seeded for every new tenant.

A brand-new tenant needs at least one ``is_default=True`` ``TicketStatus`` (the
ticket-create path 400s without one) plus a queue or two before the first
ticket can be created via UI, API, or inbound email. ``setup_company`` (self-
service onboarding) and the ``provision_tenant`` command both call
``seed_default_ticket_config`` so no creation path leaves a tenant unable to
work. The ``setup_ticket_statuses`` / ``setup_queues`` commands reuse the same
data, so there is a single source of truth.

Everything here is idempotent (``get_or_create`` on the unscoped manager with an
explicit ``tenant=``), so it is safe to call repeatedly and safe to call with no
tenant bound in the context (onboarding creates the tenant on the bare domain).
"""

DEFAULT_STATUSES = [
    {"name": "Open", "slug": "open", "color": "#0d6efd", "order": 10, "is_closed": False, "is_default": True},
    {"name": "In Progress", "slug": "in-progress", "color": "#ffc107", "order": 20, "is_closed": False, "is_default": False},
    {"name": "Waiting", "slug": "waiting", "color": "#6c757d", "order": 30, "is_closed": False, "is_default": False},
    {"name": "Resolved", "slug": "resolved", "color": "#198754", "order": 40, "is_closed": False, "is_default": False},
    {"name": "Closed", "slug": "closed", "color": "#dc3545", "order": 50, "is_closed": True, "is_default": False},
]

DEFAULT_QUEUES = [
    {"name": "Support", "description": "General support requests and customer inquiries."},
    {"name": "Billing", "description": "Billing, payment, and subscription issues."},
    {"name": "Technical", "description": "Technical issues, bugs, and feature requests."},
    {"name": "General", "description": "General inquiries and miscellaneous requests."},
]

DEFAULT_CATEGORIES = [
    {"name": "General", "slug": "general", "color": "#6c757d", "order": 10},
    {"name": "Bug", "slug": "bug", "color": "#dc3545", "order": 20},
    {"name": "Question", "slug": "question", "color": "#0d6efd", "order": 30},
    {"name": "Feature Request", "slug": "feature-request", "color": "#198754", "order": 40},
]


def seed_default_ticket_config(tenant):
    """Idempotently seed default statuses, queues, and categories for *tenant*.

    Returns a dict of created counts. Safe to call off tenant-context and safe
    to re-run (skips anything already present by slug/name).
    """
    from apps.tickets.models import Queue, TicketCategory, TicketStatus

    created = {"statuses": 0, "queues": 0, "categories": 0}

    for s in DEFAULT_STATUSES:
        _, was_created = TicketStatus.unscoped.get_or_create(
            tenant=tenant,
            slug=s["slug"],
            defaults={
                "name": s["name"],
                "color": s["color"],
                "order": s["order"],
                "is_closed": s["is_closed"],
                "is_default": s["is_default"],
            },
        )
        created["statuses"] += int(was_created)

    for q in DEFAULT_QUEUES:
        _, was_created = Queue.unscoped.get_or_create(
            tenant=tenant,
            name=q["name"],
            defaults={"description": q["description"]},
        )
        created["queues"] += int(was_created)

    for c in DEFAULT_CATEGORIES:
        _, was_created = TicketCategory.unscoped.get_or_create(
            tenant=tenant,
            slug=c["slug"],
            defaults={"name": c["name"], "color": c["color"], "order": c["order"]},
        )
        created["categories"] += int(was_created)

    return created
