"""
Tests for the inbound-email auto-assignment feature.

Covers:
- pick_email_agent selection policy (load then fairness)
- auto_assign_email_ticket atomic assignment + audit
- End-to-end: inbound pipeline respects the tenant toggle
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Role
from apps.agents.models import AgentAvailability, AgentStatus
from apps.agents.services import auto_assign_email_ticket, pick_email_agent
from apps.inbound_email.services import process_inbound_email
from apps.tenants.models import TenantSettings
from apps.tickets.models import Ticket, TicketAssignment
from conftest import (
    ContactFactory,
    InboundEmailFactory,
    MembershipFactory,
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_with_roles(db):
    """
    Tenant with Admin / Manager / Agent roles and a default ticket status.

    Roles are created by the ``create_default_roles`` post-save signal on
    ``Tenant``; we look them up rather than constructing them, which would
    collide on the ``(tenant, slug)`` unique constraint.
    """
    tenant = TenantFactory()
    admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
    manager_role = Role.unscoped.get(tenant=tenant, slug="manager")
    agent_role = Role.unscoped.get(tenant=tenant, slug="agent")
    default_status = TicketStatusFactory(tenant=tenant, is_default=True)
    yield {
        "tenant": tenant,
        "admin_role": admin_role,
        "manager_role": manager_role,
        "agent_role": agent_role,
        "status": default_status,
    }


def _make_agent(tenant, agent_role, status=AgentStatus.ONLINE, ticket_count=0):
    """Create an agent user + membership + availability row."""
    user = UserFactory()
    MembershipFactory(user=user, tenant=tenant, role=agent_role)
    if status is not None:
        AgentAvailability.unscoped.create(
            tenant=tenant,
            user=user,
            status=status,
            current_ticket_count=ticket_count,
        )
    return user


def _open_ticket_for(tenant, user, status):
    """Create an open ticket assigned to user."""
    return TicketFactory(tenant=tenant, status=status, assignee=user)


# ---------------------------------------------------------------------------
# pick_email_agent — selection policy
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPickEmailAgentSelection:
    def test_returns_none_when_no_agents(self, tenant_with_roles):
        assert pick_email_agent(tenant_with_roles["tenant"]) is None

    def test_excludes_admins_and_managers(self, tenant_with_roles):
        tenant = tenant_with_roles["tenant"]
        admin = UserFactory()
        manager = UserFactory()
        MembershipFactory(user=admin, tenant=tenant, role=tenant_with_roles["admin_role"])
        MembershipFactory(user=manager, tenant=tenant, role=tenant_with_roles["manager_role"])

        # No pure Agents → nobody should be picked even though there are
        # online admins and managers.
        AgentAvailability.unscoped.create(tenant=tenant, user=admin, status=AgentStatus.ONLINE)
        AgentAvailability.unscoped.create(tenant=tenant, user=manager, status=AgentStatus.ONLINE)

        assert pick_email_agent(tenant) is None

    def test_skips_offline_agents(self, tenant_with_roles):
        tenant = tenant_with_roles["tenant"]
        agent_role = tenant_with_roles["agent_role"]
        online = _make_agent(tenant, agent_role, status=AgentStatus.ONLINE)
        _make_agent(tenant, agent_role, status=AgentStatus.OFFLINE)

        picked = pick_email_agent(tenant)
        assert picked == online

    def test_includes_agents_without_availability_row(self, tenant_with_roles):
        """Agents who never set availability must still be eligible; otherwise
        a freshly-invited agent would be permanently skipped."""
        tenant = tenant_with_roles["tenant"]
        agent_role = tenant_with_roles["agent_role"]
        agent = _make_agent(tenant, agent_role, status=None)  # no row
        picked = pick_email_agent(tenant)
        assert picked == agent

    def test_picks_agent_with_lowest_open_ticket_count(self, tenant_with_roles):
        tenant = tenant_with_roles["tenant"]
        agent_role = tenant_with_roles["agent_role"]
        status = tenant_with_roles["status"]

        heavy = _make_agent(tenant, agent_role)
        light = _make_agent(tenant, agent_role)
        # Load heavy with three open tickets, light with zero.
        for _ in range(3):
            _open_ticket_for(tenant, heavy, status)

        picked = pick_email_agent(tenant)
        assert picked == light

    def test_ignores_closed_tickets_for_load_count(self, tenant_with_roles):
        """Closed/resolved tickets must not count against an agent's load."""
        tenant = tenant_with_roles["tenant"]
        agent_role = tenant_with_roles["agent_role"]
        open_status = tenant_with_roles["status"]
        closed_status = TicketStatusFactory(tenant=tenant, is_closed=True)

        a_loaded_with_closed = _make_agent(tenant, agent_role)
        a_loaded_with_open = _make_agent(tenant, agent_role)

        # 5 closed tickets (should not count)
        for _ in range(5):
            TicketFactory(tenant=tenant, status=closed_status, assignee=a_loaded_with_closed)
        # 2 open tickets (do count)
        for _ in range(2):
            TicketFactory(tenant=tenant, status=open_status, assignee=a_loaded_with_open)

        picked = pick_email_agent(tenant)
        assert picked == a_loaded_with_closed  # 0 open < 2 open

    def test_ignores_soft_deleted_tickets(self, tenant_with_roles):
        tenant = tenant_with_roles["tenant"]
        agent_role = tenant_with_roles["agent_role"]
        status = tenant_with_roles["status"]

        a = _make_agent(tenant, agent_role)
        b = _make_agent(tenant, agent_role)

        # 3 soft-deleted tickets on A, 1 live on B → A should win.
        for _ in range(3):
            t = TicketFactory(tenant=tenant, status=status, assignee=a)
            t.is_deleted = True
            t.save(update_fields=["is_deleted", "updated_at"])
        TicketFactory(tenant=tenant, status=status, assignee=b)

        picked = pick_email_agent(tenant)
        assert picked == a

    def test_tiebreaks_by_least_recently_assigned(self, tenant_with_roles):
        tenant = tenant_with_roles["tenant"]
        agent_role = tenant_with_roles["agent_role"]
        status = tenant_with_roles["status"]

        earlier = _make_agent(tenant, agent_role)
        later = _make_agent(tenant, agent_role)

        # Both have zero current load but have each been assigned before.
        old_ticket = TicketFactory(tenant=tenant, status=status, assignee=earlier)
        TicketAssignment.objects.create(
            ticket=old_ticket, assigned_to=earlier, tenant=tenant,
        )
        # Close it so it doesn't count toward load.
        closed = TicketStatusFactory(tenant=tenant, is_closed=True)
        old_ticket.status = closed
        old_ticket.save(update_fields=["status", "updated_at"])

        recent_ticket = TicketFactory(tenant=tenant, status=status, assignee=later)
        ta = TicketAssignment.objects.create(
            ticket=recent_ticket, assigned_to=later, tenant=tenant,
        )
        # Close the ticket so load is equal, but keep a recent assignment stamp.
        recent_ticket.status = closed
        recent_ticket.save(update_fields=["status", "updated_at"])
        # Push `later`'s assignment timestamp into the recent past.
        TicketAssignment.unscoped.filter(pk=ta.pk).update(
            created_at=timezone.now(),
        )
        # Push `earlier`'s assignment timestamp further back.
        TicketAssignment.unscoped.filter(assigned_to=earlier).update(
            created_at=timezone.now() - timedelta(days=7),
        )

        # Equal load, earlier was assigned longer ago → earlier wins.
        picked = pick_email_agent(tenant)
        assert picked == earlier

    def test_never_assigned_agent_wins_over_previously_assigned(self, tenant_with_roles):
        """Cold-start fairness: a brand-new agent with NULL last_assigned_at
        must go before an agent who has been assigned before (when both have
        equal current load)."""
        tenant = tenant_with_roles["tenant"]
        agent_role = tenant_with_roles["agent_role"]
        status = tenant_with_roles["status"]

        never_assigned = _make_agent(tenant, agent_role)
        previously_assigned = _make_agent(tenant, agent_role)

        # Give previously_assigned a historical assignment (closed ticket).
        closed = TicketStatusFactory(tenant=tenant, is_closed=True)
        old_ticket = TicketFactory(tenant=tenant, status=closed, assignee=previously_assigned)
        TicketAssignment.objects.create(
            ticket=old_ticket, assigned_to=previously_assigned, tenant=tenant,
        )

        picked = pick_email_agent(tenant)
        assert picked == never_assigned


# ---------------------------------------------------------------------------
# auto_assign_email_ticket — mutation path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAutoAssignEmailTicket:
    def test_assigns_and_writes_audit_row(self, tenant_with_roles):
        tenant = tenant_with_roles["tenant"]
        agent = _make_agent(tenant, tenant_with_roles["agent_role"])
        ticket = TicketFactory(tenant=tenant, status=tenant_with_roles["status"])

        result = auto_assign_email_ticket(ticket)

        assert result == agent
        ticket.refresh_from_db()
        assert ticket.assignee == agent
        audit = TicketAssignment.unscoped.filter(ticket=ticket).get()
        assert audit.assigned_to == agent
        assert audit.assigned_by is None
        assert "Auto-assigned" in audit.note

    def test_no_op_when_already_assigned(self, tenant_with_roles):
        tenant = tenant_with_roles["tenant"]
        existing = _make_agent(tenant, tenant_with_roles["agent_role"])
        another = _make_agent(tenant, tenant_with_roles["agent_role"])  # would be picked
        ticket = TicketFactory(
            tenant=tenant, status=tenant_with_roles["status"], assignee=existing,
        )

        result = auto_assign_email_ticket(ticket)

        assert result is None
        ticket.refresh_from_db()
        assert ticket.assignee == existing  # unchanged
        assert TicketAssignment.unscoped.filter(ticket=ticket).count() == 0

    def test_no_op_when_no_eligible_agent(self, tenant_with_roles):
        # No agents created → None, ticket stays unassigned.
        ticket = TicketFactory(
            tenant=tenant_with_roles["tenant"],
            status=tenant_with_roles["status"],
        )
        assert auto_assign_email_ticket(ticket) is None
        ticket.refresh_from_db()
        assert ticket.assignee_id is None

    def test_bumps_current_ticket_count_when_availability_exists(self, tenant_with_roles):
        tenant = tenant_with_roles["tenant"]
        agent = _make_agent(tenant, tenant_with_roles["agent_role"], ticket_count=4)

        ticket = TicketFactory(tenant=tenant, status=tenant_with_roles["status"])
        auto_assign_email_ticket(ticket)

        av = AgentAvailability.unscoped.get(tenant=tenant, user=agent)
        assert av.current_ticket_count == 5

    def test_round_robin_on_repeated_assignments(self, tenant_with_roles):
        """Three consecutive email tickets should spread across three agents
        before returning to the first — load goes 0/0/0 → 1/0/0 → 1/1/0 → 1/1/1."""
        tenant = tenant_with_roles["tenant"]
        role = tenant_with_roles["agent_role"]
        status = tenant_with_roles["status"]
        a = _make_agent(tenant, role)
        b = _make_agent(tenant, role)
        c = _make_agent(tenant, role)

        assigned = []
        for _ in range(3):
            t = TicketFactory(tenant=tenant, status=status)
            assigned.append(auto_assign_email_ticket(t))

        # After three assignments, each agent should have received exactly
        # one ticket (set equality, not ordering — ordering depends on
        # internal PK order of the tied agents).
        assert set(assigned) == {a, b, c}


# ---------------------------------------------------------------------------
# End-to-end: inbound pipeline respects the tenant toggle
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInboundPipelineHonoursToggle:
    def _setup(self, toggle_on):
        tenant = TenantFactory(slug="acme")
        agent_role = Role.unscoped.get(tenant=tenant, slug="agent")
        status = TicketStatusFactory(tenant=tenant, is_default=True)
        agent = UserFactory()
        MembershipFactory(user=agent, tenant=tenant, role=agent_role)
        AgentAvailability.unscoped.create(
            tenant=tenant, user=agent, status=AgentStatus.ONLINE,
        )
        # TenantSettings row is created by signal; flip the toggle.
        TenantSettings.objects.filter(tenant=tenant).update(
            auto_assign_inbound_email_tickets=toggle_on,
        )
        # Inbound email for this tenant's address.
        set_current_tenant(tenant)
        try:
            ContactFactory(tenant=tenant, email="customer@example.com")
        finally:
            clear_current_tenant()
        inbound = InboundEmailFactory(
            tenant=None,  # unresolved until pipeline runs
            recipient_email=f"support+{tenant.slug}@kanzan.io",
            sender_email="customer@example.com",
            subject="Printer is broken",
            body_text="Please help.",
        )
        return tenant, agent, inbound

    def test_toggle_on_assigns_created_ticket(self, db, settings):
        tenant, agent, inbound = self._setup(toggle_on=True)

        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        ticket = Ticket.unscoped.get(tenant=tenant)
        assert ticket.assignee == agent
        # Audit row captures the automatic decision.
        assert TicketAssignment.unscoped.filter(
            ticket=ticket, assigned_to=agent,
        ).exists()

    def test_toggle_off_leaves_ticket_unassigned(self, db, settings):
        tenant, agent, inbound = self._setup(toggle_on=False)

        process_inbound_email(inbound.pk)

        ticket = Ticket.unscoped.get(tenant=tenant)
        assert ticket.assignee_id is None
        assert not TicketAssignment.unscoped.filter(ticket=ticket).exists()
