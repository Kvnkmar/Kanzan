"""
Tests for pre_wait_status snapshot/restore on Waiting transitions.

Validates that:
- In Progress → Waiting → resume restores to In Progress
- Open → Waiting → resume restores to Open
- Resolved ticket + customer reply goes to Open (not pre_wait_status)
- pre_wait_status is cleared after consumption
"""

import pytest

from conftest import (
    MembershipFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import set_current_tenant

from apps.accounts.models import Role
from apps.tickets.services import (
    change_ticket_status,
    resume_from_wait,
    transition_ticket_status,
)


def _make_statuses(tenant):
    """Create the standard status set with Waiting configured as pauses_sla."""
    set_current_tenant(tenant)
    return {
        "open": TicketStatusFactory(
            tenant=tenant, name="Open", slug="open",
            order=10, is_default=True,
        ),
        "in-progress": TicketStatusFactory(
            tenant=tenant, name="In Progress", slug="in-progress",
            order=20,
        ),
        "waiting": TicketStatusFactory(
            tenant=tenant, name="Waiting", slug="waiting",
            order=30, pauses_sla=True,
        ),
        "resolved": TicketStatusFactory(
            tenant=tenant, name="Resolved", slug="resolved",
            order=40, is_closed=True,
        ),
        "closed": TicketStatusFactory(
            tenant=tenant, name="Closed", slug="closed",
            order=50, is_closed=True,
        ),
    }


class TestInProgressToWaitingResume:
    """In Progress → Waiting → resume: restores to In Progress."""

    def test_restores_in_progress(self, tenant, admin_user):
        statuses = _make_statuses(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )

        # Transition to Waiting
        transition_ticket_status(ticket, statuses["waiting"], admin_user)
        ticket.refresh_from_db()
        assert ticket.status.slug == "waiting"
        assert ticket.pre_wait_status_id == statuses["in-progress"].pk

        # Resume from wait — should restore to In Progress
        resume_from_wait(ticket, admin_user)
        ticket.refresh_from_db()
        assert ticket.status.slug == "in-progress"


class TestOpenToWaitingResume:
    """Open → Waiting → resume: restores to Open."""

    def test_restores_open(self, tenant, admin_user):
        statuses = _make_statuses(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["open"],
            created_by=admin_user,
        )

        # Transition to Waiting
        transition_ticket_status(ticket, statuses["waiting"], admin_user)
        ticket.refresh_from_db()
        assert ticket.status.slug == "waiting"
        assert ticket.pre_wait_status_id == statuses["open"].pk

        # Resume — should restore to Open
        resume_from_wait(ticket, admin_user)
        ticket.refresh_from_db()
        assert ticket.status.slug == "open"


class TestResolvedCustomerReplyGoesToOpen:
    """Resolved ticket + customer reply always goes to Open, not pre_wait_status."""

    def test_resolved_reopen_ignores_pre_wait(self, tenant, admin_user):
        statuses = _make_statuses(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )

        # Simulate: In Progress → Waiting → Resolved (unusual but possible
        # if an agent resolves from waiting). We manually set pre_wait_status
        # to demonstrate it's ignored on the resolved→open path.
        ticket.pre_wait_status = statuses["in-progress"]
        ticket.save(update_fields=["pre_wait_status"])

        # Transition to resolved
        change_ticket_status(ticket, statuses["resolved"], admin_user)
        ticket.refresh_from_db()
        assert ticket.status.slug == "resolved"

        # Simulate customer reply reopening via the inbound email path
        from apps.inbound_email.services import _reopen_resolved_ticket
        from apps.inbound_email.services import get_system_user

        system_user = get_system_user(tenant)
        _reopen_resolved_ticket(ticket, system_user)
        ticket.refresh_from_db()

        # Should go to "open", NOT "in-progress"
        assert ticket.status.slug == "open"


class TestPreWaitStatusClearedAfterConsumption:
    """pre_wait_status is set to null after the resume transition."""

    def test_cleared_after_resume(self, tenant, admin_user):
        statuses = _make_statuses(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )

        # Enter waiting
        transition_ticket_status(ticket, statuses["waiting"], admin_user)
        ticket.refresh_from_db()
        assert ticket.pre_wait_status is not None

        # Resume
        resume_from_wait(ticket, admin_user)
        ticket.refresh_from_db()
        assert ticket.status.slug == "in-progress"
        # pre_wait_status must be cleared
        assert ticket.pre_wait_status is None

    def test_cleared_on_manual_transition_out(self, tenant, admin_user):
        """Agent manually picks a different target — pre_wait_status is still cleared."""
        statuses = _make_statuses(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )

        # Enter waiting
        transition_ticket_status(ticket, statuses["waiting"], admin_user)
        ticket.refresh_from_db()
        assert ticket.pre_wait_status_id == statuses["in-progress"].pk

        # Agent manually transitions to Open (not using resume_from_wait)
        transition_ticket_status(ticket, statuses["open"], admin_user)
        ticket.refresh_from_db()
        assert ticket.status.slug == "open"
        # pre_wait_status still cleared by change_ticket_status
        assert ticket.pre_wait_status is None


class TestCustomStatusBenefits:
    """Custom (unrestricted) statuses also get snapshot/restore."""

    def test_custom_status_to_waiting_and_back(self, tenant, admin_user):
        statuses = _make_statuses(tenant)
        # Create a custom status not in ALLOWED_TRANSITIONS
        set_current_tenant(tenant)
        custom = TicketStatusFactory(
            tenant=tenant, name="Investigating", slug="investigating", order=25,
        )

        ticket = TicketFactory(
            tenant=tenant,
            status=custom,
            created_by=admin_user,
        )

        # Custom → Waiting (unrestricted transition)
        change_ticket_status(ticket, statuses["waiting"], admin_user)
        ticket.refresh_from_db()
        assert ticket.status.slug == "waiting"
        assert ticket.pre_wait_status_id == custom.pk

        # Resume — should restore to the custom status
        resume_from_wait(ticket, admin_user)
        ticket.refresh_from_db()
        assert ticket.status.slug == "investigating"
        assert ticket.pre_wait_status is None
