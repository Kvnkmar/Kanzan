"""Guard tests for the RBAC permission-catalogue + zero-perm-seeding fix.

Closes two compounding bugs:
  Bug A — Tenant.post_save seeded roles with 0 permissions whenever it fired
          with no tenant bound in context (self-service signup, provision_tenant,
          admin, shell, test factories) because the resolver used the
          tenant-scoped `Role.objects.get`. Fixed to `Role.unscoped.get`.
  Bug B — a fresh migrate created only 26 of 69 permission codenames. Fixed by
          the 0013 catalogue migration.

Also guards the permission-check semantics: explicit grants are additive over
the hierarchy floor, so Team Lead's intended delete/export grants (which sit
below the ≤20 hierarchy floor) actually apply.
"""

import pytest

from apps.accounts.defaults import ALL_CODENAMES
from apps.accounts.models import Permission, Role
from main.context import clear_current_tenant

from conftest import MembershipFactory, TenantFactory, UserFactory


@pytest.mark.django_db
def test_permission_catalogue_is_fully_seeded():
    """Every codename in the source-of-truth catalogue exists after migrate.
    Guards against a codename being added to defaults.py without a dedicated
    seeding migration (Bug B regression)."""
    assert len(ALL_CODENAMES) == 69
    db = set(Permission.objects.values_list("codename", flat=True))
    missing = set(ALL_CODENAMES) - db
    assert not missing, f"Permission catalogue incomplete — nothing seeds: {sorted(missing)}"


@pytest.mark.django_db
def test_fresh_tenant_seeds_role_permissions_without_context():
    """A tenant created with NO tenant bound in context now gets fully-seeded
    role permissions (closes Bug A — the zero-perm seeding)."""
    clear_current_tenant()
    t = TenantFactory()

    counts = {
        r.slug: r.permissions.count()
        for r in Role.unscoped.filter(tenant=t, is_system=True)
    }
    assert counts.get("admin") == 69
    assert counts.get("manager") == 59
    assert counts.get("team-lead") == 40
    assert counts.get("agent") == 24
    assert counts.get("it") == 25
    assert counts.get("hr") == 25


@pytest.mark.django_db
def test_team_lead_receives_intended_grants_beyond_hierarchy():
    """Team Lead (hierarchy 25) sits below the ≤20 delete/export hierarchy floor,
    so before the fix it silently lost those grants. The explicit blueprint
    permissions now apply."""
    clear_current_tenant()
    t = TenantFactory()
    tl = Role.unscoped.get(tenant=t, slug="team-lead")
    m = MembershipFactory(user=UserFactory(), tenant=t, role=tl)

    assert m.has_effective_permission("ticket.delete") is True
    assert m.has_effective_permission("ticket.export") is True
    assert m.has_effective_permission("hub_email.assign") is True
    # ...but nothing outside its blueprint leaked in.
    assert m.has_effective_permission("billing.manage") is False


@pytest.mark.django_db
def test_seeded_roles_stay_bounded():
    """Sanity: seeding didn't over-grant. Agent keeps its bounded set."""
    clear_current_tenant()
    t = TenantFactory()
    agent = Role.unscoped.get(tenant=t, slug="agent")
    m = MembershipFactory(user=UserFactory(), tenant=t, role=agent)

    assert m.has_effective_permission("ticket.view") is True
    assert m.has_effective_permission("ticket.create") is True
    assert m.has_effective_permission("ticket.delete") is False
    assert m.has_effective_permission("billing.manage") is False
    assert m.has_effective_permission("user.delete") is False
