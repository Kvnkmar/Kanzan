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

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from main.models import TimestampedModel


class InboundEmail(TimestampedModel):
    """
    Email record for both inbound and outbound messages.

    Inbound records store the original email data received by the
    in-process SMTP server and track processing state.

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
        BOUNCED = "bounced", "Bounced"
        FAILED = "failed", "Failed"

    class InboxStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        LINKED = "linked", "Linked"
        ACTIONED = "actioned", "Actioned"
        IGNORED = "ignored", "Ignored"

    class InboxAction(models.TextChoices):
        OPEN = "open", "Open"
        ASSIGN = "assign", "Assign"
        CLOSE = "close", "Close"

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
        help_text="The address this email was sent to (e.g. support@tenant.kanzen.io).",
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

    # --- Agent inbox workflow fields ---
    inbox_status = models.CharField(
        max_length=20,
        choices=InboxStatus.choices,
        default=InboxStatus.PENDING,
        db_index=True,
        help_text="Agent inbox workflow state.",
    )
    linked_ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linked_inbound_emails",
        help_text="Ticket manually linked by an agent via the inbox workflow.",
    )
    linked_at = models.DateTimeField(null=True, blank=True)
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linked_emails",
    )
    is_read = models.BooleanField(default=False, db_index=True)
    actioned_at = models.DateTimeField(null=True, blank=True)
    actioned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actioned_emails",
    )
    action_taken = models.CharField(
        max_length=10,
        choices=InboxAction.choices,
        null=True,
        blank=True,
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
            models.Index(
                fields=["tenant", "inbox_status"],
                name="email_tenant_inbox_status_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = InboundEmail.objects.get(pk=self.pk)
            except InboundEmail.DoesNotExist:
                old = None
            if old:
                if old.actioned_at and self.actioned_at != old.actioned_at:
                    raise ValidationError("actioned_at is immutable once set.")
                if old.actioned_by_id and self.actioned_by_id != old.actioned_by_id:
                    raise ValidationError("actioned_by is immutable once set.")
                if old.linked_at and self.linked_at != old.linked_at:
                    raise ValidationError("linked_at is immutable once set.")
                if old.linked_by_id and self.linked_by_id != old.linked_by_id:
                    raise ValidationError("linked_by is immutable once set.")
        super().save(*args, **kwargs)

    def __str__(self):
        arrow = "\u2192" if self.direction == self.Direction.OUTBOUND else "\u2190"
        return f"Email {arrow} {self.sender_email}: {self.subject[:50]}"

    @property
    def is_inbound(self):
        return self.direction == self.Direction.INBOUND

    @property
    def is_outbound(self):
        return self.direction == self.Direction.OUTBOUND


class BounceLog(TimestampedModel):
    """
    Records hard-bounce email deliveries.

    Created when the inbound pipeline detects a bounce notification
    (DSN/NDR). Optionally linked to the ticket the bounce relates to
    (if the bounce is a reply to a ticket notification).
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="bounce_logs",
    )
    inbound_email = models.ForeignKey(
        InboundEmail,
        on_delete=models.CASCADE,
        related_name="bounce_logs",
        help_text="The InboundEmail record that contained the bounce.",
    )
    from_address = models.EmailField(
        help_text="Original sender of the bounced message (the mailer-daemon).",
    )
    to_address = models.EmailField(
        blank=True,
        default="",
        help_text="The intended recipient whose delivery failed.",
    )
    subject = models.CharField(max_length=998, blank=True, default="")
    bounce_reason = models.TextField(
        blank=True,
        default="",
        help_text="Header-derived reason for the bounce (e.g. filter name, header value).",
    )
    ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bounce_logs",
        help_text="The ticket this bounce relates to, if threading matched.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "bounce log"
        verbose_name_plural = "bounce logs"

    def __str__(self):
        return f"Bounce from {self.from_address}: {self.subject[:50]}"


class IMAPPollState(TimestampedModel):
    """
    Tracks the last IMAP UID the poller has processed, per mailbox.

    Previously the poller only fetched UNSEEN messages — but Gmail
    marks a message as ``\\Seen`` the moment a user opens it in the
    web UI, which caused the poller to silently skip real incoming
    email (the user checks "did it arrive?" in Gmail, Gmail marks it
    seen, next poll sees nothing to do, ticket never gets created).

    With a UID watermark, the poller fetches everything with
    ``UID > last_uid`` regardless of read state. Duplicates are
    rejected downstream via ``InboundEmail.message_id`` uniqueness.

    UIDVALIDITY is also tracked: if the mailbox is reset (UIDs can
    be re-issued from 1), we detect the change and start fresh.
    """

    host = models.CharField(max_length=255)
    user = models.CharField(max_length=255)
    mailbox = models.CharField(max_length=255, default="INBOX")
    uid_validity = models.BigIntegerField(default=0)
    last_uid = models.BigIntegerField(default=0)

    class Meta:
        unique_together = [("host", "user", "mailbox")]
        verbose_name = "IMAP poll state"
        verbose_name_plural = "IMAP poll state"

    def __str__(self):
        return f"{self.user}@{self.host}/{self.mailbox} last_uid={self.last_uid}"
