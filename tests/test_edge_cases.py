"""
Phase 6 — Edge cases & regression tests.

Covers:
- Empty states
- Invalid UUIDs
- Pagination boundaries
- Model string representations
- Signal behavior (tenant creation → auto settings + roles)
"""

import uuid

import pytest

from conftest import (
    NotificationFactory,
    PlanFactory,
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


# ── Empty States ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEmptyStates:
    def test_empty_ticket_list(self, admin_client):
        resp = admin_client.get("/api/v1/tickets/tickets/")
        assert resp.status_code == 200
        assert resp.data["count"] == 0
        assert resp.data["results"] == []

    def test_empty_contact_list(self, admin_client):
        resp = admin_client.get("/api/v1/contacts/contacts/")
        assert resp.status_code == 200
        assert resp.data["count"] == 0

    def test_empty_board_list(self, admin_client):
        resp = admin_client.get("/api/v1/kanban/boards/")
        assert resp.status_code == 200
        assert resp.data["count"] == 0


# ── Invalid UUIDs ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestInvalidUUIDs:
    def test_invalid_uuid_returns_404(self, admin_client):
        fake_id = uuid.uuid4()
        resp = admin_client.get(f"/api/v1/tickets/tickets/{fake_id}/")
        assert resp.status_code == 404

    def test_malformed_uuid_returns_404(self, admin_client):
        resp = admin_client.get("/api/v1/tickets/tickets/not-a-uuid/")
        assert resp.status_code == 404

    def test_invalid_contact_uuid(self, admin_client):
        fake_id = uuid.uuid4()
        resp = admin_client.get(f"/api/v1/contacts/contacts/{fake_id}/")
        assert resp.status_code == 404


# ── Pagination ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPagination:
    def test_pagination_response_format(self, admin_client, tenant, admin_user, default_status):
        set_current_tenant(tenant)
        for _ in range(3):
            TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        resp = admin_client.get("/api/v1/tickets/tickets/?page_size=2")
        assert resp.status_code == 200
        assert "count" in resp.data
        assert "next" in resp.data
        assert "previous" in resp.data
        assert "results" in resp.data

    def test_page_2(self, admin_client, tenant, admin_user, default_status):
        set_current_tenant(tenant)
        for _ in range(55):
            TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        # Default page size is 50, so page 2 should have remaining tickets
        resp = admin_client.get("/api/v1/tickets/tickets/?page=2")
        assert resp.status_code == 200
        assert len(resp.data["results"]) >= 1


# ── Model __str__ representations ────────────────────────────────────

@pytest.mark.django_db
class TestModelStr:
    def test_tenant_str(self):
        t = TenantFactory(name="Acme")
        assert str(t) == "Acme"

    def test_user_str(self):
        u = UserFactory(email="str@test.com")
        assert str(u) == "str@test.com"

    def test_notification_str(self, tenant):
        user = UserFactory()
        set_current_tenant(tenant)
        n = NotificationFactory(tenant=tenant, recipient=user, title="Test")
        clear_current_tenant()
        assert "Test" in str(n)


# ── Signals: Tenant auto-setup ───────────────────────────────────────

@pytest.mark.django_db
class TestTenantSignals:
    def test_settings_auto_created(self):
        tenant = TenantFactory()
        assert hasattr(tenant, "settings")
        from apps.tenants.models import TenantSettings
        assert TenantSettings.objects.filter(tenant=tenant).exists()

    def test_default_roles_auto_created(self):
        tenant = TenantFactory()
        from apps.accounts.models import Role
        roles = Role.unscoped.filter(tenant=tenant)
        role_names = set(roles.values_list("name", flat=True))
        assert "Admin" in role_names
        assert "Manager" in role_names
        assert "Agent" in role_names
        assert "Viewer" in role_names

    def test_profile_auto_created_on_membership(self):
        tenant = TenantFactory()
        user = UserFactory()
        from apps.accounts.models import Role, TenantMembership
        role = Role.unscoped.filter(tenant=tenant).first()
        TenantMembership.objects.create(user=user, tenant=tenant, role=role)
        from apps.accounts.models import Profile
        assert Profile.unscoped.filter(user=user, tenant=tenant).exists()


# ── Ticket validation ────────────────────────────────────────────────

@pytest.mark.django_db
class TestTicketValidation:
    def test_missing_subject_rejected(self, admin_client, default_status):
        resp = admin_client.post("/api/v1/tickets/tickets/", {
            "description": "No subject",
            "status": str(default_status.pk),
            "priority": "medium",
        }, format="json")
        assert resp.status_code == 400

    def test_invalid_priority_rejected(self, admin_client, default_status):
        resp = admin_client.post("/api/v1/tickets/tickets/", {
            "subject": "Test",
            "description": "Body",
            "status": str(default_status.pk),
            "priority": "INVALID",
        }, format="json")
        assert resp.status_code == 400
