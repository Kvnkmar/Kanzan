"""
Default data provisioning for the accounts app.

- seed_permissions(): creates all global Permission objects.
- provision_default_roles(tenant): creates system roles for a new tenant.
"""

import logging

from apps.accounts.models import Permission, Role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission definitions: (resource, action, human-readable name)
# ---------------------------------------------------------------------------

PERMISSION_DEFINITIONS = [
    # Tickets
    ("ticket", "view", "View tickets"),
    ("ticket", "create", "Create tickets"),
    ("ticket", "update", "Update tickets"),
    ("ticket", "delete", "Delete tickets"),
    ("ticket", "assign", "Assign tickets"),
    ("ticket", "export", "Export tickets"),
    # Contacts
    ("contact", "view", "View contacts"),
    ("contact", "create", "Create contacts"),
    ("contact", "update", "Update contacts"),
    ("contact", "delete", "Delete contacts"),
    ("contact", "export", "Export contacts"),
    # Companies
    ("company", "view", "View companies"),
    ("company", "create", "Create companies"),
    ("company", "update", "Update companies"),
    ("company", "delete", "Delete companies"),
    # Billing
    ("billing", "view", "View billing"),
    ("billing", "manage", "Manage billing"),
    # Users
    ("user", "view", "View users"),
    ("user", "create", "Create users"),
    ("user", "update", "Update users"),
    ("user", "delete", "Delete users"),
    # Roles
    ("role", "view", "View roles"),
    ("role", "create", "Create roles"),
    ("role", "update", "Update roles"),
    ("role", "delete", "Delete roles"),
    # Reports
    ("report", "view", "View reports"),
    ("report", "export", "Export reports"),
    # Settings
    ("settings", "view", "View settings"),
    ("settings", "manage", "Manage settings"),
    # Queues (operational — Manager domain)
    ("queue", "view", "View queues"),
    ("queue", "create", "Create queues"),
    ("queue", "update", "Update queues"),
    ("queue", "delete", "Delete queues"),
    # SLA Policies (operational — Manager domain)
    ("sla_policy", "view", "View SLA policies"),
    ("sla_policy", "create", "Create SLA policies"),
    ("sla_policy", "update", "Update SLA policies"),
    ("sla_policy", "delete", "Delete SLA policies"),
    # Escalation Rules (operational — Manager domain)
    ("escalation_rule", "view", "View escalation rules"),
    ("escalation_rule", "create", "Create escalation rules"),
    ("escalation_rule", "update", "Update escalation rules"),
    ("escalation_rule", "delete", "Delete escalation rules"),
    # Agent Management (operational — Manager domain)
    ("agent", "view", "View agent availability"),
    ("agent", "manage", "Manage agent availability"),
]

# ---------------------------------------------------------------------------
# Role blueprints: (name, slug, hierarchy_level, description, codenames)
# ---------------------------------------------------------------------------

# All codenames for convenience
ALL_CODENAMES = [f"{r}.{a}" for r, a, _ in PERMISSION_DEFINITIONS]

MANAGER_CODENAMES = [
    # Tickets — full operational control
    "ticket.view",
    "ticket.create",
    "ticket.update",
    "ticket.delete",
    "ticket.assign",
    "ticket.export",
    # Contacts — full operational control
    "contact.view",
    "contact.create",
    "contact.update",
    "contact.delete",
    "contact.export",
    # Companies — full operational control
    "company.view",
    "company.create",
    "company.update",
    "company.delete",
    # Users — view only (see the team, but can't create/delete accounts)
    "user.view",
    # Roles — view only
    "role.view",
    # Reports — full access
    "report.view",
    "report.export",
    # Operational resources (Manager's domain)
    "agent.view",
    "agent.manage",
    "queue.view",
    "queue.create",
    "queue.update",
    "queue.delete",
    "sla_policy.view",
    "sla_policy.create",
    "sla_policy.update",
    "sla_policy.delete",
    "escalation_rule.view",
    "escalation_rule.create",
    "escalation_rule.update",
    "escalation_rule.delete",
]

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
]

VIEWER_CODENAMES = [
    "ticket.view",
    "contact.view",
    "company.view",
    "report.view",
]

ROLE_DEFINITIONS = [
    {
        "name": "Admin",
        "slug": "admin",
        "hierarchy_level": 10,
        "description": "Full access to all tenant resources, system configuration, and billing.",
        "codenames": ALL_CODENAMES,
    },
    {
        "name": "Manager",
        "slug": "manager",
        "hierarchy_level": 20,
        "description": "Operational oversight: agent management, SLA, queues, and full ticket/contact management.",
        "codenames": MANAGER_CODENAMES,
    },
    {
        "name": "Agent",
        "slug": "agent",
        "hierarchy_level": 30,
        "description": "Can work with tickets and contacts.",
        "codenames": AGENT_CODENAMES,
    },
    {
        "name": "Viewer",
        "slug": "viewer",
        "hierarchy_level": 40,
        "description": "Read-only access to tickets, contacts, companies, and reports.",
        "codenames": VIEWER_CODENAMES,
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def seed_permissions():
    """
    Create all Permission objects defined in PERMISSION_DEFINITIONS.
    Existing permissions (matched by codename) are left untouched.
    Returns the number of newly created permissions.
    """
    created_count = 0
    for resource, action, name in PERMISSION_DEFINITIONS:
        codename = f"{resource}.{action}"
        _, created = Permission.objects.get_or_create(
            codename=codename,
            defaults={
                "name": name,
                "resource": resource,
                "action": action,
            },
        )
        if created:
            created_count += 1
            logger.debug("Created permission: %s", codename)

    logger.info("seed_permissions: %d new permissions created.", created_count)
    return created_count


def provision_default_roles(tenant):
    """
    Create the default system roles for the given tenant.
    Each role is linked to its defined set of permissions.
    Existing roles (matched by tenant + slug) are skipped.

    Assumes seed_permissions() has already been called.
    Returns a dict mapping slug -> Role instance.
    """
    roles = {}
    for defn in ROLE_DEFINITIONS:
        role, created = Role.unscoped.get_or_create(
            tenant=tenant,
            slug=defn["slug"],
            defaults={
                "name": defn["name"],
                "description": defn["description"],
                "hierarchy_level": defn["hierarchy_level"],
                "is_system": True,
            },
        )
        if created:
            perms = Permission.objects.filter(codename__in=defn["codenames"])
            role.permissions.set(perms)
            logger.info(
                "Created system role '%s' for tenant %s with %d permissions.",
                role.name,
                tenant,
                perms.count(),
            )
        roles[defn["slug"]] = role

    return roles
