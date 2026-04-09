"""
Ticket system models for multi-tenant CRM platform.

Provides customisable ticket statuses, queues, SLA policies with escalation
rules, and full assignment history tracking -- all scoped per tenant.
"""

import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F
from django.utils import timezone

from main.models import TenantScopedModel, TimestampedModel


# ---------------------------------------------------------------------------
# Business hours defaults
# ---------------------------------------------------------------------------


def default_business_hours_schedule():
    """Default schedule: Mon–Fri 09:00–17:00, Sat–Sun off."""
    schedule = {}
    for day in range(7):  # 0=Mon .. 6=Sun
        schedule[str(day)] = {
            "is_active": day < 5,
            "open_time": "09:00",
            "close_time": "17:00",
        }
    return schedule


class Pipeline(TenantScopedModel):
    """
    A sales or service pipeline belonging to a tenant.

    Pipelines define a sequence of stages that tickets (deals) move through.
    Each tenant can have multiple pipelines for different workflows.
    """

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    is_default = models.BooleanField(
        default=False,
        help_text="If True, new deal-type tickets default to this pipeline.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "pipeline"
        verbose_name_plural = "pipelines"

    def __str__(self):
        return self.name


class PipelineStage(TenantScopedModel):
    """
    A stage within a pipeline (e.g. Qualification, Proposal, Negotiation).

    Stages are ordered by ``order`` and can be flagged as won/lost terminal
    states. ``probability`` is a default win-probability percentage (0-100)
    for deals entering this stage.
    """

    pipeline = models.ForeignKey(
        Pipeline,
        on_delete=models.CASCADE,
        related_name="stages",
    )
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(
        help_text="Display order within the pipeline.",
    )
    color = models.CharField(max_length=7, default="#6c757d")
    probability = models.PositiveIntegerField(
        default=0,
        help_text="Default win probability percentage (0-100) for this stage.",
    )
    is_won = models.BooleanField(
        default=False,
        help_text="If True, reaching this stage marks the deal as won.",
    )
    is_lost = models.BooleanField(
        default=False,
        help_text="If True, reaching this stage marks the deal as lost.",
    )

    class Meta:
        unique_together = [("pipeline", "order")]
        ordering = ["order"]
        verbose_name = "pipeline stage"
        verbose_name_plural = "pipeline stages"

    def __str__(self):
        return f"{self.pipeline.name} - {self.name}"


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
    pauses_sla = models.BooleanField(
        default=False,
        help_text="If True, SLA clocks pause while a ticket is in this status.",
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


class TicketCounter(TimestampedModel):
    """
    Atomic per-tenant ticket number counter.

    One row per tenant, incremented with F() + select_for_update() to prevent
    duplicate ticket numbers under concurrent writes.
    """

    tenant = models.OneToOneField(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="ticket_counter",
    )
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "ticket counter"
        verbose_name_plural = "ticket counters"

    def __str__(self):
        return f"{self.tenant.slug}: {self.last_number}"

    @classmethod
    def next_number(cls, tenant_id):
        """
        Atomically increment and return the next ticket number for a tenant.

        Uses select_for_update() to serialise concurrent callers. The row is
        created on first use (get_or_create) so no manual provisioning is needed.
        """
        counter, _ = cls.objects.select_for_update().get_or_create(
            tenant_id=tenant_id,
            defaults={"last_number": 0},
        )
        counter.last_number = F("last_number") + 1
        counter.save(update_fields=["last_number", "updated_at"])
        counter.refresh_from_db(fields=["last_number"])
        return counter.last_number


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

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        PORTAL = "portal", "Portal"
        AGENT = "agent", "Agent"
        CHAT = "chat", "Chat"

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

    # --- Phase 1 intake fields ---
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.AGENT,
        help_text="Intake channel that created this ticket.",
    )

    # --- SLA deadline fields ---
    sla_policy = models.ForeignKey(
        "tickets.SLAPolicy",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
        help_text="The SLA policy applied to this ticket.",
    )
    sla_first_response_due = models.DateTimeField(
        null=True,
        blank=True,
        help_text="UTC deadline for first agent response.",
    )
    sla_resolution_due = models.DateTimeField(
        null=True,
        blank=True,
        help_text="UTC deadline for full resolution.",
    )
    sla_paused_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the SLA clock was last paused. Null if not paused.",
    )
    sla_extended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When an SLA deadline extension was last applied via escalation.",
    )

    # --- Wait-status restore ---
    pre_wait_status = models.ForeignKey(
        TicketStatus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=(
            "Snapshot of the status before entering a pauses_sla status. "
            "Used to restore the previous status when leaving Waiting."
        ),
    )

    # --- Status transition tracking ---
    status_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent status transition.",
    )
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket_status_changes",
        help_text="User who triggered the most recent status transition.",
    )

    # --- Escalation tracking ---
    escalation_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this ticket has been escalated.",
    )
    escalated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent escalation.",
    )

    # --- Closure / CSAT ---
    solved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the ticket moved to 'resolved' status.",
    )
    auto_close_task_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Celery task ID of the pending auto-close task.",
    )
    csat_rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Customer satisfaction rating (1-5).",
    )
    csat_comment = models.TextField(
        blank=True,
        default="",
        help_text="Optional customer feedback text.",
    )
    csat_submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the CSAT survey was submitted.",
    )
    needs_kb_article = models.BooleanField(
        default=False,
        help_text="Set by post-closure check if category lacks KB articles.",
    )
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_from",
        help_text="If this ticket was merged, points to the primary ticket.",
    )

    # --- CRM follow-up tracking ---
    follow_up_due_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a follow-up action is due for this ticket.",
    )
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent CRM activity on this ticket.",
    )

    # --- CRM pipeline fields ---
    class TicketType(models.TextChoices):
        SUPPORT = "support", "Support"
        DEAL = "deal", "Deal"
        INQUIRY = "inquiry", "Inquiry"

    ticket_type = models.CharField(
        max_length=20,
        choices=TicketType.choices,
        default=TicketType.SUPPORT,
        help_text="Classifies the ticket as support, deal, or inquiry.",
    )
    deal_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Monetary value of the deal (for deal-type tickets).",
    )
    expected_close_date = models.DateField(
        null=True,
        blank=True,
        help_text="Expected close date for deal-type tickets.",
    )
    probability = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Win probability percentage (0-100) for deal-type tickets.",
    )
    account = models.ForeignKey(
        "contacts.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
        help_text="CRM account associated with this ticket.",
    )
    pipeline_stage = models.ForeignKey(
        "tickets.PipelineStage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
        help_text="Current pipeline stage (for deal-type tickets).",
    )
    won_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the deal was marked as won.",
    )
    lost_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the deal was marked as lost.",
    )
    won_reason = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text="Reason the deal was won.",
    )
    lost_reason = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text="Reason the deal was lost.",
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
        # Uses TicketCounter with select_for_update() for concurrency safety.
        # NOTE: select_for_update() is a no-op on SQLite, so concurrent writes
        # in dev can still race — acceptable since production uses PostgreSQL.
        if (self.number is None or self.number == 0) and self.tenant_id:
            from django.db import transaction

            with transaction.atomic():
                self.number = TicketCounter.next_number(self.tenant_id)
        super().save(*args, **kwargs)


class TicketLink(TenantScopedModel):
    """
    A directed link between two tickets within the same tenant.

    Supports duplicate tracking, dependency chains, and general relation
    markers. Links are stored as a single directed record but displayed
    bidirectionally in the UI.
    """

    class LinkType(models.TextChoices):
        DUPLICATE_OF = "duplicate_of", "Duplicate of"
        RELATED_TO = "related_to", "Related to"
        BLOCKS = "blocks", "Blocks"
        BLOCKED_BY = "blocked_by", "Blocked by"

    source_ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="outgoing_links",
    )
    target_ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="incoming_links",
    )
    link_type = models.CharField(
        max_length=20,
        choices=LinkType.choices,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )

    class Meta:
        unique_together = [("source_ticket", "target_ticket", "link_type")]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_ticket=models.F("target_ticket")),
                name="ticketlink_no_self_link",
            ),
        ]
        ordering = ["-created_at"]
        verbose_name = "ticket link"
        verbose_name_plural = "ticket links"

    def __str__(self):
        return f"#{self.source_ticket_id} {self.get_link_type_display()} #{self.target_ticket_id}"

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        if (
            self.source_ticket_id
            and self.target_ticket_id
            and self.source_ticket.tenant_id != self.target_ticket.tenant_id
        ):
            raise ValidationError("Both tickets must belong to the same tenant.")


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


class BusinessHours(TenantScopedModel):
    """
    Per-tenant business hours configuration for SLA calculations.

    The ``schedule`` JSONField stores per-day open/close times::

        {
            "0": {"is_active": true, "open_time": "09:00", "close_time": "17:00"},
            "1": {"is_active": true, "open_time": "09:00", "close_time": "17:00"},
            ...
            "6": {"is_active": false, "open_time": "09:00", "close_time": "17:00"}
        }

    Keys are ISO weekday integers as strings (``"0"``=Monday .. ``"6"``=Sunday).
    Falls back to 24/7 when no ``BusinessHours`` row exists for a tenant.
    """

    timezone = models.CharField(
        max_length=50,
        default="UTC",
        help_text="IANA timezone name (e.g. 'America/New_York').",
    )
    schedule = models.JSONField(
        default=default_business_hours_schedule,
        help_text="Per-day open/close times. Keys are weekday ints 0–6 as strings.",
    )

    class Meta:
        verbose_name = "business hours"
        verbose_name_plural = "business hours"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"],
                name="unique_business_hours_per_tenant",
            ),
        ]

    def __str__(self):
        active_days = sum(
            1 for d in self.schedule.values()
            if isinstance(d, dict) and d.get("is_active")
        )
        return f"Business hours for {self.tenant} ({active_days} active days)"

    def clean(self):
        super().clean()
        if not isinstance(self.schedule, dict):
            raise ValidationError({"schedule": "Must be a JSON object."})
        for key, day in self.schedule.items():
            if key not in {str(i) for i in range(7)}:
                raise ValidationError(
                    {"schedule": f"Invalid day key: {key}. Must be '0'..'6'."}
                )
            if not isinstance(day, dict):
                raise ValidationError(
                    {"schedule": f"Day {key} must be an object."}
                )
            if day.get("is_active"):
                for field in ("open_time", "close_time"):
                    val = day.get(field)
                    if not val:
                        raise ValidationError(
                            {"schedule": f"Day {key}: {field} is required when active."}
                        )
                    try:
                        datetime.time.fromisoformat(val)
                    except (ValueError, TypeError):
                        raise ValidationError(
                            {"schedule": f"Day {key}: {field} must be HH:MM format."}
                        )

    def get_day_config(self, weekday: int):
        """Return (is_active, open_time, close_time) for a given weekday int."""
        day = self.schedule.get(str(weekday), {})
        if not isinstance(day, dict) or not day.get("is_active"):
            return False, None, None
        return (
            True,
            datetime.time.fromisoformat(day["open_time"]),
            datetime.time.fromisoformat(day["close_time"]),
        )

    def weekly_business_minutes(self):
        """Return total business minutes per week."""
        total = 0
        for weekday in range(7):
            is_active, open_t, close_t = self.get_day_config(weekday)
            if is_active and open_t and close_t and open_t < close_t:
                delta = datetime.datetime.combine(datetime.date.min, close_t) - \
                        datetime.datetime.combine(datetime.date.min, open_t)
                total += delta.total_seconds() / 60
        return int(total)


class PublicHoliday(TenantScopedModel):
    """
    Public holiday that pauses SLA clocks for the entire day.

    Holidays are specific to a tenant and a calendar date. Business hours
    utilities skip any day that has a matching ``PublicHoliday`` record.
    """

    date = models.DateField(help_text="The holiday date.")
    name = models.CharField(max_length=200, help_text="Holiday name (e.g. 'Christmas Day').")

    class Meta:
        unique_together = [("tenant", "date")]
        ordering = ["date"]
        verbose_name = "public holiday"
        verbose_name_plural = "public holidays"

    def __str__(self):
        return f"{self.name} ({self.date})"


class SLAPause(TenantScopedModel):
    """
    Records a period during which the SLA clock was paused for a ticket.

    Created when a ticket transitions to a ``pauses_sla=True`` status
    (e.g. "Waiting on Customer"). Closed (``resumed_at`` set) when the
    ticket leaves that status or a customer reply arrives.
    """

    class Reason(models.TextChoices):
        WAITING_ON_CUSTOMER = "waiting_on_customer", "Waiting on customer"
        MANUAL = "manual", "Manual pause"

    ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.CASCADE,
        related_name="sla_pauses",
    )
    paused_at = models.DateTimeField(
        help_text="When the SLA clock was paused.",
    )
    resumed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the SLA clock was resumed. Null if still paused.",
    )
    reason = models.CharField(
        max_length=30,
        choices=Reason.choices,
        default=Reason.WAITING_ON_CUSTOMER,
    )

    class Meta:
        ordering = ["-paused_at"]
        indexes = [
            models.Index(
                fields=["ticket", "resumed_at"],
                name="slapause_ticket_resumed",
            ),
        ]
        verbose_name = "SLA pause"
        verbose_name_plural = "SLA pauses"

    def __str__(self):
        state = "active" if self.resumed_at is None else "closed"
        return f"SLA pause for #{self.ticket.number} ({state})"

    @property
    def duration_minutes(self):
        """Return pause duration in minutes. Uses now() if still open."""
        end = self.resumed_at or timezone.now()
        return (end - self.paused_at).total_seconds() / 60


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
        ESCALATED_MANUAL = "escalated_manual", "Manually Escalated"
        ATTACHMENT_ADDED = "attachment_added", "Attachment Added"
        ATTACHMENT_REMOVED = "attachment_removed", "Attachment Removed"
        SLA_PAUSED = "sla_paused", "SLA Paused"
        SLA_RESUMED = "sla_resumed", "SLA Resumed"
        AUTO_CLOSED = "auto_closed", "Auto-closed"
        CSAT_RECEIVED = "csat_received", "CSAT Received"
        FIRST_RESPONSE = "first_response", "First Response Sent"
        PIPELINE_STAGE_CHANGED = "pipeline_stage_changed", "Pipeline Stage Changed"
        EMAIL_LINKED = "email_linked", "Email Linked"
        EMAIL_ACTIONED = "email_actioned", "Email Actioned"

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket_activities",
        help_text="Contact associated with the ticket at the time of this event.",
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


class CannedResponse(TenantScopedModel):
    """
    Pre-written response templates for agent productivity.

    Agents create reusable reply snippets (optionally with a ``/shortcut``
    trigger) that can be inserted into the comment composer on the ticket
    detail page. Template variables like ``{{ticket.number}}`` are replaced
    at render time.
    """

    title = models.CharField(max_length=200, help_text="Display name for the response")
    content = models.TextField(
        help_text="Response content. Supports template variables like {{ticket.number}}."
    )
    category = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Grouping label, e.g. 'Billing', 'Technical', 'General'.",
    )
    shortcut = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Quick trigger like '/thanks' or '/refund'.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="canned_responses",
    )
    is_shared = models.BooleanField(
        default=True,
        help_text="False = personal to creator only.",
    )
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["category", "title"]
        indexes = [
            models.Index(fields=["tenant", "is_shared"]),
            models.Index(fields=["shortcut"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "shortcut"],
                condition=~models.Q(shortcut=""),
                name="unique_shortcut_per_tenant",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.shortcut or 'no shortcut'})"


class Macro(TenantScopedModel):
    """
    Agent macro: a reusable body template with optional ticket actions.

    When applied to a ticket, the body is rendered with variable substitution
    and posted as a comment, then each action in ``actions`` is executed
    atomically (set_status, set_priority, add_tag).
    """

    name = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True, default="")
    body = models.TextField(
        help_text=(
            "Template body. Supports variables: "
            "{{ticket.number}}, {{ticket.subject}}, {{contact.name}}, "
            "{{agent.name}}, {{ticket.queue}}"
        ),
    )
    actions = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'List of actions, e.g. [{"action": "set_status", "value": "resolved"}, '
            '{"action": "set_priority", "value": "low"}, '
            '{"action": "add_tag", "value": "billing"}]'
        ),
    )
    is_shared = models.BooleanField(
        default=True,
        help_text="False = personal to creator only.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="macros",
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["tenant", "is_shared"]),
        ]
        verbose_name = "macro"
        verbose_name_plural = "macros"

    def __str__(self):
        return self.name


class SavedView(TenantScopedModel):
    """
    Saved filter configurations for tickets and contacts.

    Users can save their current filter state as a named view to avoid
    re-applying filters on every page load. Views can be personal (linked
    to a user) or shared (``user=NULL``).
    """

    class ResourceType(models.TextChoices):
        TICKET = "ticket", "Tickets"
        CONTACT = "contact", "Contacts"

    name = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=20, choices=ResourceType.choices)
    filters = models.JSONField(default=dict, help_text="Filter parameters as JSON")
    sort_field = models.CharField(max_length=50, default="-created_at")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="saved_views",
        help_text="null = shared view visible to all tenant members.",
    )
    is_default = models.BooleanField(
        default=False, help_text="Load this view by default."
    )
    is_pinned = models.BooleanField(
        default=False, help_text="Pin to top of view selector."
    )

    class Meta:
        ordering = ["-is_pinned", "name"]
        indexes = [
            models.Index(fields=["tenant", "resource_type", "user"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "user", "name", "resource_type"],
                name="unique_view_per_user_resource",
            ),
        ]

    def __str__(self):
        scope = "Shared" if self.user is None else f"Personal ({self.user.email})"
        return f"{self.name} - {self.get_resource_type_display()} ({scope})"


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

