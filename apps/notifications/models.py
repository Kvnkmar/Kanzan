"""
Notification models for the multi-tenant CRM platform.

Provides:
- ``Notification`` -- individual notification records delivered to users.
- ``NotificationPreference`` -- per-user, per-tenant delivery preferences
  (in-app and/or email) for each notification type.
"""

from django.conf import settings
from django.db import models

from main.models import TenantScopedModel


class NotificationType(models.TextChoices):
    """Enumeration of all supported notification types."""

    TICKET_ASSIGNED = "ticket_assigned", "Ticket Assigned"
    TICKET_UPDATED = "ticket_updated", "Ticket Updated"
    TICKET_COMMENT = "ticket_comment", "Ticket Comment"
    MENTION = "mention", "Mention"
    MESSAGE = "message", "Message"
    SLA_BREACH = "sla_breach", "SLA Breach"
    TICKET_OVERDUE = "ticket_overdue", "Ticket Overdue"
    PAYMENT_FAILED = "payment_failed", "Payment Failed"
    SUBSCRIPTION_CHANGE = "subscription_change", "Subscription Change"
    INVITATION = "invitation", "Invitation"


class Notification(TenantScopedModel):
    """
    A single notification delivered to a user within a tenant.

    The ``data`` JSONField carries arbitrary context (e.g. ticket ID, URL)
    that the frontend can use to render deep links and rich previews.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    type = models.CharField(
        max_length=50,
        choices=NotificationType.choices,
    )
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "notification"
        verbose_name_plural = "notifications"
        indexes = [
            models.Index(
                fields=["recipient", "is_read", "-created_at"],
                name="notif_recipient_read_created",
            ),
        ]

    def __str__(self):
        return f"[{self.get_type_display()}] {self.title} -> {self.recipient}"

    def mark_read(self):
        """Mark this notification as read, recording the timestamp."""
        if not self.is_read:
            from django.utils import timezone

            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at", "updated_at"])


class NotificationPreference(TenantScopedModel):
    """
    Per-user, per-tenant delivery preference for a specific notification type.

    Controls whether a notification type should be delivered in-app (WebSocket
    push) and/or via email. Defaults to both channels enabled when no
    preference record exists.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    notification_type = models.CharField(
        max_length=50,
        choices=NotificationType.choices,
    )
    in_app = models.BooleanField(default=True)
    email = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "notification preference"
        verbose_name_plural = "notification preferences"
        unique_together = [("user", "tenant", "notification_type")]

    def __str__(self):
        return (
            f"{self.user} | {self.get_notification_type_display()} "
            f"(in_app={self.in_app}, email={self.email})"
        )
