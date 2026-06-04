"""
Inbox Hub data model.

Backing store for the Email-to-Queue Hub: inbound emails land here for agent
triage instead of auto-creating tickets. A HubEmail wraps an existing
InboundEmail (1:1) with workspace state (queue, assignee, SLA, lifecycle).

See /home/kavin/.claude/plans/plan-mode-only-do-polymorphic-catmull.md for the
full architecture; this module corresponds to plan section 2 ("Database Design").
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from main.models import TenantScopedModel


class Department(TenantScopedModel):
    """Tenant-scoped org unit that owns one-or-more ticket Queues."""

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    description = models.TextField(blank=True)
    lead = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="led_departments",
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="DepartmentMembership",
        related_name="departments",
        blank=True,
    )
    default_queue = models.ForeignKey(
        "tickets.Queue",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_departments",
    )
    business_hours = models.ForeignKey(
        "tickets.BusinessHours",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "slug"],
                name="ih_department_tenant_slug_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "is_active"], name="ih_dept_tenant_active"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


class DepartmentMembership(TenantScopedModel):
    """Through-model for Department.members.

    The ``skills`` JSON list is populated in Phase 3 when SkillBasedStrategy
    arrives; for MVP it stays empty. Materialising the through-model now
    avoids a destructive M2M-to-through migration later.
    """

    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="department_memberships",
    )
    skills = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["department", "user"],
                name="ih_deptmember_dept_user_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.department_id}"


class HubEmail(TenantScopedModel):
    """An inbound email parked in the Hub awaiting agent action.

    Wraps a single inbound_email.InboundEmail row (1:1). The InboundEmail
    remains the transport-layer record (parse + threading + idempotency);
    HubEmail carries the workspace lifecycle (state, assignment, SLA).
    """

    class State(models.TextChoices):
        NEW = "new", "New"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In Progress"
        PENDING_AGENT = "pending_agent", "Pending Agent"
        AWAITING_CUSTOMER = "awaiting_customer", "Awaiting Customer"
        ESCALATED = "escalated", "Escalated"
        RESOLVED = "resolved", "Resolved"
        CONVERTED_TO_TICKET = "converted_to_ticket", "Converted to Ticket"
        DISMISSED = "dismissed", "Dismissed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    # Relationships
    inbound = models.OneToOneField(
        "inbound_email.InboundEmail",
        on_delete=models.CASCADE,
        related_name="hub_email",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hub_emails",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name="hub_emails",
    )
    queue = models.ForeignKey(
        "tickets.Queue",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name="hub_emails",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name="assigned_hub_emails",
    )

    # Lifecycle
    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.NEW,
        db_index=True,
    )
    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.NORMAL,
        db_index=True,
    )
    category = models.CharField(max_length=64, blank=True)
    tags = models.JSONField(default=list, blank=True)

    # SLA
    sla_response_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    sla_resolution_due_at = models.DateTimeField(null=True, blank=True)
    response_breached = models.BooleanField(default=False)
    resolution_breached = models.BooleanField(default=False)
    first_responded_at = models.DateTimeField(null=True, blank=True)
    first_assigned_at = models.DateTimeField(null=True, blank=True)
    pause_started_at = models.DateTimeField(null=True, blank=True)
    total_pause_seconds = models.PositiveIntegerField(default=0)

    # Escalation
    escalation_count = models.PositiveIntegerField(default=0)
    escalated_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # Terminal paths
    converted_ticket = models.OneToOneField(
        "tickets.Ticket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="origin_hub_email",
    )
    dismissed_at = models.DateTimeField(null=True, blank=True)
    dismissed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    dismissal_reason = models.CharField(max_length=255, blank=True)

    # Future AI classifier drop-zone
    auto_classification_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["tenant", "state", "-created_at"],
                name="ih_email_t_state_created",
            ),
            models.Index(
                fields=["tenant", "queue", "state"],
                name="ih_email_t_queue_state",
            ),
            models.Index(
                fields=["tenant", "assignee", "state"],
                name="ih_email_t_assignee_state",
            ),
            models.Index(
                fields=["tenant", "department", "state"],
                name="ih_email_t_dept_state",
            ),
            # Partial index for the active-SLA hot path used by
            # check_hub_email_sla_breaches and the SLA-risk saved view.
            models.Index(
                fields=["tenant", "sla_response_due_at"],
                condition=Q(state__in=["new", "assigned", "in_progress", "pending_agent"]),
                name="ih_email_active_sla_due",
            ),
        ]

    def __str__(self) -> str:
        return f"HubEmail<{self.id} state={self.state}>"


class HubEmailAssignment(TenantScopedModel):
    """Immutable audit row for every assign / reassign / escalation.

    Append-only by convention (no service mutates after insert). Mirrors
    apps.tickets.models.TicketAssignment.
    """

    class Reason(models.TextChoices):
        AUTO = "auto", "Auto"
        MANUAL = "manual", "Manual"
        ESCALATION = "escalation", "Escalation"
        REASSIGNMENT = "reassignment", "Reassignment"

    hub_email = models.ForeignKey(
        HubEmail,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hub_email_assignments",
    )
    # null = system / engine assignment with no human actor.
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["hub_email", "-created_at"],
                name="ih_assign_email_created",
            ),
        ]


class HubEmailNote(TenantScopedModel):
    """Internal agent note attached to a HubEmail.

    Distinct from a ticket Comment: notes here belong to the triage workspace
    and do not carry over when an email is converted to a ticket.
    """

    hub_email = models.ForeignKey(
        HubEmail,
        on_delete=models.CASCADE,
        related_name="notes",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hub_email_notes",
    )
    body = models.TextField()

    class Meta:
        ordering = ["created_at"]


class HubEmailSLA(TenantScopedModel):
    """Per-(queue, priority) or per-(department, priority) SLA policy.

    Resolution order at runtime: queue+priority → department+priority →
    tenant default (set in TenantSettings; not modelled here).
    """

    queue = models.ForeignKey(
        "tickets.Queue",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="hub_email_slas",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="hub_email_slas",
    )
    priority = models.CharField(max_length=10, choices=HubEmail.Priority.choices)
    response_minutes = models.PositiveIntegerField()
    resolution_minutes = models.PositiveIntegerField()
    escalation_minutes = models.PositiveIntegerField()

    class Meta:
        constraints = [
            # Conditional uniqueness so NULL columns don't collide.
            # Supported on PostgreSQL and on SQLite >= 3.30.
            models.UniqueConstraint(
                fields=["queue", "priority"],
                condition=Q(queue__isnull=False),
                name="ih_sla_queue_priority_uniq",
            ),
            models.UniqueConstraint(
                fields=["department", "priority"],
                condition=Q(department__isnull=False),
                name="ih_sla_dept_priority_uniq",
            ),
        ]


class RoutingRule(TenantScopedModel):
    """Ordered IF/THEN rule consulted by RoutingEngine.classify_and_route.

    ``match`` JSON shape:
        {
            "sender_domain":   ["acme.com", "other.co"],
            "subject_regex":   "...",
            "recipient_local": ["billing", "support"],
            "keyword":         ["invoice", "refund"]
        }
    All keys optional; multiple keys are AND-combined, values within a key
    are OR-combined.
    """

    name = models.CharField(max_length=120)
    order = models.PositiveIntegerField(default=100, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    match = models.JSONField(default=dict)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routing_rules",
    )
    queue = models.ForeignKey(
        "tickets.Queue",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routing_rules",
    )
    category = models.CharField(max_length=64, blank=True)
    # Optional override; null = rule does not change priority.
    priority = models.CharField(
        max_length=10,
        choices=HubEmail.Priority.choices,
        null=True,
        blank=True,
    )
    stop_on_match = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]
        indexes = [
            models.Index(fields=["tenant", "is_active", "order"], name="ih_rule_active_order"),
        ]

    def __str__(self) -> str:
        return f"RoutingRule<{self.order} {self.name}>"


class QueueRouting(TenantScopedModel):
    """1:1 supplement to tickets.Queue carrying Hub assignment config.

    Lives in inbox_hub (not tickets) so the tickets app stays unaware of
    Hub-specific routing knobs. AssignmentEngine reads ``strategy_code`` to
    build the strategy chain.
    """

    queue = models.OneToOneField(
        "tickets.Queue",
        on_delete=models.CASCADE,
        related_name="hub_routing",
    )
    # Pipe-delimited fallback chain consumed by FallbackChainStrategy.
    strategy_code = models.CharField(
        max_length=80,
        default="availability_aware|least_loaded|round_robin",
    )
    leave_unassigned_when_no_match = models.BooleanField(default=True)
