"""
Phase 4c — Ticketing core tests.

Covers:
- Ticket CRUD
- Auto-incrementing ticket numbers
- Status transitions (open → closed → reopen)
- Assignment via API
- Priority changes
"""

import pytest

from conftest import (
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


@pytest.mark.django_db(transaction=True)
class TestTicketCRUD:
    def test_create_ticket(self, admin_client, tenant, default_status):
        resp = admin_client.post("/api/v1/tickets/tickets/", {
            "subject": "New ticket",
            "description": "A test ticket",
            "priority": "high",
        }, format="json")
        assert resp.status_code == 201, f"Got {resp.status_code}: {resp.data}"
        assert resp.data["subject"] == "New ticket"
        assert resp.data["priority"] == "high"

    def test_list_tickets(self, admin_client, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        resp = admin_client.get("/api/v1/tickets/tickets/")
        assert resp.status_code == 200
        assert resp.data["count"] == 2

    def test_retrieve_ticket(self, admin_client, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        resp = admin_client.get(f"/api/v1/tickets/tickets/{ticket.pk}/")
        assert resp.status_code == 200
        assert resp.data["subject"] == ticket.subject

    def test_update_ticket(self, admin_client, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        resp = admin_client.patch(f"/api/v1/tickets/tickets/{ticket.pk}/", {
            "subject": "Updated subject",
        }, format="json")
        assert resp.status_code == 200
        assert resp.data["subject"] == "Updated subject"

    def test_delete_ticket(self, admin_client, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        resp = admin_client.delete(f"/api/v1/tickets/tickets/{ticket.pk}/")
        assert resp.status_code in (204, 200)


@pytest.mark.django_db
class TestTicketNumber:
    def test_auto_increment(self, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        t1 = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user, number=0)
        t2 = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user, number=0)
        clear_current_tenant()
        assert t1.number >= 1
        assert t2.number >= 1
        assert t1.number != t2.number

    def test_separate_per_tenant(self, tenant, tenant_b, admin_user):
        set_current_tenant(tenant)
        status_a = TicketStatusFactory(tenant=tenant, name="OA", slug="oa")
        t1 = TicketFactory(tenant=tenant, status=status_a, created_by=admin_user, number=0)
        clear_current_tenant()

        set_current_tenant(tenant_b)
        status_b = TicketStatusFactory(tenant=tenant_b, name="OB", slug="ob")
        t2 = TicketFactory(tenant=tenant_b, status=status_b, created_by=admin_user, number=0)
        clear_current_tenant()

        assert t1.number == 1
        assert t2.number == 1


@pytest.mark.django_db
class TestStatusTransitions:
    def test_change_status_via_api(self, admin_client, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        closed = TicketStatusFactory(tenant=tenant, is_closed=True, name="Closed", slug="closed")
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        # Use PATCH to update the status (or POST to close action)
        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/close/",
            format="json",
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {getattr(resp, 'data', '')}"

    def test_close_sets_closed_at(self, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        closed = TicketStatusFactory(tenant=tenant, is_closed=True, name="Cl", slug="cl")
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)

        ticket.status = closed
        ticket.save()
        ticket.refresh_from_db()
        assert ticket.closed_at is not None
        clear_current_tenant()


@pytest.mark.django_db
class TestTicketAssignment:
    def test_assign_ticket(self, admin_client, tenant, default_status, admin_user, agent_user):
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/assign/",
            {"assignee": str(agent_user.pk)},
            format="json",
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestTicketPriority:
    def test_change_priority_via_patch(self, admin_client, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        resp = admin_client.patch(
            f"/api/v1/tickets/tickets/{ticket.pk}/",
            {"priority": "urgent"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["priority"] == "urgent"
