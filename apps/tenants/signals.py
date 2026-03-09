"""
Signals for the tenants app.

* Auto-create ``TenantSettings`` whenever a new ``Tenant`` is saved.
* Auto-create default RBAC roles for the tenant (when the accounts app
  provides a ``Role`` model).
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.tenants.models import Tenant, TenantSettings

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Tenant)
def create_tenant_settings(sender, instance, created, **kwargs):
    """Ensure every new Tenant has an associated TenantSettings row."""
    if created:
        TenantSettings.objects.get_or_create(tenant=instance)
        logger.info("Created default TenantSettings for tenant '%s'.", instance.slug)


@receiver(post_save, sender=Tenant)
def create_default_roles(sender, instance, created, **kwargs):
    """
    Provision default RBAC roles for a newly created Tenant.

    Attempts to import ``Role`` from the accounts app.  If the model does
    not exist yet (the accounts app may not have been built), this is a
    no-op -- roles can be provisioned later.
    """
    if not created:
        return

    try:
        from apps.accounts.models import Role  # noqa: WPS433
    except (ImportError, Exception):
        logger.debug(
            "accounts.Role not available; skipping default role creation "
            "for tenant '%s'.",
            instance.slug,
        )
        return

    default_roles = [
        {"name": "Admin", "slug": "admin", "hierarchy_level": 10, "is_system": True},
        {"name": "Manager", "slug": "manager", "hierarchy_level": 20, "is_system": True},
        {"name": "Agent", "slug": "agent", "hierarchy_level": 30, "is_system": True},
        {"name": "Viewer", "slug": "viewer", "hierarchy_level": 40, "is_system": True},
    ]

    for role_data in default_roles:
        Role.objects.get_or_create(
            tenant=instance,
            slug=role_data["slug"],
            defaults={
                "name": role_data["name"],
                "hierarchy_level": role_data["hierarchy_level"],
                "is_system": role_data.get("is_system", False),
            },
        )

    # Assign default permissions to each role
    _assign_default_role_permissions(instance)

    logger.info("Created default RBAC roles for tenant '%s'.", instance.slug)


def _assign_default_role_permissions(tenant):
    """
    Assign default permissions to the four standard roles.

    Delegates to ROLE_DEFINITIONS from accounts.defaults so that the
    permission lists are maintained in a single place.
    """
    try:
        from apps.accounts.defaults import ROLE_DEFINITIONS  # noqa: WPS433
        from apps.accounts.models import Permission, Role  # noqa: WPS433
    except (ImportError, Exception):
        return

    all_perms = {p.codename: p for p in Permission.objects.all()}
    if not all_perms:
        return

    for defn in ROLE_DEFINITIONS:
        try:
            role = Role.objects.get(tenant=tenant, slug=defn["slug"])
        except Role.DoesNotExist:
            continue
        perm_objects = [all_perms[c] for c in defn["codenames"] if c in all_perms]
        role.permissions.set(perm_objects)
