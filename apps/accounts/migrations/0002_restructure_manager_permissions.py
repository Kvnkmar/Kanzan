"""
Data migration: add operational permissions and restructure Manager role.

- Creates 14 new Permission objects for queue, sla_policy, escalation_rule, and agent resources.
- Updates every tenant's system Manager role: removes admin-only permissions,
  adds operational permissions.
- Updates every tenant's system Admin role: adds the new permissions.
"""

from django.db import migrations

# Permissions that already exist and should be REMOVED from Manager
REMOVE_FROM_MANAGER = [
    "user.create",
    "user.update",
    "settings.view",
]

# New permission definitions: (resource, action, human-readable name)
NEW_PERMISSIONS = [
    ("queue", "view", "View queues"),
    ("queue", "create", "Create queues"),
    ("queue", "update", "Update queues"),
    ("queue", "delete", "Delete queues"),
    ("sla_policy", "view", "View SLA policies"),
    ("sla_policy", "create", "Create SLA policies"),
    ("sla_policy", "update", "Update SLA policies"),
    ("sla_policy", "delete", "Delete SLA policies"),
    ("escalation_rule", "view", "View escalation rules"),
    ("escalation_rule", "create", "Create escalation rules"),
    ("escalation_rule", "update", "Update escalation rules"),
    ("escalation_rule", "delete", "Delete escalation rules"),
    ("agent", "view", "View agent availability"),
    ("agent", "manage", "Manage agent availability"),
]

NEW_CODENAMES = [f"{r}.{a}" for r, a, _ in NEW_PERMISSIONS]


def forwards(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")

    # 1. Create new permissions
    for resource, action, name in NEW_PERMISSIONS:
        codename = f"{resource}.{action}"
        Permission.objects.get_or_create(
            codename=codename,
            defaults={
                "name": name,
                "resource": resource,
                "action": action,
            },
        )

    new_perms = Permission.objects.filter(codename__in=NEW_CODENAMES)
    remove_perms = Permission.objects.filter(codename__in=REMOVE_FROM_MANAGER)

    # 2. Update Admin roles: add new permissions
    for role in Role.objects.filter(slug="admin", is_system=True):
        role.permissions.add(*new_perms)

    # 3. Update Manager roles: remove admin-only, add operational
    for role in Role.objects.filter(slug="manager", is_system=True):
        role.permissions.remove(*remove_perms)
        role.permissions.add(*new_perms)


def backwards(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")

    restore_perms = Permission.objects.filter(codename__in=REMOVE_FROM_MANAGER)
    new_perms = Permission.objects.filter(codename__in=NEW_CODENAMES)

    # Restore Manager roles to previous state
    for role in Role.objects.filter(slug="manager", is_system=True):
        role.permissions.add(*restore_perms)
        role.permissions.remove(*new_perms)

    # Remove new permissions from Admin roles
    for role in Role.objects.filter(slug="admin", is_system=True):
        role.permissions.remove(*new_perms)

    # Delete the new permission objects
    Permission.objects.filter(codename__in=NEW_CODENAMES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
