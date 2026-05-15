"""
API Key model — tenant-scoped service-account credentials.

An ``APIKey`` row pairs a hidden synthetic service-account ``User`` (created
at mint time) with a tenant + role. The cleartext secret is shown to the
admin exactly once at creation; only its SHA-512 hash + indexed prefix are
persisted. Authentication is handled by
``apps.api_keys.authentication.APIKeyAuthentication``.

See :mod:`apps.api_keys.services` for the mint/regenerate/revoke flow.
"""

from django.conf import settings
from django.db import models

from main.models import TenantScopedModel


class APIKey(TenantScopedModel):
    """
    A tenant-scoped API key authenticating as a hidden service-account user.

    The cleartext secret is never persisted; only ``hashed_key`` (SHA-512 hex)
    and ``prefix`` (first ~20 chars, indexed for lookup) are stored. The
    cleartext is returned exactly once at creation/regeneration.
    """

    name = models.CharField(
        max_length=120,
        help_text="User-facing label, e.g. 'Zapier Integration'.",
    )
    service_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_key",
        help_text="Hidden synthetic user this key authenticates as.",
    )
    role = models.ForeignKey(
        "accounts.Role",
        on_delete=models.PROTECT,
        related_name="api_keys",
        help_text="Role for permission inheritance.",
    )
    prefix = models.CharField(
        max_length=20,
        db_index=True,
        help_text="First ~20 chars of the cleartext key (the lookup target).",
    )
    hashed_key = models.CharField(
        max_length=128,
        help_text="SHA-512 hex digest of the full cleartext key.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="api_keys_created",
        help_text="Human admin who minted the key.",
    )
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)
    last_used_user_agent = models.CharField(max_length=255, blank=True, default="")
    request_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("tenant", "name")]
        indexes = [
            models.Index(fields=["prefix"]),
            models.Index(fields=["tenant", "is_active"]),
        ]
        verbose_name = "API key"
        verbose_name_plural = "API keys"

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @property
    def masked_prefix(self):
        """A safe-to-display masked form: ``kz_live_dpap_…abcd``."""
        return f"{self.prefix}…{self.hashed_key[-4:]}"
