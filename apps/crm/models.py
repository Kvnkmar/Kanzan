"""
CRM activity models for scheduling calls, emails, meetings, and tasks.

Provides the ``Activity`` model for tracking CRM-style activities linked
to tickets, contacts, or standalone. Supports follow-up tracking and
agent task queues.

Also provides the ``Reminder`` model for scheduling reminders
calls/contacts with overdue tracking, completion, cancellation, and
rescheduling.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from main.models import TenantScopedModel


class Activity(TenantScopedModel):
    """
    A scheduled CRM activity (call, email, meeting, or task).

    Can be linked to a ticket and/or contact. When linked to a ticket,
    saving an activity atomically updates ``ticket.last_activity_at``.
    """

    class ActivityType(models.TextChoices):
        CALL = "call", "Call"
        EMAIL = "email", "Email"
        MEETING = "meeting", "Meeting"
        TASK = "task", "Task"

    activity_type = models.CharField(
        max_length=10,
        choices=ActivityType.choices,
    )
    subject = models.CharField(max_length=255)
    notes = models.TextField(blank=True, default="")
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=255, blank=True, default="")

    ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activities_crm",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities_crm",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="crm_activities_created",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crm_activities_assigned",
    )

    class Meta:
        ordering = ["due_at"]
        verbose_name = "activity"
        verbose_name_plural = "activities"
        indexes = [
            models.Index(fields=["tenant", "assigned_to", "completed_at"]),
            models.Index(fields=["tenant", "ticket"]),
            models.Index(fields=["tenant", "due_at"]),
        ]

    def __str__(self):
        return f"{self.get_activity_type_display()}: {self.subject}"


class ReminderQuerySet(models.QuerySet):
    """Custom queryset with reminder-specific filtering helpers."""

    def overdue(self, now=None):
        """Return reminders that are overdue: pending + scheduled_at < now."""
        if now is None:
            now = timezone.now()
        return self.filter(
            scheduled_at__lt=now,
            completed_at__isnull=True,
            cancelled_at__isnull=True,
        )

    def pending(self):
        """Return reminders that are pending (not completed, not cancelled)."""
        return self.filter(
            completed_at__isnull=True,
            cancelled_at__isnull=True,
        )

    def for_user(self, user):
        """Return reminders assigned to a specific user."""
        return self.filter(assigned_to=user)


class ReminderManager(models.Manager.from_queryset(ReminderQuerySet)):
    """Tenant-aware manager that uses ReminderQuerySet."""

    def get_queryset(self):
        from main.context import get_current_tenant

        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        else:
            qs = qs.none()
        return qs


ReminderUnscopedManager = models.Manager.from_queryset(ReminderQuerySet)


class Reminder(TenantScopedModel):
    """
    A scheduled reminder for a contact (calls, emails, follow-ups).

    Overdue is computed dynamically:
        overdue = scheduled_at < now AND completed_at IS NULL AND cancelled_at IS NULL

    Status is derived, not stored:
        - completed: completed_at IS NOT NULL
        - cancelled: cancelled_at IS NOT NULL
        - overdue: pending + scheduled_at < now
        - pending: not completed, not cancelled, scheduled_at >= now
    """

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    subject = models.CharField(max_length=255)
    notes = models.TextField(blank=True, default="")
    scheduled_at = models.DateTimeField(
        db_index=True,
        help_text="When this reminder is due.",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )

    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reminders",
    )
    ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reminders",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reminders_assigned",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reminders_created",
    )

    objects = ReminderManager()
    unscoped = ReminderUnscopedManager()

    class Meta:
        ordering = ["scheduled_at"]
        verbose_name = "reminder"
        verbose_name_plural = "reminders"
        indexes = [
            models.Index(
                fields=["tenant", "scheduled_at", "completed_at", "cancelled_at"],
                name="reminder_overdue_idx",
            ),
            models.Index(fields=["tenant", "assigned_to"]),
            models.Index(fields=["tenant", "contact"]),
        ]

    def __str__(self):
        return f"Reminder: {self.subject} ({self.get_status_display()})"

    @property
    def status(self):
        """Compute status dynamically."""
        if self.completed_at is not None:
            return "completed"
        if self.cancelled_at is not None:
            return "cancelled"
        if self.scheduled_at < timezone.now():
            return "overdue"
        return "pending"

    def get_status_display(self):
        return self.status.replace("_", " ").title()

    @property
    def is_overdue(self):
        return (
            self.completed_at is None
            and self.cancelled_at is None
            and self.scheduled_at < timezone.now()
        )

    @property
    def overdue_duration(self):
        """Return timedelta of how long this reminder has been overdue, or None."""
        if self.is_overdue:
            return timezone.now() - self.scheduled_at
        return None

    def mark_completed(self, completed_at=None):
        """Mark this reminder as completed."""
        self.completed_at = completed_at or timezone.now()
        self.save(update_fields=["completed_at", "updated_at"])

    def mark_cancelled(self, cancelled_at=None):
        """Mark this reminder as cancelled."""
        self.cancelled_at = cancelled_at or timezone.now()
        self.save(update_fields=["cancelled_at", "updated_at"])

    def reschedule(self, new_scheduled_at, note=None):
        """Reschedule this reminder to a new datetime."""
        self.scheduled_at = new_scheduled_at
        if note:
            self.notes = f"{self.notes}\n---\nRescheduled: {note}".strip()
        self.save(update_fields=["scheduled_at", "notes", "updated_at"])
