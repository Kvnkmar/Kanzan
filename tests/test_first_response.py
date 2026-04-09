"""
Tests for first-response tracking.

Validates that ``first_responded_at`` is stamped only on customer-facing
outbound actions (external comments, outbound emails) — NOT on internal
actions like assignment or escalation.
"""

import pytest

from conftest import (
    MembershipFactory,
    QueueFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import set_current_tenant

from apps.tickets.models import TicketActivity
from apps.tickets.services import (
    assign_ticket,
    escalate_ticket,
    record_first_response,
)


@pytest.fixture
def setup(tenant, admin_user, agent_role):
    """Common setup: tenant context, default status, ticket, second agent."""
    set_current_tenant(tenant)
    default_status = TicketStatusFactory(
        tenant=tenant, is_default=True, name="Open", slug="open",
    )
    # in-progress status for auto-transition on assignment
    TicketStatusFactory(
        tenant=tenant, name="In Progress", slug="in-progress",
    )
    ticket = TicketFactory(
        tenant=tenant,
        status=default_status,
        created_by=admin_user,
    )
    other_agent = UserFactory()
    MembershipFactory(user=other_agent, tenant=tenant, role=agent_role)
    return ticket, admin_user, other_agent


class TestAssignmentDoesNotStampFirstResponse:
    """Assignment is an internal action — it must NOT stamp first_responded_at."""

    def test_assign_does_not_stamp(self, setup):
        ticket, creator, agent = setup
        assign_ticket(ticket, agent, creator)
        ticket.refresh_from_db()

        assert ticket.first_responded_at is None
        assert ticket.assignee == agent
        assert ticket.assigned_at is not None


class TestCreatorResponseDoesNotStamp:
    """The ticket creator replying to their own ticket must NOT stamp it."""

    def test_creator_comment_skipped(self, setup):
        ticket, creator, _ = setup
        record_first_response(ticket, creator)
        ticket.refresh_from_db()

        assert ticket.first_responded_at is None


class TestOutboundCommentStampsFirstResponse:
    """An outbound comment by a non-creator agent DOES stamp first_responded_at."""

    def test_agent_reply_stamps(self, setup):
        ticket, _, agent = setup
        assert ticket.first_responded_at is None

        record_first_response(ticket, agent)
        ticket.refresh_from_db()

        assert ticket.first_responded_at is not None

    def test_timeline_event_created(self, setup):
        ticket, _, agent = setup
        record_first_response(ticket, agent)

        event = TicketActivity.objects.filter(
            ticket=ticket, event=TicketActivity.Event.FIRST_RESPONSE,
        ).first()
        assert event is not None
        assert agent.get_full_name() in event.message or str(agent) in event.message


class TestIdempotency:
    """Second outbound reply must NOT overwrite first_responded_at."""

    def test_second_reply_no_overwrite(self, setup, agent_role):
        ticket, _, agent = setup

        # First response
        record_first_response(ticket, agent)
        ticket.refresh_from_db()
        first_ts = ticket.first_responded_at
        assert first_ts is not None

        # Second agent also replies
        another = UserFactory()
        MembershipFactory(user=another, tenant=ticket.tenant, role=agent_role)

        record_first_response(ticket, another)
        ticket.refresh_from_db()

        # Timestamp unchanged
        assert ticket.first_responded_at == first_ts

        # Only one FIRST_RESPONSE event
        count = TicketActivity.objects.filter(
            ticket=ticket, event=TicketActivity.Event.FIRST_RESPONSE,
        ).count()
        assert count == 1


class TestEscalationDoesNotStampFirstResponse:
    """Escalation is an internal action — it must NOT stamp first_responded_at."""

    def test_escalation_does_not_stamp(self, setup, tenant):
        ticket, creator, agent = setup
        queue = QueueFactory(tenant=tenant)
        set_current_tenant(tenant)

        escalate_ticket(
            ticket, creator, reason="Needs specialist",
            assignee=agent, queue=queue,
        )
        ticket.refresh_from_db()

        assert ticket.first_responded_at is None


class TestInternalCommentDoesNotStamp:
    """
    Internal notes are not customer-facing — they should NOT stamp
    first_responded_at. This test validates the gate condition used
    in the ViewSet comment action.
    """

    def test_internal_note_skipped(self, setup, admin_client, tenant):
        ticket, admin_user, _ = setup

        # Create an internal comment via the API
        url = f"/api/v1/tickets/tickets/{ticket.pk}/comments/"
        response = admin_client.post(url, {
            "body": "Internal note — not customer-facing",
            "is_internal": True,
        })
        # Accept either 201 (created) or other success code
        assert response.status_code in (200, 201), response.data

        ticket.refresh_from_db()
        assert ticket.first_responded_at is None
