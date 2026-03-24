"""
Phase 4b — Authentication & RBAC tests.

Covers:
- JWT login/refresh flow
- Unauthenticated access denial
- Role hierarchy enforcement (Admin > Manager > Agent > Viewer)
- Permission boundary checks
- Object-level IsTicketAccessible
"""

import pytest

from conftest import (
    MembershipFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
    make_api_client,
)
from main.context import clear_current_tenant, set_current_tenant


# ── JWT Authentication ───────────────────────────────────────────────

@pytest.mark.django_db
class TestJWTAuth:
    def test_login_returns_tokens(self, tenant):
        user = UserFactory(email="jwt@test.com")
        user.set_password("securepass123")
        user.save()

        from rest_framework.test import APIClient
        client = APIClient()
        client.defaults["HTTP_HOST"] = f"{tenant.slug}.localhost:8001"

        resp = client.post("/api/v1/accounts/auth/login/", {
            "email": "jwt@test.com",
            "password": "securepass123",
        }, format="json")
        assert resp.status_code == 200
        assert "access" in resp.data
        assert "refresh" in resp.data

    def test_login_wrong_password(self, tenant):
        user = UserFactory(email="wrong@test.com")
        user.set_password("correctpass")
        user.save()

        from rest_framework.test import APIClient
        client = APIClient()
        client.defaults["HTTP_HOST"] = f"{tenant.slug}.localhost:8001"

        resp = client.post("/api/v1/accounts/auth/login/", {
            "email": "wrong@test.com",
            "password": "wrongpass",
        }, format="json")
        assert resp.status_code in (400, 401)

    def test_unauthenticated_access_denied(self, anon_client):
        resp = anon_client.get("/api/v1/tickets/tickets/")
        assert resp.status_code in (401, 403)


# ── Role Hierarchy ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestRoleHierarchy:
    def test_admin_can_list_users(self, admin_client):
        resp = admin_client.get("/api/v1/accounts/users/")
        assert resp.status_code == 200

    def test_viewer_can_list_tickets(self, viewer_client, tenant, default_status):
        """Viewers have view permission."""
        resp = viewer_client.get("/api/v1/tickets/tickets/")
        assert resp.status_code == 200

    def test_viewer_cannot_create_ticket(self, viewer_client, tenant, default_status):
        """Viewers (hierarchy_level=40) cannot create tickets."""
        resp = viewer_client.post("/api/v1/tickets/tickets/", {
            "subject": "Viewer ticket",
            "description": "Should fail",
            "status": str(default_status.pk),
            "priority": "medium",
        }, format="json")
        assert resp.status_code == 403

    def test_agent_can_create_ticket(self, agent_client, tenant, default_status):
        """Agents (hierarchy_level=30) can create tickets."""
        resp = agent_client.post("/api/v1/tickets/tickets/", {
            "subject": "Agent ticket",
            "description": "Should work",
            "priority": "medium",
        }, format="json")
        assert resp.status_code == 201, f"Got {resp.status_code}: {resp.data}"

    def test_admin_can_delete_ticket(self, admin_client, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()
        resp = admin_client.delete(f"/api/v1/tickets/tickets/{ticket.pk}/")
        assert resp.status_code in (204, 200)

    def test_agent_cannot_delete_ticket(self, agent_client, tenant, default_status, admin_user):
        """Agents cannot delete tickets (requires manager+)."""
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()
        resp = agent_client.delete(f"/api/v1/tickets/tickets/{ticket.pk}/")
        assert resp.status_code == 403


# ── Object-level: IsTicketAccessible ─────────────────────────────────

@pytest.mark.django_db
class TestTicketAccessibility:
    def test_agent_sees_own_ticket(self, agent_client, tenant, default_status, agent_user):
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=agent_user)
        clear_current_tenant()
        resp = agent_client.get(f"/api/v1/tickets/tickets/{ticket.pk}/")
        assert resp.status_code == 200

    def test_agent_sees_assigned_ticket(self, agent_client, tenant, default_status, agent_user, admin_user):
        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant, status=default_status,
            created_by=admin_user, assignee=agent_user,
        )
        clear_current_tenant()
        resp = agent_client.get(f"/api/v1/tickets/tickets/{ticket.pk}/")
        assert resp.status_code == 200

    def test_agent_cannot_see_others_ticket(self, agent_client, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()
        resp = agent_client.get(f"/api/v1/tickets/tickets/{ticket.pk}/")
        # 403 (permission denied) or 404 (filtered by queryset) both indicate blocked access
        assert resp.status_code in (403, 404)

    def test_admin_sees_all_tickets(self, admin_client, tenant, default_status):
        other = UserFactory()
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=other)
        clear_current_tenant()
        resp = admin_client.get(f"/api/v1/tickets/tickets/{ticket.pk}/")
        assert resp.status_code == 200


# ── Permission boundary: non-member denied ──────────────────────────

@pytest.mark.django_db
class TestNonMemberDenied:
    def test_non_member_cannot_access_tenant(self, tenant):
        outsider = UserFactory()
        client = make_api_client(outsider, tenant)
        resp = client.get("/api/v1/tickets/tickets/")
        # No membership → HasTenantPermission denies
        assert resp.status_code == 403
