"""
Models for the agents app.

Provides AgentAvailability for tracking agent online status, workload
capacity, and current ticket counts within each tenant.
"""

from django.conf import settings
from django.db import models

from main.models import TenantScopedModel


class AgentStatus(models.TextChoices):
    """Possible availability states for an agent."""

    ONLINE = "online", "Online"
    AWAY = "away", "Away"
    BUSY = "busy", "Busy"
    OFFLINE = "offline", "Offline"


class AgentAvailability(TenantScopedModel):
    """
    Tenant-scoped agent availability and workload tracker.

    Each user has at most one AgentAvailability record per tenant (enforced
    by unique_together). The ``current_ticket_count`` is recalculated from
    actual open assigned tickets to stay in sync.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_availability",
    )
    status = models.CharField(
        max_length=20,
        choices=AgentStatus.choices,
        default=AgentStatus.OFFLINE,
    )
    max_concurrent_tickets = models.PositiveIntegerField(
        default=10,
        help_text="Maximum number of tickets this agent can handle concurrently.",
    )
    current_ticket_count = models.PositiveIntegerField(
        default=0,
        help_text="Current number of open tickets assigned to this agent.",
    )
    status_message = models.CharField(
        max_length=100, null=True, blank=True,
        help_text="Custom status message (e.g. 'In a meeting').",
    )
    last_activity = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the agent's most recent activity.",
    )
    working_hours = models.JSONField(
        default=dict, blank=True,
        help_text="Per-day working hours, e.g. {'mon': {'enabled': true, 'start': '09:00', 'end': '17:00'}, ...}",
    )
    auto_away_outside_hours = models.BooleanField(
        default=False,
        help_text="Automatically set status to away outside working hours.",
    )

    class Meta:
        verbose_name = "agent availability"
        verbose_name_plural = "agent availabilities"
        unique_together = [("tenant", "user")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.get_status_display()}"

    @property
    def is_available(self):
        """Return True if the agent is online and has capacity."""
        return (
            self.status == AgentStatus.ONLINE
            and self.current_ticket_count < self.max_concurrent_tickets
        )

    @property
    def remaining_capacity(self):
        """Return the number of additional tickets this agent can accept."""
        return max(0, self.max_concurrent_tickets - self.current_ticket_count)
