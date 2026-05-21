"""
Data migration: seed three new system roles on every existing tenant.

- Team Lead (hierarchy 25) — elevated Agent. Sits between Manager and Agent.
- IT (hierarchy 30) — departmental flavour of Agent + user.view.
- HR (hierarchy 30) — departmental flavour of Agent + user.view.

New tenants pick these up automatically via apps.tenants.signals.create_default_roles
(which iterates the same role list and delegates permission assignment to
ROLE_DEFINITIONS in apps/accounts/defaults.py). This migration only backfills
tenants that existed before the roles were introduced.

Idempotent: get_or_create on (tenant, slug). Permissions are .set() on each
role so re-running the migration converges on the intended permission set.
"""

from django.db import migrations


AGENT_CODENAMES = [
    "ticket.view",
    "ticket.create",
    "ticket.update",
    "ticket.assign",
    "contact.view",
    "contact.create",
    "contact.update",
    "company.view",
    "report.view",
    "agent.view",
    "kb_article.view",
    "kb_article.create",
    "kb_article.update",
    "kb_category.view",
    "calendar_event.view",
    "calendar_event.create",
    "calendar_event.update",
    "inbound_email.view",
]

TEAM_LEAD_CODENAMES = AGENT_CODENAMES + [
    "ticket.delete",
    "ticket.export",
    "contact.delete",
    "contact.export",
    "user.view",
    "report.export",
    "queue.view",
    "sla_policy.view",
    "escalation_rule.view",
    "kb_article.delete",
    "kb_category.update",
    "calendar_event.delete",
    "inbound_email.manage",
]

IT_CODENAMES = AGENT_CODENAMES + ["user.view"]
HR_CODENAMES = AGENT_CODENAMES + ["user.view"]


NEW_ROLES = [
    {
        "name": "Team Lead",
        "slug": "team-lead",
        "hierarchy_level": 25,
        "description": "Leads a small group of agents: can delete/export tickets, view ops config, and manage the agent inbox.",
        "codenames": TEAM_LEAD_CODENAMES,
    },
    {
        "name": "IT",
        "slug": "it",
        "hierarchy_level": 30,
        "description": "IT support agent: same ticketing rights as Agent, plus user lookup for support tickets.",
        "codenames": IT_CODENAMES,
    },
    {
        "name": "HR",
        "slug": "hr",
        "hierarchy_level": 30,
        "description": "HR agent: same ticketing rights as Agent, plus user lookup for employee tickets.",
        "codenames": HR_CODENAMES,
    },
]


def forwards(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")

    perm_by_codename = {p.codename: p for p in Permission.objects.all()}
    if not perm_by_codename:
        return

    for tenant in Tenant.objects.all():
        for defn in NEW_ROLES:
            role, _ = Role.objects.get_or_create(
                tenant=tenant,
                slug=defn["slug"],
                defaults={
                    "name": defn["name"],
                    "description": defn["description"],
                    "hierarchy_level": defn["hierarchy_level"],
                    "is_system": True,
                },
            )
            perms = [perm_by_codename[c] for c in defn["codenames"] if c in perm_by_codename]
            role.permissions.set(perms)


def backwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.filter(slug__in=["team-lead", "it", "hr"], is_system=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_user_is_service_account"),
        ("tenants", "0008_tenantsettings_auto_assign_inbound_email_tickets"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
