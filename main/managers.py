"""
Tenant-aware managers and querysets for row-level multi-tenancy.

All tenant-scoped models should use TenantAwareManager as their default manager.
Use Model.unscoped for admin/superuser queries that need cross-tenant access.
"""

import logging

from django.db import models

from main.context import get_current_tenant

logger = logging.getLogger(__name__)


class TenantQuerySet(models.QuerySet):
    """QuerySet that can be explicitly filtered by tenant."""

    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)


class TenantAwareManager(models.Manager):
    """
    Default manager that automatically filters by the current tenant
    from context-local storage.

    If no tenant is set in the context, returns an empty queryset to
    prevent cross-tenant data leakage.  Use ``Model.unscoped`` for
    admin, management commands, or Celery tasks that intentionally
    need cross-tenant access.
    """

    def get_queryset(self):
        qs = TenantQuerySet(self.model, using=self._db)
        tenant = get_current_tenant()
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        else:
            logger.debug(
                "TenantAwareManager: no tenant context for %s, returning empty queryset.",
                self.model.__name__,
            )
            qs = qs.none()
        return qs


class SoftDeleteTenantManager(TenantAwareManager):
    """
    Tenant-aware manager that also excludes soft-deleted records.

    Models with an ``is_deleted`` boolean field should use this as their
    default ``objects`` manager so that soft-deleted rows are hidden from
    all ORM queries — not just API views.
    """

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)
