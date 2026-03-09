"""
Ticket system models for multi-tenant CRM platform.

Provides customisable ticket statuses, queues, SLA policies with escalation
rules, and full assignment history tracking -- all scoped per tenant.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from main.models import TenantScopedModel


class TicketStatus(TenantScopedModel):
    """
    Tenant-customisable ticket status (e.g. Open, In Progress, Closed).

    Each tenant can define their own workflow statuses with colours, ordering,
    and flags that indicate whether a status represents a closed/resolved state.
    Exactly one status per tenant should be marked ``is_default=True`` so that
    new tickets receive it automatically.
    """

    name = models.CharField(max_length=50)
    slug = models.SlugField(max_length=50)
    color = models.CharField(max_length=7, default="#6c757d")
    order = models.PositiveIntegerField()
    is_closed = models.BooleanField(
        default=False,
        help_text="Marks this status as a resolution/closed state.",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="If True, new tickets for this tenant receive this status.",
    )

    class Meta:
        unique_together = [("tenant", "slug")]
        ordering = ["order"]
        verbose_name = "ticket status"
        verbose_name_plural = "ticket statuses"

    def __str__(self):
        return self.name


class Queue(TenantScopedModel):
    """
    Organisational bucket for tickets (e.g. Support, Billing, Engineering).

    Optionally routes new tickets to a ``default_assignee`` when
    ``auto_assign`` is enabled.
    """

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    default_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_queues",
    )
    auto_assign = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        verbose_name = "queue"
        verbose_name_plural = "queues"

    def __str__(self):
        return self.name


class TicketCategory(TenantScopedModel):
    """
    Admin-configurable ticket category per tenant.

    Categories are managed in Settings and appear as dropdown options
    when creating or editing tickets.
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    color = models.CharField(max_length=7, default="#6c757d")
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "slug")]
        ordering = ["order", "name"]
        verbose_name = "ticket category"
        verbose_name_plural = "ticket categories"

    def __str__(self):
        return self.name


class Ticket(TenantScopedModel):
    """
    Core ticket model.

    ``number`` is auto-incremented per tenant on first save.  The ``status``
    field references a tenant-customisable ``TicketStatus`` instance.
    """

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    number = models.PositiveIntegerField(editable=False)
    subject = models.CharField(max_length=255)
    description = models.TextField()
    status = models.ForeignKey(
        TicketStatus,
        on_delete=models.PROTECT,
        related_name="tickets",
    )
    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )
    category = models.CharField(max_length=100, null=True, blank=True)
    queue = models.ForeignKey(
        Queue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    company = models.ForeignKey(
        "contacts.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets_assigned_by",
        help_text="The user who last assigned this ticket.",
    )
    assigned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent assignment.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_tickets",
    )
    due_date = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    first_responded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the first agent response (comment or assignment).",
    )
    sla_response_breached = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if the first-response SLA was breached.",
    )
    sla_resolution_breached = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if the resolution SLA was breached.",
    )
    tags = models.JSONField(default=list, blank=True)
    custom_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary key/value data for custom fields.",
    )

    class Meta:
        unique_together = [("tenant", "number")]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "assignee"]),
            models.Index(fields=["tenant", "priority"]),
            models.Index(fields=["tenant", "created_at"]),
        ]
        ordering = ["-created_at"]
        verbose_name = "ticket"
        verbose_name_plural = "tickets"

    def __str__(self):
        return f"#{self.number} {self.subject}"

    def save(self, *args, **kwargs):
        # Ensure tenant is set before number auto-increment so the
        # per-tenant query returns correct results.
        if not self.tenant_id:
            from main.context import get_current_tenant

            tenant = get_current_tenant()
            if tenant:
                self.tenant = tenant

        # Auto-assign a per-tenant ticket number on creation.
        if self.number is None or self.number == 0:
            last_number = (
                Ticket.unscoped.filter(tenant_id=self.tenant_id)
                .order_by("-number")
                .values_list("number", flat=True)
                .first()
            ) or 0
            self.number = last_number + 1
        super().save(*args, **kwargs)


class SLAPolicy(TenantScopedModel):
    """
    Service-Level Agreement policy for a given ticket priority.

    Defines target response and resolution times. When ``business_hours_only``
    is True, SLA clocks only tick during the tenant's configured working hours.
    """

    name = models.CharField(max_length=100)
    priority = models.CharField(
        max_length=10,
        choices=Ticket.Priority.choices,
    )
    first_response_minutes = models.PositiveIntegerField(
        help_text="Target time (in minutes) for the first agent response.",
    )
    resolution_minutes = models.PositiveIntegerField(
        help_text="Target time (in minutes) for full resolution.",
    )
    business_hours_only = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "priority")]
        ordering = ["priority"]
        verbose_name = "SLA policy"
        verbose_name_plural = "SLA policies"

    def __str__(self):
        return f"{self.name} ({self.get_priority_display()})"


class EscalationRule(TenantScopedModel):
    """
    Automated escalation rule attached to an ``SLAPolicy``.

    When the configured ``trigger`` threshold is breached, the system performs
    the specified ``action`` (assign, notify, or change priority).
    """

    class Trigger(models.TextChoices):
        RESPONSE_BREACH = "response_breach", "Response SLA breach"
        RESOLUTION_BREACH = "resolution_breach", "Resolution SLA breach"
        IDLE_TIME = "idle_time", "Idle time exceeded"

    class Action(models.TextChoices):
        ASSIGN = "assign", "Re-assign ticket"
        NOTIFY = "notify", "Send notification"
        CHANGE_PRIORITY = "change_priority", "Escalate priority"

    sla_policy = models.ForeignKey(
        SLAPolicy,
        on_delete=models.CASCADE,
        related_name="escalation_rules",
    )
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    threshold_minutes = models.PositiveIntegerField(
        help_text="Minutes after trigger condition before this rule fires.",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escalation_targets",
    )
    target_role = models.ForeignKey(
        "accounts.Role",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escalation_targets",
    )
    notify_message = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        verbose_name = "escalation rule"
        verbose_name_plural = "escalation rules"

    def __str__(self):
        return (
            f"{self.get_trigger_display()} -> {self.get_action_display()} "
            f"({self.sla_policy})"
        )


class TicketActivity(TenantScopedModel):
    """
    Human-readable timeline of events for a specific ticket.

    Displayed in the ticket detail UI as the ticket's lifecycle history.
    This is SEPARATE from the audit log (ActivityLog) which tracks WHO did
    WHAT for compliance. This model tracks WHAT happened to THIS ticket.

    Why separate?
    - ActivityLog is polymorphic (any entity), compliance-focused, stores
      structured diffs and IP addresses. Queried by auditors across entities.
    - TicketActivity is ticket-specific, human-readable, UI-optimized.
      Queried per-ticket for the detail page timeline.
    """

    class Event(models.TextChoices):
        CREATED = "created", "Created"
        ASSIGNED = "assigned", "Assigned"
        UNASSIGNED = "unassigned", "Unassigned"
        STATUS_CHANGED = "status_changed", "Status Changed"
        PRIORITY_CHANGED = "priority_changed", "Priority Changed"
        COMMENTED = "commented", "Commented"
        INTERNAL_NOTE = "internal_note", "Internal Note"
        CLOSED = "closed", "Closed"
        REOPENED = "reopened", "Reopened"
        ESCALATED = "escalated", "Escalated"
        ATTACHMENT_ADDED = "attachment_added", "Attachment Added"
        ATTACHMENT_REMOVED = "attachment_removed", "Attachment Removed"

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket_activities",
    )
    event = models.CharField(max_length=50, choices=Event.choices)
    message = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["ticket", "created_at"],
                name="ticketact_ticket_created",
            ),
        ]
        verbose_name = "ticket activity"
        verbose_name_plural = "ticket activities"

    def __str__(self):
        actor_str = self.actor.get_full_name() if self.actor else "System"
        return f"[#{self.ticket.number}] {actor_str}: {self.get_event_display()}"


class TicketAssignment(TenantScopedModel):
    """
    Immutable log of every ticket assignment change.

    Provides a complete audit trail of who was assigned a ticket, when, and by
    whom.
    """

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ticket_assignments",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket_assignments_made",
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ticket assignment"
        verbose_name_plural = "ticket assignments"

    def __str__(self):
        return f"Ticket #{self.ticket.number} -> {self.assigned_to}"
