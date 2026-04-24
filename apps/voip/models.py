"""
VoIP models for Asterisk/FreePBX integration.

* VoIPSettings   -- per-tenant Asterisk connection and telephony config
* Extension      -- user-to-SIP endpoint mapping
* CallLog        -- immutable call detail record
* CallRecording  -- recorded call audio file
* CallQueue      -- ACD-style call distribution queue
"""

import uuid

from django.conf import settings
from django.db import models

from main.models import TenantScopedModel


def recording_upload_path(instance, filename):
    """Tenant-isolated recording storage: tenants/<id>/recordings/YYYY/MM/<file>."""
    from django.utils import timezone

    now = timezone.now()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "wav"
    return f"tenants/{instance.tenant_id}/recordings/{now.year}/{now.month:02d}/{uuid.uuid4().hex}.{ext}"


class VoIPSettings(TenantScopedModel):
    """
    Per-tenant Asterisk/FreePBX connection settings.

    One record per tenant (enforced by unique constraint).
    Passwords are stored encrypted via django-encrypted-model-fields.
    """

    asterisk_host = models.CharField(
        max_length=255,
        default="127.0.0.1",
        help_text="Asterisk server hostname or IP.",
    )
    asterisk_ari_port = models.PositiveIntegerField(
        default=8088,
        help_text="Asterisk ARI HTTP port (8088 for HTTP, 8089 for HTTPS).",
    )
    asterisk_use_ssl = models.BooleanField(
        default=False,
        help_text="Use HTTPS/WSS for ARI connection (required if FreePBX has SSL enabled).",
    )
    asterisk_wss_port = models.PositiveIntegerField(
        default=8089,
        help_text="Asterisk PJSIP WebSocket (WSS) port for browser clients.",
    )
    ari_username = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="ARI application username.",
    )
    ari_password = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="ARI application password (stored encrypted).",
    )
    stun_server = models.CharField(
        max_length=255,
        default="stun:stun.l.google.com:19302",
        help_text="STUN server URI for WebRTC NAT traversal.",
    )
    turn_server = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="TURN server URI for WebRTC relay.",
    )
    turn_username = models.CharField(max_length=100, blank=True, default="")
    turn_password = models.CharField(max_length=255, blank=True, default="")
    default_caller_id = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Default outbound caller ID for this tenant.",
    )
    pjsip_context = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Asterisk PJSIP context for tenant isolation.",
    )
    recording_enabled = models.BooleanField(
        default=False,
        help_text="Whether call recording is enabled for this tenant.",
    )
    voicemail_enabled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "VoIP settings"
        verbose_name_plural = "VoIP settings"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"],
                name="unique_voip_settings_per_tenant",
            ),
        ]

    def __str__(self):
        return f"VoIP Settings for {self.tenant}"


class Extension(TenantScopedModel):
    """
    Maps a user to a SIP endpoint in Asterisk.

    Each user gets one extension per tenant. The sip_username is globally
    unique across Asterisk and used for PJSIP endpoint registration.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="voip_extensions",
    )
    extension_number = models.CharField(
        max_length=10,
        help_text="Internal extension number (e.g., 1001).",
    )
    sip_username = models.CharField(
        max_length=100,
        unique=True,
        help_text="PJSIP endpoint username (globally unique).",
    )
    sip_password = models.CharField(
        max_length=255,
        help_text="SIP registration password (stored encrypted).",
    )
    caller_id_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Display name for outbound caller ID.",
    )
    caller_id_number = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Phone number for outbound caller ID.",
    )
    is_active = models.BooleanField(default=True)
    registered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last successful SIP registration timestamp.",
    )

    class Meta:
        ordering = ["extension_number"]
        unique_together = [("tenant", "extension_number"), ("tenant", "user")]

    def __str__(self):
        return f"Ext {self.extension_number} ({self.user.email})"


class CallLog(TenantScopedModel):
    """
    Immutable call detail record.

    Tracks the full lifecycle of a call from initiation to completion.
    Linked to contacts and tickets for CRM integration.
    """

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"
        INTERNAL = "internal", "Internal"

    class Status(models.TextChoices):
        RINGING = "ringing", "Ringing"
        ANSWERED = "answered", "Answered"
        ON_HOLD = "on_hold", "On Hold"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        MISSED = "missed", "Missed"
        BUSY = "busy", "Busy"
        NO_ANSWER = "no_answer", "No Answer"
        VOICEMAIL = "voicemail", "Voicemail"

    asterisk_channel_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Asterisk channel unique ID for ARI correlation.",
    )
    direction = models.CharField(max_length=10, choices=Direction.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RINGING,
    )
    caller_extension = models.ForeignKey(
        Extension,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outgoing_calls",
    )
    callee_extension = models.ForeignKey(
        Extension,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_calls",
    )
    caller_number = models.CharField(max_length=50)
    callee_number = models.CharField(max_length=50)
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="call_logs",
        help_text="Linked contact for CRM timeline.",
    )
    ticket = models.ForeignKey(
        "tickets.Ticket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="call_logs",
        help_text="Linked ticket for timeline logging.",
    )
    started_at = models.DateTimeField(
        help_text="When the call was initiated.",
    )
    answered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the call was answered.",
    )
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the call ended.",
    )
    duration_seconds = models.PositiveIntegerField(
        default=0,
        help_text="Total call duration in seconds.",
    )
    hold_duration_seconds = models.PositiveIntegerField(
        default=0,
        help_text="Total time on hold in seconds.",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Agent notes about the call.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw ARI event data and additional context.",
    )

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["tenant", "started_at"]),
            models.Index(fields=["tenant", "caller_extension"]),
            models.Index(fields=["tenant", "contact"]),
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self):
        return f"Call {self.direction} {self.caller_number} → {self.callee_number} ({self.status})"

    @property
    def is_active(self):
        return self.status in (
            self.Status.RINGING,
            self.Status.ANSWERED,
            self.Status.ON_HOLD,
        )


class CallRecording(TenantScopedModel):
    """Audio recording of a call, stored tenant-isolated."""

    call_log = models.OneToOneField(
        CallLog,
        on_delete=models.CASCADE,
        related_name="recording",
    )
    file = models.FileField(upload_to=recording_upload_path)
    duration_seconds = models.PositiveIntegerField(default=0)
    size_bytes = models.PositiveIntegerField(default=0)
    mime_type = models.CharField(max_length=50, default="audio/wav")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Recording for {self.call_log}"


class CallQueue(TenantScopedModel):
    """
    ACD-style call distribution queue.

    Routes inbound calls to a group of extensions using a ring strategy.
    """

    class Strategy(models.TextChoices):
        RING_ALL = "ringall", "Ring All"
        ROUND_ROBIN = "roundrobin", "Round Robin"
        LEAST_RECENT = "leastrecent", "Least Recent"
        FEWEST_CALLS = "fewestcalls", "Fewest Calls"
        RANDOM = "random", "Random"

    name = models.CharField(max_length=100)
    strategy = models.CharField(
        max_length=20,
        choices=Strategy.choices,
        default=Strategy.RING_ALL,
    )
    members = models.ManyToManyField(
        Extension,
        blank=True,
        related_name="call_queues",
    )
    timeout_seconds = models.PositiveIntegerField(
        default=30,
        help_text="Seconds to ring before moving to next agent or voicemail.",
    )
    max_wait_seconds = models.PositiveIntegerField(
        default=300,
        help_text="Maximum wait time in queue before dropping.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("tenant", "name")]

    def __str__(self):
        return f"Queue: {self.name} ({self.strategy})"
