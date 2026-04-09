"""
Tests for the Contact context API endpoint.

Validates that:
- Returns only tickets from the current tenant for the contact
- Excludes the current ticket from recent_tickets
- avg_csat is null when no CSAT responses exist
- email_bouncing flag is present if Contact.email_bouncing=True
"""

import pytest

from conftest import (
    ContactFactory,
    MembershipFactory,
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
    make_api_client,
)
from main.context import clear_current_tenant, set_current_tenant


@pytest.fixture
def setup(tenant, admin_user):
    """Create common objects: statuses, contact, tickets."""
    set_current_tenant(tenant)
    open_status = TicketStatusFactory(
        tenant=tenant, name="Open", slug="open", is_default=True,
    )
    closed_status = TicketStatusFactory(
        tenant=tenant, name="Closed", slug="closed", is_closed=True,
    )
    contact = ContactFactory(tenant=tenant, email="customer@example.com")
    return open_status, closed_status, contact


class TestTenantScoping:
    """Returns only tickets from the current tenant for the contact."""

    def test_only_current_tenant_tickets(self, tenant, admin_user, admin_client, setup):
        open_status, _, contact = setup

        # Create 2 tickets for the contact in the current tenant
        t1 = TicketFactory(tenant=tenant, status=open_status, created_by=admin_user, contact=contact)
        t2 = TicketFactory(tenant=tenant, status=open_status, created_by=admin_user, contact=contact)

        # Create a ticket in another tenant for a contact with the same email
        tenant_b = TenantFactory(slug="tenant-ctx-b")
        set_current_tenant(tenant_b)
        other_status = TicketStatusFactory(tenant=tenant_b, name="Open", slug="open", is_default=True)
        user_b = UserFactory()
        from apps.accounts.models import Role
        MembershipFactory(user=user_b, tenant=tenant_b, role=Role.unscoped.get(tenant=tenant_b, slug="admin"))
        other_contact = ContactFactory(tenant=tenant_b, email="customer@example.com")
        TicketFactory(tenant=tenant_b, status=other_status, created_by=user_b, contact=other_contact)

        clear_current_tenant()

        resp = admin_client.get(f"/api/v1/contacts/contacts/{contact.pk}/context/")
        assert resp.status_code == 200

        data = resp.data
        assert data["stats"]["total_tickets"] == 2
        # recent_tickets should only contain tickets from tenant, not tenant_b
        ticket_ids = {t["id"] for t in data["recent_tickets"]}
        assert str(t1.pk) in ticket_ids
        assert str(t2.pk) in ticket_ids
        assert len(data["recent_tickets"]) == 2


class TestExcludeCurrentTicket:
    """Excludes the current ticket from recent_tickets."""

    def test_exclude_ticket_param(self, tenant, admin_user, admin_client, setup):
        open_status, _, contact = setup

        t1 = TicketFactory(tenant=tenant, status=open_status, created_by=admin_user, contact=contact)
        t2 = TicketFactory(tenant=tenant, status=open_status, created_by=admin_user, contact=contact)
        t3 = TicketFactory(tenant=tenant, status=open_status, created_by=admin_user, contact=contact)

        clear_current_tenant()

        resp = admin_client.get(
            f"/api/v1/contacts/contacts/{contact.pk}/context/?exclude_ticket={t2.pk}"
        )
        assert resp.status_code == 200

        ticket_ids = {t["id"] for t in resp.data["recent_tickets"]}
        assert str(t2.pk) not in ticket_ids
        assert str(t1.pk) in ticket_ids
        assert str(t3.pk) in ticket_ids


class TestAvgCsatNull:
    """avg_csat is null when no CSAT responses exist."""

    def test_no_csat_returns_null(self, tenant, admin_user, admin_client, setup):
        open_status, _, contact = setup

        TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
            contact=contact, csat_rating=None,
        )

        clear_current_tenant()

        resp = admin_client.get(f"/api/v1/contacts/contacts/{contact.pk}/context/")
        assert resp.status_code == 200
        assert resp.data["stats"]["avg_csat"] is None

    def test_with_csat_returns_average(self, tenant, admin_user, admin_client, setup):
        open_status, _, contact = setup

        TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
            contact=contact, csat_rating=5,
        )
        TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
            contact=contact, csat_rating=3,
        )

        clear_current_tenant()

        resp = admin_client.get(f"/api/v1/contacts/contacts/{contact.pk}/context/")
        assert resp.status_code == 200
        assert resp.data["stats"]["avg_csat"] == 4.0


class TestEmailBouncingFlag:
    """email_bouncing flag is present if Contact.email_bouncing=True."""

    def test_bouncing_true(self, tenant, admin_user, admin_client):
        set_current_tenant(tenant)
        TicketStatusFactory(tenant=tenant, name="Open", slug="open", is_default=True)
        contact = ContactFactory(
            tenant=tenant, email="bounce@example.com", email_bouncing=True,
        )
        clear_current_tenant()

        resp = admin_client.get(f"/api/v1/contacts/contacts/{contact.pk}/context/")
        assert resp.status_code == 200
        assert resp.data["contact"]["email_bouncing"] is True

    def test_bouncing_false(self, tenant, admin_user, admin_client):
        set_current_tenant(tenant)
        TicketStatusFactory(tenant=tenant, name="Open", slug="open", is_default=True)
        contact = ContactFactory(
            tenant=tenant, email="good@example.com", email_bouncing=False,
        )
        clear_current_tenant()

        resp = admin_client.get(f"/api/v1/contacts/contacts/{contact.pk}/context/")
        assert resp.status_code == 200
        assert resp.data["contact"]["email_bouncing"] is False
