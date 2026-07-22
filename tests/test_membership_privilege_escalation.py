"""Security probe — Manager -> Admin privilege escalation via TenantMembershipViewSet.

The 2026-07-13 QA-audit KZ-H04 fix hardened ``UserViewSet`` so an Agent can no
longer PATCH / deactivate another member. But the tenant-access levers that
actually matter live on ``TenantMembership`` (``role`` + ``is_active``), and the
*sibling* ``TenantMembershipViewSet`` was NOT hardened:

* it exposes ``UpdateModelMixin`` gated only by ``[IsAuthenticated,
  IsTenantAdminOrManager]`` — which a level-20 **Manager** passes;
* ``get_queryset`` returns **all** tenant memberships (incl. the Admin's);
* there is no ``perform_update`` / object-level guard; and
* ``TenantMembershipSerializer`` leaves **both** ``role`` and ``is_active``
  writable (``read_only_fields`` = id/user/tenant/invited_by/joined_at only).

So any Manager can PATCH ``/api/v1/accounts/memberships/{id}/`` to
  (a) set their own ``role`` -> Admin  (vertical privilege escalation), or
  (b) set the Admin's ``is_active`` -> False  (tenant lockout), or
  (c) demote the Admin's ``role``.

These tests assert the SECURE behaviour (403 + state unchanged). They FAIL
against the current source, demonstrating the open hole. Once the viewset gains
an object-level guard forbidding a non-Admin from touching an Admin membership /
elevating ``role`` to Admin / flipping ``is_active``, they pass.
"""

import pytest

from apps.accounts.models import TenantMembership
from conftest import make_api_client


def _membership(user, tenant):
    return TenantMembership.objects.get(user=user, tenant=tenant)


def _url(membership_id):
    return f"/api/v1/accounts/memberships/{membership_id}/"


@pytest.mark.django_db
class TestManagerCannotEscalateViaMembership:
    def test_manager_cannot_promote_self_to_admin(
        self, manager_client, manager_user, tenant, admin_role
    ):
        m = _membership(manager_user, tenant)
        resp = manager_client.patch(
            _url(m.id), {"role": str(admin_role.id)}, format="json"
        )
        m.refresh_from_db()
        assert m.role.slug != "admin", (
            "SECURITY: a Manager self-promoted to Admin via the membership "
            "endpoint (vertical privilege escalation)."
        )
        assert resp.status_code == 403

    def test_manager_cannot_deactivate_admin(self, manager_client, admin_user, tenant):
        m = _membership(admin_user, tenant)
        resp = manager_client.patch(
            _url(m.id), {"is_active": False}, format="json"
        )
        m.refresh_from_db()
        assert m.is_active is True, (
            "SECURITY: a Manager deactivated the Admin membership -> tenant lockout."
        )
        assert resp.status_code == 403

    def test_manager_cannot_demote_admin(
        self, manager_client, admin_user, tenant, agent_role
    ):
        m = _membership(admin_user, tenant)
        resp = manager_client.patch(
            _url(m.id), {"role": str(agent_role.id)}, format="json"
        )
        m.refresh_from_db()
        assert m.role.slug == "admin", (
            "SECURITY: a Manager demoted the Admin via the membership endpoint."
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestMembershipEndpointPositiveControls:
    """Confirm the endpoint is genuinely reachable/functional, so the deny
    tests above are not passing merely because the route is broken."""

    def test_admin_can_change_a_members_role(
        self, admin_client, agent_user, tenant, manager_role
    ):
        m = _membership(agent_user, tenant)
        resp = admin_client.patch(
            _url(m.id), {"role": str(manager_role.id)}, format="json"
        )
        assert resp.status_code == 200
        m.refresh_from_db()
        assert m.role.slug == "manager"

    def test_agent_cannot_touch_memberships_at_all(
        self, agent_client, admin_user, tenant, agent_role
    ):
        # Baseline: a plain Agent (level 30) is blocked by IsTenantAdminOrManager.
        m = _membership(admin_user, tenant)
        resp = agent_client.patch(
            _url(m.id), {"role": str(agent_role.id)}, format="json"
        )
        assert resp.status_code == 403
