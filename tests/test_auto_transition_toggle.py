"""
Tests for TenantSettings.auto_transition_on_assign toggle.

Validates that:
- When True (default): assigning a ticket on the Open status transitions
  it to In Progress.
- When False: assignment leaves the status unchanged.
- The setting is tenant-isolated.
"""

import pytest

from conftest import (
    MembershipFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant

from apps.accounts.models import Role
from apps.tickets.services import assign_ticket


def _setup_tenant(tenant):
    """Create Open + In Progress statuses for a tenant."""
    set_current_tenant(tenant)
    open_status = TicketStatusFactory(
        tenant=tenant, name="Open", slug="open", is_default=True, order=10,
    )
    TicketStatusFactory(
        tenant=tenant, name="In Progress", slug="in-progress", order=20,
    )
    return open_status


class TestAutoTransitionEnabled:
    """Default behaviour (toggle=True): assignment transitions Open → In Progress."""

    def test_assignment_transitions_status(self, tenant, admin_user):
        open_status = _setup_tenant(tenant)

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        ticket = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )

        # Default is True — transition should happen
        assert tenant.settings.auto_transition_on_assign is True

        assign_ticket(ticket, agent, admin_user)
        ticket.refresh_from_db()

        assert ticket.status.slug == "in-progress"
        assert ticket.assignee == agent


class TestAutoTransitionDisabled:
    """When toggle=False: assignment does NOT transition status."""

    def test_assignment_preserves_status(self, tenant, admin_user):
        open_status = _setup_tenant(tenant)

        # Disable the toggle
        tenant.settings.auto_transition_on_assign = False
        tenant.settings.save(update_fields=["auto_transition_on_assign"])

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        ticket = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )

        assign_ticket(ticket, agent, admin_user)
        ticket.refresh_from_db()

        # Status should remain "Open"
        assert ticket.status.slug == "open"
        # Assignment itself still works
        assert ticket.assignee == agent
        assert ticket.assigned_at is not None


class TestTenantIsolation:
    """Changing the setting for tenant A does not affect tenant B."""

    def test_setting_isolated_between_tenants(self, tenant, admin_user, tenant_b):
        # Tenant A: disable
        open_a = _setup_tenant(tenant)
        tenant.settings.auto_transition_on_assign = False
        tenant.settings.save(update_fields=["auto_transition_on_assign"])

        # Tenant B: leave default (enabled)
        set_current_tenant(tenant_b)
        open_b = TicketStatusFactory(
            tenant=tenant_b, name="Open", slug="open", is_default=True, order=10,
        )
        TicketStatusFactory(
            tenant=tenant_b, name="In Progress", slug="in-progress", order=20,
        )

        admin_b = UserFactory()
        agent_b = UserFactory()
        MembershipFactory(
            user=admin_b, tenant=tenant_b,
            role=Role.unscoped.get(tenant=tenant_b, slug="admin"),
        )
        MembershipFactory(
            user=agent_b, tenant=tenant_b,
            role=Role.unscoped.get(tenant=tenant_b, slug="agent"),
        )

        agent_a = UserFactory()
        MembershipFactory(
            user=agent_a, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        # Tenant A ticket: should NOT transition
        set_current_tenant(tenant)
        ticket_a = TicketFactory(
            tenant=tenant, status=open_a, created_by=admin_user,
        )
        assign_ticket(ticket_a, agent_a, admin_user)
        ticket_a.refresh_from_db()
        assert ticket_a.status.slug == "open"

        # Tenant B ticket: should transition
        set_current_tenant(tenant_b)
        ticket_b = TicketFactory(
            tenant=tenant_b, status=open_b, created_by=admin_b,
        )
        assign_ticket(ticket_b, agent_b, admin_b)
        ticket_b.refresh_from_db()
        assert ticket_b.status.slug == "in-progress"

        clear_current_tenant()
