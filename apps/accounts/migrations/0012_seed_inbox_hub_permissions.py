"""
Data migration: seed Inbox Hub permissions on every existing tenant.

Adds 12 new Permission rows (hub_email.*, department.*, routing_rule.manage,
hub_sla.manage) and grants them to the appropriate system roles per the
RBAC grid in plan section 6.

Mirrors the pattern from 0002_restructure_manager_permissions: codename
lists are inlined rather than imported from apps/accounts/defaults.py so
this migration stays frozen against future edits of the source-of-truth
file. New tenants pick up the same permissions via
apps.tenants.signals.create_default_roles, which DOES read defaults.py at
tenant-creation time.

Idempotent: get_or_create on Permission, .add(*perms) on each role.
"""

from django.db import migrations


# (resource, action, human-readable name)
NEW_PERMISSIONS = [
    ("hub_email", "view", "View hub emails"),
    ("hub_email", "assign", "Assign hub emails"),
    ("hub_email", "reassign", "Reassign hub emails"),
    ("hub_email", "convert", "Convert hub email to ticket"),
    ("hub_email", "dismiss", "Dismiss hub emails"),
    ("hub_email", "reply", "Reply to hub emails"),
    ("hub_email", "escalate", "Escalate hub emails"),
    ("hub_email", "note", "Add notes to hub emails"),
    ("department", "view", "View departments"),
    ("department", "manage", "Manage departments"),
    ("routing_rule", "manage", "Manage routing rules"),
    ("hub_sla", "manage", "Manage hub SLA policies"),
]

ALL_NEW_CODENAMES = [f"{r}.{a}" for r, a, _ in NEW_PERMISSIONS]

# Admin: all 12 (matches ALL_CODENAMES in defaults.py).
ADMIN_CODENAMES = ALL_NEW_CODENAMES

# Manager: full operational + admin (12).
MANAGER_CODENAMES = ALL_NEW_CODENAMES

# Agent / IT / HR: triage (no assign/reassign/dismiss; can convert/reply/escalate/note).
AGENT_CODENAMES = [
    "hub_email.view",
    "hub_email.convert",
    "hub_email.reply",
    "hub_email.escalate",
    "hub_email.note",
    "department.view",
]

# Team Lead: AGENT + dept-scoped triage authority.
TEAM_LEAD_CODENAMES = AGENT_CODENAMES + [
    "hub_email.assign",
    "hub_email.reassign",
    "hub_email.dismiss",
]


GRANTS_BY_SLUG = {
    "admin": ADMIN_CODENAMES,
    "manager": MANAGER_CODENAMES,
    "team-lead": TEAM_LEAD_CODENAMES,
    "agent": AGENT_CODENAMES,
    "it": AGENT_CODENAMES,
    "hr": AGENT_CODENAMES,
}


def forwards(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")

    # 1. Create Permission rows.
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

    perm_by_codename = {
        p.codename: p
        for p in Permission.objects.filter(codename__in=ALL_NEW_CODENAMES)
    }

    # 2. Grant per role slug on every tenant. Use .add() not .set() so
    #    operator-customised role permission sets are preserved.
    for slug, codenames in GRANTS_BY_SLUG.items():
        perms = [perm_by_codename[c] for c in codenames if c in perm_by_codename]
        if not perms:
            continue
        for role in Role.objects.filter(slug=slug, is_system=True):
            role.permissions.add(*perms)


def backwards(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")

    perms = Permission.objects.filter(codename__in=ALL_NEW_CODENAMES)
    # Detach from every role (any tenant) before deleting the rows.
    for role in Role.objects.filter(
        permissions__codename__in=ALL_NEW_CODENAMES
    ).distinct():
        role.permissions.remove(*perms)
    perms.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_seed_team_lead_it_hr_roles"),
        ("tenants", "0009_tenantsettings_inbox_hub_enabled"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
