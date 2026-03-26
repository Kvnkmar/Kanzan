"""
Email record models.

Stores both inbound emails (from webhook providers) and outbound email
tracking records (for Message-ID threading). The ``direction`` field
distinguishes the two.

Each record is linked to a tenant (resolved from the recipient address
for inbound, set explicitly for outbound) and optionally to the ticket
it created, updated, or was sent from.
"""

import uuid

from django.db import models

from main.models import TimestampedModel


class InboundEmail(TimestampedModel):
    """
    Email record for both inbound and outbound messages.

    Inbound records store the original email data from webhook providers
    (SendGrid, Mailgun, etc.) and track processing state.

    Outbound records store Message-ID references so that customer replies
    can be threaded back to the originating ticket. These are created by
    the outbound email service after a successful send.

    The ``direction`` field distinguishes inbound from outbound records.
    The ``sender_type`` field identifies who sent the email (customer,
    system automation, or a human agent).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        TICKET_CREATED = "ticket_created", "Ticket Created"
        REPLY_ADDED = "reply_added", "Reply Added"
        SENT = "sent", "Sent"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    class SenderType(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        SYSTEM = "system", "System"
        AGENT = "agent", "Agent"

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
        help_text="RFC 2822 Message-ID header. Stored WITHOUT angle brackets.",
    )
    in_reply_to = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="In-Reply-To header for threading. Stored WITHOUT angle brackets.",
    )
    references = models.TextField(
        blank=True,
        default="",
        help_text=(
            "References header (space-separated Message-IDs). "
            "Each ID stored WITHOUT angle brackets."
        ),
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
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        default=Direction.INBOUND,
        db_index=True,
        help_text=(
            "Whether this record represents an email received (inbound) "
            "or sent (outbound). Outbound records exist for Message-ID "
            "threading so customer replies can be matched back."
        ),
    )
    sender_type = models.CharField(
        max_length=10,
        choices=SenderType.choices,
        default=SenderType.CUSTOMER,
        help_text=(
            "Who sent this email: customer (inbound from external), "
            "system (automated notification), or agent (manual send from UI)."
        ),
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
    idempotency_key = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text=(
            "Unique key for deduplication. Format: "
            "'in:{tenant_id}:{message_id}' for inbound, "
            "'out:{tenant_id}:{ticket_id}:{message_id}' for outbound."
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["message_id"]),
            models.Index(
                fields=["tenant", "direction", "status"],
                name="email_tenant_dir_status_idx",
            ),
            models.Index(
                fields=["ticket", "direction"],
                name="email_ticket_direction_idx",
            ),
        ]

    def __str__(self):
        arrow = "\u2192" if self.direction == self.Direction.OUTBOUND else "\u2190"
        return f"Email {arrow} {self.sender_email}: {self.subject[:50]}"

    @property
    def is_inbound(self):
        return self.direction == self.Direction.INBOUND

    @property
    def is_outbound(self):
        return self.direction == self.Direction.OUTBOUND
