"""
Models for the agents app.

Provides AgentAvailability for tracking agent online status, workload
capacity, and current ticket counts within each tenant, plus
CustomAgentStatus for tenant-curated extra status options.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from main.models import TenantScopedModel

# How fresh an agent's presence heartbeat must be for them to count as truly
# "online" for auto-assignment. A live ``/ws/live/`` socket re-stamps
# ``AgentAvailability.last_seen`` every ~25s; if the most recent stamp is older
# than this TTL the session is assumed dead (closed tab, network drop) and the
# agent is no longer assignable until they reconnect. Override per-deployment
# via ``AGENT_PRESENCE_TTL_SECONDS`` in settings.
DEFAULT_PRESENCE_TTL_SECONDS = 90


class AgentStatus(models.TextChoices):
    """Possible availability states for an agent."""

    ONLINE = "online", "Online"
    AWAY = "away", "Away"
    BUSY = "busy", "Busy"
    OFFLINE = "offline", "Offline"


BUILTIN_STATUS_SLUGS = frozenset(AgentStatus.values)


class StatusColor(models.TextChoices):
    """Fixed palette of dot colors available for custom statuses."""

    GREEN = "green", "Green"
    YELLOW = "yellow", "Yellow"
    RED = "red", "Red"
    BLUE = "blue", "Blue"
    PURPLE = "purple", "Purple"
    ORANGE = "orange", "Orange"
    PINK = "pink", "Pink"
    GRAY = "gray", "Gray"


STATUS_COLOR_HEX = {
    StatusColor.GREEN: "#22C55E",
    StatusColor.YELLOW: "#F59E0B",
    StatusColor.RED: "#EF4444",
    StatusColor.BLUE: "#3B82F6",
    StatusColor.PURPLE: "#8B5CF6",
    StatusColor.ORANGE: "#F97316",
    StatusColor.PINK: "#EC4899",
    StatusColor.GRAY: "#6B7280",
}


class CustomAgentStatus(TenantScopedModel):
    """
    Tenant-curated custom availability status (e.g. 'In Meeting', 'Lunch').

    Created and managed by admins/managers; selectable by any agent in the
    tenant via the navbar status dropdown alongside the four built-in
    statuses (online/away/busy/offline).
    """

    slug = models.SlugField(
        max_length=40,
        help_text="Stable identifier used by the API and dropdown.",
    )
    label = models.CharField(
        max_length=50,
        help_text="Display name shown in the dropdown (e.g. 'In Meeting').",
    )
    description = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Short helper text shown under the label.",
    )
    color = models.CharField(
        max_length=20,
        choices=StatusColor.choices,
        default=StatusColor.BLUE,
    )
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_agent_statuses_created",
    )

    class Meta:
        verbose_name = "custom agent status"
        verbose_name_plural = "custom agent statuses"
        unique_together = [("tenant", "slug")]
        ordering = ["order", "label"]

    def __str__(self):
        return self.label

    @property
    def color_hex(self):
        return STATUS_COLOR_HEX.get(self.color, STATUS_COLOR_HEX[StatusColor.BLUE])


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
    last_seen = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Last presence heartbeat from a live /ws/live/ socket. Re-stamped "
            "on connect and on each heartbeat ping; aged out by the presence "
            "reaper. Drives the freshness half of is_assignable."
        ),
    )
    working_hours = models.JSONField(
        default=dict, blank=True,
        help_text="Per-day working hours, e.g. {'mon': {'enabled': true, 'start': '09:00', 'end': '17:00'}, ...}",
    )
    auto_away_outside_hours = models.BooleanField(
        default=False,
        help_text="Automatically set status to away outside working hours.",
    )
    custom_status = models.ForeignKey(
        "agents.CustomAgentStatus",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        help_text=(
            "When set, takes precedence over the built-in status for display "
            "purposes. Cleared when the agent picks a built-in status."
        ),
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

    @property
    def presence_fresh(self):
        """True if the last heartbeat is within the presence TTL.

        A stale ``online`` row (browser closed, network dropped) returns
        False here even though ``status`` may still read ``online`` in the DB
        until the reaper task flips it.
        """
        if self.last_seen is None:
            return False
        ttl = getattr(settings, "AGENT_PRESENCE_TTL_SECONDS", DEFAULT_PRESENCE_TTL_SECONDS)
        return (timezone.now() - self.last_seen).total_seconds() <= ttl

    @property
    def is_assignable(self):
        """True if this agent may receive a NEW auto-assigned item right now.

        Stricter than :pyattr:`is_available`: in addition to ONLINE + spare
        capacity, the agent must have a *fresh* presence heartbeat. This is the
        single gate the Inbox Hub AssignmentEngine consults so work is never
        routed to a dead session. When ``auto_away_outside_hours`` is set, the
        agent is also only assignable inside their configured working hours.
        """
        if self.status != AgentStatus.ONLINE:
            return False
        if self.remaining_capacity <= 0:
            return False
        if not self.presence_fresh:
            return False
        if self.auto_away_outside_hours and not self._within_working_hours():
            return False
        return True

    def _within_working_hours(self):
        """Best-effort check of the agent's per-day ``working_hours`` JSON.

        Shape: ``{"mon": {"enabled": true, "start": "09:00", "end": "17:00"}, ...}``.
        Returns True when working hours are not configured for the current day
        (fail-open) so an unconfigured agent is never silently excluded. Uses
        server-local time (TIME_ZONE); per-tenant timezone refinement is a
        later enhancement.
        """
        hours = self.working_hours or {}
        if not isinstance(hours, dict) or not hours:
            return True
        now_local = timezone.localtime()
        day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now_local.weekday()]
        day = hours.get(day_key)
        if not isinstance(day, dict) or not day.get("enabled"):
            # No window configured for today → fail-open (don't exclude).
            return True
        start = str(day.get("start") or "00:00")
        end = str(day.get("end") or "23:59")
        current = now_local.strftime("%H:%M")
        return start <= current <= end
