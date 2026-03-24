"""
Inbound email models.

Stores raw inbound emails and tracks their processing status.
Each email is linked to a tenant (resolved from the recipient address)
and optionally to the ticket it created or updated.
"""

import uuid

from django.db import models

from main.models import TimestampedModel


class InboundEmail(TimestampedModel):
    """
    Raw inbound email record.

    Stores the original email data from the webhook provider (SendGrid,
    Mailgun, etc.) and tracks processing state. Kept for audit trail
    and debugging failed parses.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        TICKET_CREATED = "ticket_created", "Ticket Created"
        REPLY_ADDED = "reply_added", "Reply Added"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="inbound_emails",
        null=True,
        blank=True,
    )
    message_id = models.CharField(
        max_length=512,
        db_index=True,
        help_text="RFC 2822 Message-ID header for dedup.",
    )
    in_reply_to = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="In-Reply-To header for threading.",
    )
    references = models.TextField(
        blank=True,
        default="",
        help_text="References header (space-separated Message-IDs).",
    )
    sender_email = models.EmailField(db_index=True)
    sender_name = models.CharField(max_length=255, blank=True, default="")
    recipient_email = models.EmailField(
        help_text="The address this email was sent to (e.g. support@tenant.kanzan.io).",
    )
    subject = models.CharField(max_length=998, blank=True, default="")
    body_text = models.TextField(blank=True, default="")
    body_html = models.TextField(blank=True, default="")
    raw_headers = models.TextField(
        blank=True,
        default="",
        help_text="Full raw headers for debugging.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Error details if processing failed.",
    )
    ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_emails",
        help_text="The ticket this email created or was threaded to.",
    )
    attachment_metadata = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of dicts describing attachments received with this email. "
            "Each entry: {filename, content_type, size, storage_path}."
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["message_id"]),
        ]

    def __str__(self):
        return f"InboundEmail from {self.sender_email}: {self.subject[:50]}"
