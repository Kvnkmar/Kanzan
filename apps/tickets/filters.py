"""
django-filter FilterSets for the tickets app.

Provides rich filtering on the Ticket model including range-based date
filters, JSON ``tags`` containment lookups, and pre-built view filters
for Admin Active / Agent Inbox use-cases.
"""

import django_filters

from apps.tickets.models import Ticket


class TicketFilter(django_filters.FilterSet):
    """
    Filterable fields for the Ticket list endpoint.

    Supports:
    - Exact match on status, priority, assignee, queue, category
    - Boolean ``unassigned`` filter (assignee is null)
    - ``view`` filter for pre-built views:
        - ``admin_active``: unassigned + not closed
        - ``agent_inbox``: assigned to current user + not closed
        - ``closed``: all closed tickets
    - Date range on created_at (``created_after`` / ``created_before``)
    - Date range on due_date (``due_after`` / ``due_before``)
    - JSON containment on tags (pass a single tag value)
    """

    number = django_filters.NumberFilter(field_name="number")
    status = django_filters.UUIDFilter(field_name="status__id")
    status_slug = django_filters.CharFilter(field_name="status__slug")
    priority = django_filters.CharFilter(field_name="priority")
    assignee = django_filters.UUIDFilter(field_name="assignee__id")
    queue = django_filters.UUIDFilter(field_name="queue__id")
    category = django_filters.CharFilter(field_name="category", lookup_expr="iexact")
    is_closed = django_filters.BooleanFilter(field_name="status__is_closed")
    unassigned = django_filters.BooleanFilter(
        method="filter_unassigned",
        help_text="True returns only unassigned tickets; False returns only assigned.",
    )
    view = django_filters.CharFilter(
        method="filter_by_view",
        help_text="Pre-built views: admin_active, agent_inbox, closed.",
    )

    created_after = django_filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
    )
    created_before = django_filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
    )
    due_after = django_filters.DateTimeFilter(
        field_name="due_date",
        lookup_expr="gte",
    )
    due_before = django_filters.DateTimeFilter(
        field_name="due_date",
        lookup_expr="lte",
    )

    tag = django_filters.CharFilter(method="filter_by_tag")

    # --- SLA filters ---
    sla_response_breached = django_filters.BooleanFilter(field_name="sla_response_breached")
    sla_resolution_breached = django_filters.BooleanFilter(field_name="sla_resolution_breached")
    has_sla = django_filters.BooleanFilter(
        method="filter_has_sla",
        help_text="True returns tickets with SLA policy; False returns without.",
    )

    # --- Ticket type ---
    ticket_type = django_filters.CharFilter(field_name="ticket_type")

    # --- Channel ---
    channel = django_filters.CharFilter(field_name="channel")

    # --- Watching ---
    watching = django_filters.BooleanFilter(
        method="filter_watching",
        help_text="True returns tickets the current user is watching.",
    )

    # --- SLA approaching breach ---
    sla_approaching = django_filters.BooleanFilter(
        method="filter_sla_approaching",
        help_text="True returns tickets approaching SLA breach within 30 minutes.",
    )

    class Meta:
        model = Ticket
        fields = [
            "number",
            "status",
            "status_slug",
            "priority",
            "assignee",
            "queue",
            "category",
            "is_closed",
            "unassigned",
            "view",
            "created_after",
            "created_before",
            "due_after",
            "due_before",
            "tag",
            "sla_response_breached",
            "sla_resolution_breached",
            "has_sla",
            "ticket_type",
            "channel",
            "watching",
            "sla_approaching",
        ]

    def filter_by_tag(self, queryset, name, value):
        """Filter tickets whose ``tags`` JSON array contains *value*."""
        return queryset.filter(tags__contains=[value])

    def filter_unassigned(self, queryset, name, value):
        """True -> assignee IS NULL; False -> assignee IS NOT NULL."""
        if value:
            return queryset.filter(assignee__isnull=True)
        return queryset.filter(assignee__isnull=False)

    def filter_by_view(self, queryset, name, value):
        """
        Pre-built view filters:

        - ``admin_active``: Unassigned + not closed (Admin's active queue).
        - ``agent_inbox``: Assigned to current user + not closed.
        - ``closed``: All closed tickets (searchable by case number).
        """
        if value == "admin_active":
            return queryset.filter(
                assignee__isnull=True, status__is_closed=False,
            )
        if value == "agent_inbox":
            user = getattr(self.request, "user", None)
            if user and user.is_authenticated:
                return queryset.filter(
                    assignee=user, status__is_closed=False,
                )
            return queryset.none()
        if value == "closed":
            return queryset.filter(status__is_closed=True)
        if value == "watching":
            user = getattr(self.request, "user", None)
            if user and user.is_authenticated:
                from apps.tickets.models import TicketWatcher
                watched_ids = TicketWatcher.objects.filter(
                    user=user, is_muted=False,
                ).values_list("ticket_id", flat=True)
                return queryset.filter(pk__in=watched_ids)
            return queryset.none()
        if value == "sla_breached":
            from django.db.models import Q
            return queryset.filter(
                Q(sla_response_breached=True) | Q(sla_resolution_breached=True),
            )
        return queryset

    def filter_has_sla(self, queryset, name, value):
        if value:
            return queryset.filter(sla_policy__isnull=False)
        return queryset.filter(sla_policy__isnull=True)

    def filter_watching(self, queryset, name, value):
        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return queryset.none() if value else queryset
        from apps.tickets.models import TicketWatcher
        watched_ids = TicketWatcher.objects.filter(
            user=user, is_muted=False,
        ).values_list("ticket_id", flat=True)
        if value:
            return queryset.filter(pk__in=watched_ids)
        return queryset.exclude(pk__in=watched_ids)

    def filter_sla_approaching(self, queryset, name, value):
        if not value:
            return queryset
        import datetime
        from django.db.models import Q
        from django.utils import timezone as tz
        now = tz.now()
        window = datetime.timedelta(minutes=30)
        return queryset.filter(
            Q(
                sla_first_response_due__gt=now,
                sla_first_response_due__lte=now + window,
                sla_response_breached=False,
                first_responded_at__isnull=True,
            ) | Q(
                sla_resolution_due__gt=now,
                sla_resolution_due__lte=now + window,
                sla_resolution_breached=False,
                resolved_at__isnull=True,
            ),
        )
