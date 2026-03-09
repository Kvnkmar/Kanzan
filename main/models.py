"""
Abstract base models for the Kanzen Suite platform.

All concrete models should inherit from one of these bases:
- TimestampedModel: UUID PK + created_at/updated_at (for global models)
- TenantScopedModel: TimestampedModel + tenant FK + auto-scoping (for tenant data)
"""

import uuid

from django.db import models

from main.context import get_current_tenant
from main.managers import TenantAwareManager


class TimestampedModel(models.Model):
    """Abstract base with UUID primary key and timestamps."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class TenantScopedModel(TimestampedModel):
    """
    Abstract base for all tenant-scoped models.

    Provides:
    - Automatic tenant filtering via TenantAwareManager
    - Auto-assignment of tenant on save
    - Explicit 'unscoped' manager for cross-tenant queries
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
        db_index=True,
        editable=False,
    )

    objects = TenantAwareManager()
    unscoped = models.Manager()

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            tenant = get_current_tenant()
            if tenant:
                self.tenant = tenant
            else:
                raise ValueError(
                    f"Cannot save {self.__class__.__name__} without a tenant context. "
                    "Either set the tenant explicitly or ensure TenantMiddleware is active."
                )
        super().save(*args, **kwargs)
