"""
Regression tests for the temporary-role / effective_role engine on
apps.accounts.models.TenantMembership.

Covers:
  (a) no temp role            -> effective_role == role
  (b) active temp role        -> effective_role == temporary_role; has_active_temporary_role True
  (c) expired temp role       -> falls back to permanent role
  (d) temp_permissions empty  -> full temp-role permission set applies
  (e) temp_permissions non-empty -> effective perms are the INTERSECTION
  (f) has_effective_permission(codename) reflects current role/perms

All roles/permissions are built via the real models so we control the exact
permission sets and never depend on which fixtures seed what.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from main.context import clear_current_tenant, set_current_tenant

from conftest import MembershipFactory, UserFactory


def _perm(codename, **extra):
    """Get-or-create a global Permission (Permission is NOT tenant-scoped)."""
    from apps.accounts.models import Permission

    resource, _, action = codename.partition(".")
    perm, _ = Permission.objects.get_or_create(
        codename=codename,
        defaults={"name": codename, "resource": resource, "action": action or "view"},
    )
    return perm


def _role(tenant, slug, hierarchy_level, codenames=()):
    """Create a tenant-scoped Role with the given permission codenames attached."""
    from apps.accounts.models import Role

    role = Role.unscoped.create(
        tenant=tenant,
        name=slug.title(),
        slug=slug,
        hierarchy_level=hierarchy_level,
        is_system=False,
    )
    if codenames:
        role.permissions.set([_perm(c) for c in codenames])
    return role


@pytest.fixture
def perm_roles(tenant):
    """A permanent (agent-ish) role and a temporary (manager-ish) role.

    Permanent role grants {ticket.view}.
    Temporary role grants {ticket.view, ticket.update, ticket.delete}.
    The overlap (ticket.view) and the temp-only perms let us probe the
    intersection / full-set branches precisely.
    """
    set_current_tenant(tenant)
    try:
        perm = _role(
            tenant, "perm-role", 30,
            codenames=["ticket.view"],
        )
        temp = _role(
            tenant, "temp-role", 20,
            codenames=["ticket.view", "ticket.update", "ticket.delete"],
        )
    finally:
        clear_current_tenant()
    return perm, temp


@pytest.fixture
def membership(tenant, perm_roles):
    perm_role, _temp = perm_roles
    user = UserFactory()
    return MembershipFactory(user=user, tenant=tenant, role=perm_role)


@pytest.mark.django_db
class TestEffectiveRole:
    def test_no_temp_role_effective_equals_permanent(self, membership, perm_roles):
        perm_role, _temp = perm_roles
        assert membership.temporary_role_id is None
        assert membership.has_active_temporary_role is False
        assert membership.effective_role == perm_role
        assert membership.effective_role.id == perm_role.id

    def test_active_temp_role_overrides_permanent(self, membership, perm_roles):
        perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = timezone.now() + timedelta(hours=1)
        membership.save()

        assert membership.has_active_temporary_role is True
        assert membership.effective_role == temp_role
        assert membership.effective_role != perm_role

    def test_expired_temp_role_falls_back_to_permanent(self, membership, perm_roles):
        perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = timezone.now() - timedelta(seconds=1)
        membership.save()

        assert membership.has_active_temporary_role is False
        assert membership.effective_role == perm_role

    def test_temp_role_set_but_no_expiry_is_not_active(self, membership, perm_roles):
        """A temp role with a NULL expiry must not be treated as active."""
        _perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = None
        membership.save()

        assert membership.has_active_temporary_role is False
        assert membership.effective_role == _perm_role


@pytest.mark.django_db
class TestEffectivePermissions:
    def test_no_temp_role_uses_permanent_perms(self, membership):
        codes = set(
            membership.get_effective_permissions_qs().values_list("codename", flat=True)
        )
        assert codes == {"ticket.view"}

    def test_active_temp_role_empty_allowlist_uses_full_temp_perms(
        self, membership, perm_roles
    ):
        """temporary_permissions empty -> the temp role's FULL perm set applies."""
        _perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = timezone.now() + timedelta(hours=1)
        membership.save()
        # explicitly leave temporary_permissions empty
        assert membership.temporary_permissions.exists() is False

        codes = set(
            membership.get_effective_permissions_qs().values_list("codename", flat=True)
        )
        assert codes == {"ticket.view", "ticket.update", "ticket.delete"}

    def test_active_temp_role_nonempty_allowlist_is_intersection(
        self, membership, perm_roles
    ):
        """temporary_permissions non-empty -> intersection with temp-role perms."""
        _perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = timezone.now() + timedelta(hours=1)
        membership.save()

        # Curate a subset of the temp role's perms.
        membership.temporary_permissions.set([_perm("ticket.update")])

        codes = set(
            membership.get_effective_permissions_qs().values_list("codename", flat=True)
        )
        # ticket.update is in BOTH temp-role perms and allowlist -> stays.
        # ticket.view / ticket.delete are temp-role perms NOT in allowlist -> dropped.
        assert codes == {"ticket.update"}

    def test_allowlist_perm_outside_temp_role_is_excluded(self, membership, perm_roles):
        """An allowlist entry the temp role does NOT grant must not leak in.

        The intersection is computed against the temp role's perms, so a
        codename present only in the allowlist (never in the role) must be
        excluded — the allowlist can only restrict, never widen.
        """
        _perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = timezone.now() + timedelta(hours=1)
        membership.save()

        # ticket.export is NOT one of the temp role's perms.
        membership.temporary_permissions.set([_perm("ticket.update"), _perm("ticket.export")])

        codes = set(
            membership.get_effective_permissions_qs().values_list("codename", flat=True)
        )
        assert codes == {"ticket.update"}
        assert "ticket.export" not in codes

    def test_expired_temp_role_ignores_allowlist_uses_permanent_perms(
        self, membership, perm_roles
    ):
        """An expired temp role + allowlist falls back to permanent perms fully."""
        _perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = timezone.now() - timedelta(seconds=1)
        membership.save()
        membership.temporary_permissions.set([_perm("ticket.update")])

        codes = set(
            membership.get_effective_permissions_qs().values_list("codename", flat=True)
        )
        # Back to the permanent role's full set, allowlist ignored.
        assert codes == {"ticket.view"}


@pytest.mark.django_db
class TestHasEffectivePermission:
    def test_permanent_perm_granted(self, membership):
        assert membership.has_effective_permission("ticket.view") is True

    def test_permanent_perm_not_granted(self, membership):
        assert membership.has_effective_permission("ticket.delete") is False

    def test_active_temp_role_grants_its_perms(self, membership, perm_roles):
        _perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = timezone.now() + timedelta(hours=1)
        membership.save()

        assert membership.has_effective_permission("ticket.delete") is True
        assert membership.has_effective_permission("ticket.update") is True

    def test_allowlist_restricts_has_effective_permission(self, membership, perm_roles):
        _perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = timezone.now() + timedelta(hours=1)
        membership.save()
        membership.temporary_permissions.set([_perm("ticket.update")])

        # Granted by temp role AND in allowlist.
        assert membership.has_effective_permission("ticket.update") is True
        # Granted by temp role but NOT in allowlist -> denied.
        assert membership.has_effective_permission("ticket.delete") is False
        assert membership.has_effective_permission("ticket.view") is False

    def test_expired_temp_role_reverts_has_effective_permission(
        self, membership, perm_roles
    ):
        _perm_role, temp_role = perm_roles
        membership.temporary_role = temp_role
        membership.temporary_role_expires_at = timezone.now() - timedelta(seconds=1)
        membership.save()

        # Temp role would have granted delete; expired -> reverts to permanent.
        assert membership.has_effective_permission("ticket.delete") is False
        assert membership.has_effective_permission("ticket.view") is True
