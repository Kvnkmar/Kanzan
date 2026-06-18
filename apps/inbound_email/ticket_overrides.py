"""Shared validator for optional ticket-field overrides on email→ticket
conversion.

Single source of truth used by BOTH email→ticket surfaces:

* the agent **Emails page** "Create ticket" form
  (``apps.inbound_email.api_views.InboundEmailViewSet.create_ticket``), and
* the **Inbox Hub** cockpit "Convert to ticket" panel
  (``apps.inbox_hub.views.HubEmailViewSet.convert_to_ticket``).

Keeping the validation here (rather than duplicated in each serializer) means
both surfaces enforce the exact same rules — length caps, priority choices,
tenant-scoped FK lookups (malformed id → 400, not 500), closed-status
rejection, active-member assignee, and an aware ``due_date`` — so they can
never drift apart.

Blank / absent fields are omitted so the email-derived defaults survive (a
cleared subject keeps the original email subject rather than creating a
blank-titled ticket). The returned dict is ready to splat into
:func:`apps.inbox_hub.services.convert_to_ticket` /
:func:`apps.inbound_email.services._create_ticket_from_email`.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError as DRFValidationError


def build_ticket_overrides(data, tenant):
    """Parse + validate optional ticket-field overrides from a payload dict.

    ``data`` is any ``request.data``-like mapping; ``tenant`` scopes all FK
    lookups. Bad references raise DRF :class:`ValidationError` → HTTP 400.
    """
    from django.utils import timezone

    from apps.accounts.models import TenantMembership, User
    from apps.tickets.models import Queue, Ticket, TicketStatus

    def _as_str(value):
        # Coerce only genuine strings — a non-string (int/list/dict/bool) becomes
        # "" rather than crashing on .strip(), so malformed JSON is a clean 400.
        return value.strip() if isinstance(value, str) else ""

    def _lookup_pk(model, pk, label):
        # A malformed (non-UUID) id raises Django ValidationError/ValueError from
        # the queryset; translate to a DRF 400 instead of leaking a 500.
        try:
            return model.objects.filter(tenant=tenant, pk=pk).first()
        except (DjangoValidationError, ValueError, TypeError):
            raise DRFValidationError({label: f"Invalid {label} reference."})

    overrides = {}

    subject = _as_str(data.get("subject"))
    if subject:
        overrides["subject"] = subject[:255]

    description = data.get("description")
    if description is not None and str(description).strip():
        # Cap to a sane size — Ticket.description is an unbounded TextField and
        # save() does not full_clean(), so bound it like subject/category.
        overrides["description"] = str(description)[:20000]

    priority = _as_str(data.get("priority")).lower()
    if priority:
        if priority not in {choice[0] for choice in Ticket.Priority.choices}:
            raise DRFValidationError({"priority": "Invalid priority."})
        overrides["priority"] = priority

    category = _as_str(data.get("category"))
    if category:
        overrides["category"] = category[:100]

    queue_id = data.get("queue")
    if queue_id:
        queue = _lookup_pk(Queue, queue_id, "queue")
        if queue is None:
            raise DRFValidationError({"queue": "Selected subcategory was not found."})
        overrides["queue"] = queue

    status_id = data.get("status")
    if status_id:
        ticket_status = _lookup_pk(TicketStatus, status_id, "status")
        if ticket_status is None:
            raise DRFValidationError({"status": "Selected status was not found."})
        # A brand-new ticket should not start life closed — the pre_save
        # lifecycle hook does not stamp resolved_at/closed_at on creation, so a
        # closed status would yield broken closure/SLA reporting.
        if ticket_status.is_closed:
            raise DRFValidationError(
                {"status": "Cannot create a ticket directly into a closed status."}
            )
        overrides["status"] = ticket_status

    assignee_id = data.get("assignee")
    if assignee_id:
        try:
            is_member = TenantMembership.objects.filter(
                tenant=tenant, user_id=assignee_id, is_active=True,
            ).exists()
            assignee = User.objects.filter(pk=assignee_id).first() if is_member else None
        except (DjangoValidationError, ValueError, TypeError):
            raise DRFValidationError({"assignee": "Invalid assignee reference."})
        if assignee is None:
            raise DRFValidationError(
                {"assignee": "Assignee is not an active member of this workspace."}
            )
        overrides["assignee"] = assignee

    due_date_raw = data.get("due_date")
    if due_date_raw:
        if not isinstance(due_date_raw, str):
            raise DRFValidationError({"due_date": "Invalid due-date format."})
        due_date = parse_datetime(due_date_raw)
        if due_date is None:
            raise DRFValidationError({"due_date": "Invalid due-date format."})
        # Normalise to an aware datetime under USE_TZ so we never store naive.
        if timezone.is_naive(due_date):
            due_date = timezone.make_aware(due_date, timezone.get_current_timezone())
        overrides["due_date"] = due_date

    tags = data.get("tags")
    if isinstance(tags, list):
        cleaned = [str(tag).strip()[:50] for tag in tags if str(tag).strip()]
        if cleaned:
            overrides["tags"] = cleaned

    return overrides
