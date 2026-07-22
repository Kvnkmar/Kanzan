"""Data migration: complete the Permission catalogue + backfill role grants.

Two compounding bugs left every self-service tenant's roles with ZERO explicit
permissions (so fine-grained RBAC never enforced — the whole tenant ran on the
coarse hierarchy fallback):

  Bug A — the seeding resolver (apps.tenants.signals) used the tenant-scoped
          ``Role.objects.get`` while no tenant was bound in context, so it
          fail-closed to DoesNotExist and assigned no perms. (Fixed in code.)
  Bug B — a fresh ``migrate`` only ever created 26 of the 69 permission
          codenames (accounts/0002 seeded 14, accounts/0012 seeded 12); the 43
          core codenames — ticket.*, contact.*, company.*, billing.*, user.*,
          role.*, report.*, settings.*, kb_article.*, kb_category.*,
          calendar_event.*, inbound_email.* — were written by NO migration.

This migration closes Bug B and backfills every existing tenant's system roles:

  1. Seed the FULL 69-codename catalogue (idempotent get_or_create).
  2. Grant each system role its blueprint permissions on every tenant, via
     .add() (idempotent; preserves any operator-added perms). This repairs the
     tenants whose roles were seeded 0-perm before the Bug A fix.

Codename lists are INLINED (frozen) rather than imported from
apps/accounts/defaults.py — matching accounts/0012 — so a future edit to the
source-of-truth file does not silently change what a fresh migrate seeds here
(a dedicated follow-up migration must seed any newly-added codename, and the
``test_permission_catalogue`` CI guard enforces that).
"""

from django.db import migrations


# (resource, action, human-readable name) — the full catalogue (69), a frozen
# snapshot of apps.accounts.defaults.PERMISSION_DEFINITIONS at this release.
PERMISSION_DEFINITIONS = [
    ("ticket", "view", "View tickets"),
    ("ticket", "create", "Create tickets"),
    ("ticket", "update", "Update tickets"),
    ("ticket", "delete", "Delete tickets"),
    ("ticket", "assign", "Assign tickets"),
    ("ticket", "export", "Export tickets"),
    ("contact", "view", "View contacts"),
    ("contact", "create", "Create contacts"),
    ("contact", "update", "Update contacts"),
    ("contact", "delete", "Delete contacts"),
    ("contact", "export", "Export contacts"),
    ("company", "view", "View companies"),
    ("company", "create", "Create companies"),
    ("company", "update", "Update companies"),
    ("company", "delete", "Delete companies"),
    ("billing", "view", "View billing"),
    ("billing", "manage", "Manage billing"),
    ("user", "view", "View users"),
    ("user", "create", "Create users"),
    ("user", "update", "Update users"),
    ("user", "delete", "Delete users"),
    ("role", "view", "View roles"),
    ("role", "create", "Create roles"),
    ("role", "update", "Update roles"),
    ("role", "delete", "Delete roles"),
    ("report", "view", "View reports"),
    ("report", "export", "Export reports"),
    ("settings", "view", "View settings"),
    ("settings", "manage", "Manage settings"),
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
    ("kb_article", "view", "View knowledge base articles"),
    ("kb_article", "create", "Create knowledge base articles"),
    ("kb_article", "update", "Update knowledge base articles"),
    ("kb_article", "delete", "Delete knowledge base articles"),
    ("kb_category", "view", "View knowledge base categories"),
    ("kb_category", "create", "Create knowledge base categories"),
    ("kb_category", "update", "Update knowledge base categories"),
    ("kb_category", "delete", "Delete knowledge base categories"),
    ("calendar_event", "view", "View calendar events"),
    ("calendar_event", "create", "Create calendar events"),
    ("calendar_event", "update", "Update calendar events"),
    ("calendar_event", "delete", "Delete calendar events"),
    ("inbound_email", "view", "View inbound emails"),
    ("inbound_email", "manage", "Manage inbound emails"),
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

ALL_CODENAMES = [f"{r}.{a}" for r, a, _ in PERMISSION_DEFINITIONS]

MANAGER_CODENAMES = [
    "ticket.view", "ticket.create", "ticket.update", "ticket.delete",
    "ticket.assign", "ticket.export",
    "contact.view", "contact.create", "contact.update", "contact.delete",
    "contact.export",
    "company.view", "company.create", "company.update", "company.delete",
    "user.view", "role.view", "report.view", "report.export",
    "agent.view", "agent.manage",
    "queue.view", "queue.create", "queue.update", "queue.delete",
    "sla_policy.view", "sla_policy.create", "sla_policy.update", "sla_policy.delete",
    "escalation_rule.view", "escalation_rule.create", "escalation_rule.update",
    "escalation_rule.delete",
    "kb_article.view", "kb_article.create", "kb_article.update", "kb_article.delete",
    "kb_category.view", "kb_category.create", "kb_category.update", "kb_category.delete",
    "calendar_event.view", "calendar_event.create", "calendar_event.update",
    "calendar_event.delete",
    "inbound_email.view", "inbound_email.manage",
    "hub_email.view", "hub_email.assign", "hub_email.reassign", "hub_email.convert",
    "hub_email.dismiss", "hub_email.reply", "hub_email.escalate", "hub_email.note",
    "department.view", "department.manage", "routing_rule.manage", "hub_sla.manage",
]

AGENT_CODENAMES = [
    "ticket.view", "ticket.create", "ticket.update", "ticket.assign",
    "contact.view", "contact.create", "contact.update",
    "company.view", "report.view", "agent.view",
    "kb_article.view", "kb_article.create", "kb_article.update", "kb_category.view",
    "calendar_event.view", "calendar_event.create", "calendar_event.update",
    "inbound_email.view",
    "hub_email.view", "hub_email.convert", "hub_email.reply", "hub_email.escalate",
    "hub_email.note", "department.view",
]

TEAM_LEAD_CODENAMES = AGENT_CODENAMES + [
    "ticket.delete", "ticket.export", "contact.delete", "contact.export",
    "user.view", "report.export", "queue.view", "sla_policy.view",
    "escalation_rule.view", "kb_article.delete", "kb_category.update",
    "calendar_event.delete", "inbound_email.manage",
    "hub_email.assign", "hub_email.reassign", "hub_email.dismiss",
]

IT_CODENAMES = AGENT_CODENAMES + ["user.view"]
HR_CODENAMES = AGENT_CODENAMES + ["user.view"]

GRANTS_BY_SLUG = {
    "admin": ALL_CODENAMES,
    "manager": MANAGER_CODENAMES,
    "team-lead": TEAM_LEAD_CODENAMES,
    "agent": AGENT_CODENAMES,
    "it": IT_CODENAMES,
    "hr": HR_CODENAMES,
}


def forwards(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")

    # 1. Seed the full catalogue (the 26 already-seeded rows are no-ops).
    for resource, action, name in PERMISSION_DEFINITIONS:
        Permission.objects.get_or_create(
            codename=f"{resource}.{action}",
            defaults={"name": name, "resource": resource, "action": action},
        )

    perm_by_codename = {p.codename: p for p in Permission.objects.all()}

    # 2. Backfill: grant each system role its blueprint perms on every tenant.
    #    .add() (not .set()) keeps any operator-added perms intact and is a
    #    no-op for the correctly-seeded tenant.
    for slug, codenames in GRANTS_BY_SLUG.items():
        perms = [perm_by_codename[c] for c in codenames if c in perm_by_codename]
        if not perms:
            continue
        for role in Role.objects.filter(slug=slug, is_system=True):
            role.permissions.add(*perms)


def backwards(apps, schema_editor):
    # Non-reversible by design: we do not un-seed permissions or strip role
    # grants on rollback (other data — roles, memberships — depend on them, and
    # tearing them out would re-open the zero-perm hole). No-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_seed_inbox_hub_permissions"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
