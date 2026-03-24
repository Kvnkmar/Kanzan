"""
Phase 4a — Multi-tenancy isolation tests.

Covers:
- Tenant context manager auto-filtering
- Cross-tenant data leakage prevention
- TenantScopedModel auto-assignment
- TenantAwareManager empty-queryset when no context
- Subdomain routing (via HTTP_HOST header)
"""

import pytest
from django.test import RequestFactory

from main.context import clear_current_tenant, get_current_tenant, set_current_tenant
from main.models import TenantScopedModel

from conftest import (
    ContactFactory,
    MembershipFactory,
    RoleFactory,
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)


# ── Context manager ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestTenantContext:
    def test_set_and_get_tenant(self, tenant):
        set_current_tenant(tenant)
        assert get_current_tenant() == tenant

    def test_clear_tenant(self, tenant):
        set_current_tenant(tenant)
        clear_current_tenant()
        assert get_current_tenant() is None

    def test_default_is_none(self):
        assert get_current_tenant() is None


# ── TenantAwareManager ───────────────────────────────────────────────

@pytest.mark.django_db
class TestTenantAwareManager:
    def test_filters_by_current_tenant(self, tenant, tenant_b):
        set_current_tenant(tenant)
        status_a = TicketStatusFactory(tenant=tenant, name="Open A", slug="open-a")
        clear_current_tenant()

        set_current_tenant(tenant_b)
        status_b = TicketStatusFactory(tenant=tenant_b, name="Open B", slug="open-b")
        clear_current_tenant()

        # Query with tenant A context
        set_current_tenant(tenant)
        from apps.tickets.models import TicketStatus
        qs = TicketStatus.objects.all()
        assert status_a in qs
        assert status_b not in qs

    def test_returns_empty_without_context(self):
        clear_current_tenant()
        from apps.tickets.models import TicketStatus
        qs = TicketStatus.objects.all()
        assert qs.count() == 0

    def test_unscoped_returns_all(self, tenant, tenant_b):
        set_current_tenant(tenant)
        TicketStatusFactory(tenant=tenant, name="A", slug="a")
        clear_current_tenant()

        set_current_tenant(tenant_b)
        TicketStatusFactory(tenant=tenant_b, name="B", slug="b")
        clear_current_tenant()

        from apps.tickets.models import TicketStatus
        assert TicketStatus.unscoped.count() >= 2


# ── TenantScopedModel.save() ────────────────────────────────────────

@pytest.mark.django_db
class TestTenantScopedModelSave:
    def test_auto_assigns_tenant_from_context(self, tenant):
        set_current_tenant(tenant)
        from apps.tickets.models import TicketStatus
        status = TicketStatus(name="Test", slug="test-auto", order=99)
        status.save()
        assert status.tenant == tenant

    def test_raises_without_context(self):
        clear_current_tenant()
        from apps.tickets.models import TicketStatus
        status = TicketStatus(name="NoCtx", slug="no-ctx", order=99)
        with pytest.raises(ValueError, match="without a tenant context"):
            status.save()


# ── Cross-tenant data isolation ──────────────────────────────────────

@pytest.mark.django_db
class TestCrossTenantIsolation:
    def test_contacts_isolated(self, tenant, tenant_b):
        set_current_tenant(tenant)
        c1 = ContactFactory(tenant=tenant, email="shared@example.com")
        clear_current_tenant()

        set_current_tenant(tenant_b)
        c2 = ContactFactory(tenant=tenant_b, email="shared@example.com")
        clear_current_tenant()

        from apps.contacts.models import Contact
        set_current_tenant(tenant)
        assert list(Contact.objects.all()) == [c1]

        set_current_tenant(tenant_b)
        assert list(Contact.objects.all()) == [c2]

    def test_tickets_isolated(self, tenant, tenant_b):
        user = UserFactory()
        set_current_tenant(tenant)
        status_a = TicketStatusFactory(tenant=tenant, name="OA", slug="oa")
        t1 = TicketFactory(tenant=tenant, status=status_a, created_by=user)
        clear_current_tenant()

        set_current_tenant(tenant_b)
        status_b = TicketStatusFactory(tenant=tenant_b, name="OB", slug="ob")
        t2 = TicketFactory(tenant=tenant_b, status=status_b, created_by=user)
        clear_current_tenant()

        from apps.tickets.models import Ticket
        set_current_tenant(tenant)
        assert Ticket.objects.count() == 1
        assert Ticket.objects.first() == t1

    def test_user_memberships_scoped(self, tenant, tenant_b):
        user = UserFactory()
        from apps.accounts.models import Role
        role_a = Role.unscoped.get(tenant=tenant, slug="agent")
        role_b = Role.unscoped.get(tenant=tenant_b, slug="agent")

        m1 = MembershipFactory(user=user, tenant=tenant, role=role_a)
        m2 = MembershipFactory(user=user, tenant=tenant_b, role=role_b)

        from apps.accounts.models import TenantMembership
        memberships = TenantMembership.objects.filter(user=user)
        assert memberships.count() == 2
        tenants = set(memberships.values_list("tenant_id", flat=True))
        assert tenant.pk in tenants
        assert tenant_b.pk in tenants


# ── Subdomain routing via API ────────────────────────────────────────

@pytest.mark.django_db
class TestSubdomainRouting:
    def test_api_returns_tenant_scoped_data(self, admin_client, tenant, default_status):
        """Tickets listed via API only show current tenant data."""
        set_current_tenant(tenant)
        TicketFactory(tenant=tenant, status=default_status, created_by=UserFactory())
        clear_current_tenant()

        resp = admin_client.get("/api/v1/tickets/tickets/")
        assert resp.status_code == 200
        assert resp.data["count"] >= 1

    def test_wrong_subdomain_no_data(self, tenant, tenant_b, admin_user):
        """Authenticated user on wrong subdomain gets no tenant-scoped data."""
        from conftest import make_api_client

        set_current_tenant(tenant)
        status = TicketStatusFactory(tenant=tenant, name="O", slug="o", is_default=True)
        TicketFactory(tenant=tenant, status=status, created_by=admin_user)
        clear_current_tenant()

        # Client hits tenant_b subdomain — should not see tenant A tickets
        client = make_api_client(admin_user, tenant_b)
        resp = client.get("/api/v1/tickets/tickets/")
        # Might be 200 with empty list or 403 depending on membership
        if resp.status_code == 200:
            assert resp.data["count"] == 0
