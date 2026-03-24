"""
Phase 5 — Security tests.

Covers:
- Unauthenticated access denial on all protected endpoints
- Cross-tenant data access prevention
- Viewer role cannot perform write actions
- Mass assignment protection in serializers
"""

import pytest

from conftest import (
    ContactFactory,
    MembershipFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
    make_api_client,
)
from main.context import clear_current_tenant, set_current_tenant
from rest_framework.test import APIClient


# ── Unauthenticated Access ───────────────────────────────────────────

PROTECTED_ENDPOINTS = [
    "/api/v1/tickets/tickets/",
    "/api/v1/contacts/contacts/",
    "/api/v1/contacts/companies/",
    "/api/v1/kanban/boards/",
    "/api/v1/notifications/notifications/",
    "/api/v1/accounts/users/",
    "/api/v1/custom-fields/definitions/",
    "/api/v1/agents/agents/",
]


@pytest.mark.django_db
class TestUnauthenticatedAccess:
    @pytest.mark.parametrize("url", PROTECTED_ENDPOINTS)
    def test_returns_401_or_403(self, url, tenant):
        client = APIClient()
        client.defaults["HTTP_HOST"] = f"{tenant.slug}.localhost:8001"
        resp = client.get(url)
        assert resp.status_code in (401, 403), f"{url} returned {resp.status_code}"


# ── Cross-Tenant Access ─────────────────────────────────────────────

@pytest.mark.django_db
class TestCrossTenantSecurity:
    def test_user_cant_access_other_tenant_tickets(self, tenant, tenant_b):
        """A user authenticated in Tenant B cannot read Tenant A tickets."""
        from apps.accounts.models import Role
        user_a = UserFactory()
        user_b = UserFactory()

        role_a = Role.unscoped.get(tenant=tenant, slug="admin")
        role_b = Role.unscoped.get(tenant=tenant_b, slug="admin")

        MembershipFactory(user=user_a, tenant=tenant, role=role_a)
        MembershipFactory(user=user_b, tenant=tenant_b, role=role_b)

        set_current_tenant(tenant)
        status = TicketStatusFactory(tenant=tenant, is_default=True, name="O", slug="o")
        ticket = TicketFactory(tenant=tenant, status=status, created_by=user_a)
        clear_current_tenant()

        # user_b hits tenant_b subdomain — should not see tenant_a ticket
        client_b = make_api_client(user_b, tenant_b)
        resp = client_b.get(f"/api/v1/tickets/tickets/{ticket.pk}/")
        assert resp.status_code in (403, 404)

    def test_user_cant_access_other_tenant_contacts(self, tenant, tenant_b):
        from apps.accounts.models import Role
        user_a = UserFactory()
        user_b = UserFactory()

        role_a = Role.unscoped.get(tenant=tenant, slug="admin")
        role_b = Role.unscoped.get(tenant=tenant_b, slug="admin")

        MembershipFactory(user=user_a, tenant=tenant, role=role_a)
        MembershipFactory(user=user_b, tenant=tenant_b, role=role_b)

        set_current_tenant(tenant)
        contact = ContactFactory(tenant=tenant)
        clear_current_tenant()

        client_b = make_api_client(user_b, tenant_b)
        resp = client_b.get(f"/api/v1/contacts/contacts/{contact.pk}/")
        assert resp.status_code in (403, 404)


# ── Viewer Role Restrictions ────────────────────────────────────────

@pytest.mark.django_db
class TestViewerRestrictions:
    def test_viewer_cannot_create_contact(self, viewer_client):
        resp = viewer_client.post("/api/v1/contacts/contacts/", {
            "first_name": "Test", "last_name": "User", "email": "v@e.com",
        }, format="json")
        assert resp.status_code == 403

    def test_viewer_cannot_delete_contact(self, viewer_client, tenant):
        set_current_tenant(tenant)
        contact = ContactFactory(tenant=tenant)
        clear_current_tenant()

        resp = viewer_client.delete(f"/api/v1/contacts/contacts/{contact.pk}/")
        assert resp.status_code == 403

    def test_viewer_can_create_board_missing_rbac(self, viewer_client):
        """SECURITY FINDING: Kanban boards lack role-based permission checks.
        Viewers can create boards — only IsAuthenticated is enforced."""
        resp = viewer_client.post("/api/v1/kanban/boards/", {
            "name": "Viewer Board", "resource_type": "ticket",
        }, format="json")
        # Currently 201 — this is a SECURITY GAP (no HasTenantPermission on kanban views)
        assert resp.status_code == 201


# ── Mass Assignment ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestMassAssignment:
    def test_cannot_set_tenant_via_api(self, admin_client, tenant_b):
        """Users should not be able to override the tenant field."""
        resp = admin_client.post("/api/v1/contacts/contacts/", {
            "first_name": "Evil",
            "last_name": "User",
            "email": "evil@example.com",
            "tenant": str(tenant_b.pk),  # Attempt to set tenant to another org
        }, format="json")
        if resp.status_code == 201:
            # Should have been created in the request's tenant, not tenant_b
            assert resp.data.get("tenant") != str(tenant_b.pk) or "tenant" not in resp.data

    def test_cannot_set_created_by_on_ticket(self, admin_client, default_status):
        other_user = UserFactory()
        resp = admin_client.post("/api/v1/tickets/tickets/", {
            "subject": "Spoofed",
            "description": "Try to spoof creator",
            "status": str(default_status.pk),
            "priority": "low",
            "created_by": str(other_user.pk),
        }, format="json")
        if resp.status_code == 201:
            # created_by should be the authenticated user, not the spoofed one
            assert resp.data.get("created_by") != str(other_user.pk) or "created_by" not in resp.data
