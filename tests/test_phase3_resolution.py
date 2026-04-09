"""
Phase 3 — Resolution tests.

Covers:
- Status transition enforcement (illegal transitions raise ValidationError)
- SLA pause/resume arithmetic (deadline shifting with business hours)
- First-response breach detection and signal firing
- Escalation atomicity (reassign + internal note + SLA recalculation)
"""

import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from apps.accounts.models import Role
from conftest import (
    MembershipFactory,
    QueueFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_statuses(tenant):
    """Create the standard set of ticket statuses for a tenant."""
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


def _make_ticket(tenant, status, created_by, **kwargs):
    """Create a ticket with tenant context set."""
    set_current_tenant(tenant)
    ticket = TicketFactory(
        tenant=tenant, status=status, created_by=created_by, **kwargs,
    )
    clear_current_tenant()
    return ticket


# ===========================================================================
# 1. STATUS TRANSITION ENFORCEMENT
# ===========================================================================


@pytest.mark.django_db(transaction=True)
class TestStatusTransitions:
    """Verify that the transition guard enforces allowed status paths."""

    def test_open_to_in_progress_allowed(self, tenant, admin_user):
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        result = transition_ticket_status(ticket, statuses["in-progress"], admin_user)
        clear_current_tenant()

        assert result.status.slug == "in-progress"
        assert result.status_changed_at is not None
        assert result.status_changed_by == admin_user

    def test_open_to_waiting_allowed(self, tenant, admin_user):
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        result = transition_ticket_status(ticket, statuses["waiting"], admin_user)
        clear_current_tenant()

        assert result.status.slug == "waiting"

    def test_open_to_resolved_allowed(self, tenant, admin_user):
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        result = transition_ticket_status(ticket, statuses["resolved"], admin_user)
        clear_current_tenant()

        assert result.status.slug == "resolved"

    def test_closed_to_anything_blocked(self, tenant, admin_user):
        """Closed is a terminal status — no transitions out."""
        from django.core.exceptions import ValidationError

        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["closed"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        with pytest.raises(ValidationError, match="Cannot transition"):
            transition_ticket_status(ticket, statuses["open"], admin_user)
        clear_current_tenant()

    def test_waiting_to_resolved_blocked(self, tenant, admin_user):
        """Waiting can only go to open or in-progress."""
        from django.core.exceptions import ValidationError

        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["waiting"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        with pytest.raises(ValidationError, match="Cannot transition"):
            transition_ticket_status(ticket, statuses["resolved"], admin_user)
        clear_current_tenant()

    def test_waiting_to_open_allowed(self, tenant, admin_user):
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["waiting"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        result = transition_ticket_status(ticket, statuses["open"], admin_user)
        clear_current_tenant()

        assert result.status.slug == "open"

    def test_resolved_to_open_allowed(self, tenant, admin_user):
        """Resolved can be reopened to open."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["resolved"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        result = transition_ticket_status(ticket, statuses["open"], admin_user)
        clear_current_tenant()

        assert result.status.slug == "open"

    def test_resolved_to_in_progress_blocked(self, tenant, admin_user):
        """Resolved can only go to closed or open, not in-progress."""
        from django.core.exceptions import ValidationError

        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["resolved"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        with pytest.raises(ValidationError, match="Cannot transition"):
            transition_ticket_status(ticket, statuses["in-progress"], admin_user)
        clear_current_tenant()

    def test_same_status_is_noop(self, tenant, admin_user):
        """Transitioning to the same status returns the ticket unchanged."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        result = transition_ticket_status(ticket, statuses["open"], admin_user)
        clear_current_tenant()

        assert result.status.slug == "open"
        # status_changed_at should not be set (no actual change)
        assert result.status_changed_at is None

    def test_custom_status_unrestricted(self, tenant, admin_user):
        """Statuses with slugs not in the map can transition freely."""
        statuses = _make_statuses(tenant)
        custom = TicketStatusFactory(
            tenant=tenant, name="Custom", slug="custom-status", order=99,
        )
        ticket = _make_ticket(tenant, custom, admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        result = transition_ticket_status(ticket, statuses["closed"], admin_user)
        clear_current_tenant()

        assert result.status.slug == "closed"

    def test_change_status_api(self, admin_client, tenant, admin_user):
        """Test the change-status API action enforces the guard."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/change-status/",
            {"status": str(statuses["in-progress"].pk)},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["status"]["slug"] == "in-progress"

    def test_change_status_api_blocked(self, admin_client, tenant, admin_user):
        """Test the change-status API action rejects illegal transitions."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["closed"], admin_user)

        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/change-status/",
            {"status": str(statuses["in-progress"].pk)},
            format="json",
        )
        assert resp.status_code == 400
        assert "Cannot transition" in resp.data["detail"]


# ===========================================================================
# 2. SLA PAUSE / RESUME ARITHMETIC
# ===========================================================================


@pytest.mark.django_db(transaction=True)
class TestSLAPauseResume:
    """Verify that SLA deadlines are shifted correctly on pause/resume."""

    def test_deadlines_shift_on_resume(self, tenant, admin_user):
        """When a ticket is un-paused, deadlines shift forward by pause duration."""
        from django.utils import timezone

        from apps.tickets.models import SLAPause

        statuses = _make_statuses(tenant)

        # Use a fixed time within business hours (Wed 10:00 UTC) so that
        # the pause duration is counted as business minutes regardless of
        # when the test suite actually runs.
        now = datetime.datetime(2026, 4, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)

        # Create a ticket with SLA deadlines
        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["open"],
            created_by=admin_user,
            sla_first_response_due=now + datetime.timedelta(hours=4),
            sla_resolution_due=now + datetime.timedelta(hours=24),
        )
        clear_current_tenant()

        original_response_due = ticket.sla_first_response_due
        original_resolution_due = ticket.sla_resolution_due

        # Simulate pause → resume with a 30 minute gap (10:00–10:30)
        pause_start = now
        pause_end = now + datetime.timedelta(minutes=30)

        set_current_tenant(tenant)

        # Create a pause record
        pause = SLAPause.objects.create(
            tenant=tenant,
            ticket=ticket,
            paused_at=pause_start,
            reason=SLAPause.Reason.WAITING_ON_CUSTOMER,
        )
        ticket.sla_paused_at = pause_start
        ticket.save(update_fields=["sla_paused_at"])

        # Resume the pause
        from apps.tickets.signals import _resume_sla_pause

        _resume_sla_pause(ticket, now=pause_end, reason="status_change")

        clear_current_tenant()

        ticket.refresh_from_db()

        # Deadlines should have shifted forward by 30 business minutes
        assert ticket.sla_first_response_due > original_response_due
        assert ticket.sla_resolution_due > original_resolution_due
        assert ticket.sla_paused_at is None

    def test_status_to_waiting_creates_pause(self, tenant, admin_user):
        """Moving to a pauses_sla=True status creates an SLAPause and sets sla_paused_at."""
        from apps.tickets.models import SLAPause
        from apps.tickets.services import transition_ticket_status

        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        set_current_tenant(tenant)
        transition_ticket_status(ticket, statuses["waiting"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.sla_paused_at is not None

        pause = SLAPause.unscoped.filter(ticket=ticket, resumed_at__isnull=True).first()
        assert pause is not None

    def test_status_from_waiting_resumes_pause(self, tenant, admin_user):
        """Moving from waiting to open closes the pause and clears sla_paused_at."""
        from apps.tickets.models import SLAPause
        from apps.tickets.services import transition_ticket_status

        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        set_current_tenant(tenant)
        transition_ticket_status(ticket, statuses["waiting"], admin_user)
        ticket.refresh_from_db()
        assert ticket.sla_paused_at is not None

        transition_ticket_status(ticket, statuses["open"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.sla_paused_at is None

        pause = SLAPause.unscoped.filter(ticket=ticket).first()
        assert pause is not None
        assert pause.resumed_at is not None

    def test_no_shift_when_no_deadlines(self, tenant, admin_user):
        """If ticket has no SLA deadlines, pause/resume is a no-op for deadlines."""
        from apps.tickets.models import SLAPause
        from apps.tickets.services import transition_ticket_status

        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        assert ticket.sla_first_response_due is None
        assert ticket.sla_resolution_due is None

        set_current_tenant(tenant)
        transition_ticket_status(ticket, statuses["waiting"], admin_user)
        transition_ticket_status(ticket, statuses["open"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.sla_first_response_due is None
        assert ticket.sla_resolution_due is None


# ===========================================================================
# 3. FIRST-RESPONSE BREACH DETECTION
# ===========================================================================


@pytest.mark.django_db(transaction=True)
class TestFirstResponseBreach:
    """Verify first-response tracking and breach flag/signal."""

    def test_on_time_response_no_breach(self, tenant, admin_user):
        """Response before deadline does not set breach flag."""
        from django.utils import timezone

        statuses = _make_statuses(tenant)
        now = timezone.now()

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["open"],
            created_by=admin_user,
            sla_first_response_due=now + datetime.timedelta(hours=4),
        )
        clear_current_tenant()

        from apps.tickets.services import record_first_response

        record_first_response(ticket, agent)

        ticket.refresh_from_db()
        assert ticket.first_responded_at is not None
        assert ticket.sla_response_breached is False

    def test_late_response_sets_breach(self, tenant, admin_user):
        """Response after deadline sets sla_response_breached = True."""
        from django.utils import timezone

        statuses = _make_statuses(tenant)
        now = timezone.now()

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)
        # Deadline was 2 hours ago
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["open"],
            created_by=admin_user,
            sla_first_response_due=now - datetime.timedelta(hours=2),
        )
        clear_current_tenant()

        from apps.tickets.services import record_first_response

        record_first_response(ticket, agent)

        ticket.refresh_from_db()
        assert ticket.first_responded_at is not None
        assert ticket.sla_response_breached is True

    def test_breach_fires_signal(self, tenant, admin_user):
        """The sla_first_response_breached signal fires on late response."""
        from django.utils import timezone

        statuses = _make_statuses(tenant)
        now = timezone.now()

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["open"],
            created_by=admin_user,
            sla_first_response_due=now - datetime.timedelta(hours=1),
        )
        clear_current_tenant()

        from apps.tickets.signals import sla_first_response_breached

        signal_received = []

        def handler(sender, instance, responded_at, due_at, **kwargs):
            signal_received.append({
                "ticket_id": instance.pk,
                "responded_at": responded_at,
                "due_at": due_at,
            })

        sla_first_response_breached.connect(handler)
        try:
            from apps.tickets.services import record_first_response

            record_first_response(ticket, agent)

            assert len(signal_received) == 1
            assert signal_received[0]["ticket_id"] == ticket.pk
        finally:
            sla_first_response_breached.disconnect(handler)

    def test_no_signal_when_no_deadline(self, tenant, admin_user):
        """No signal fires when there is no SLA deadline."""
        statuses = _make_statuses(tenant)

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["open"],
            created_by=admin_user,
            # No sla_first_response_due
        )
        clear_current_tenant()

        from apps.tickets.signals import sla_first_response_breached

        signal_received = []
        sla_first_response_breached.connect(
            lambda **kwargs: signal_received.append(True),
        )

        from apps.tickets.services import record_first_response

        record_first_response(ticket, agent)

        ticket.refresh_from_db()
        assert ticket.first_responded_at is not None
        assert ticket.sla_response_breached is False
        assert len(signal_received) == 0

    def test_creator_response_does_not_count(self, tenant, admin_user):
        """The ticket creator's own response does not count as first response."""
        from django.utils import timezone

        statuses = _make_statuses(tenant)

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["open"],
            created_by=admin_user,
            sla_first_response_due=timezone.now() - datetime.timedelta(hours=1),
        )
        clear_current_tenant()

        from apps.tickets.services import record_first_response

        record_first_response(ticket, admin_user)  # Creator responds

        ticket.refresh_from_db()
        assert ticket.first_responded_at is None
        assert ticket.sla_response_breached is False


# ===========================================================================
# 4. ESCALATION ATOMICITY
# ===========================================================================


@pytest.mark.django_db(transaction=True)
class TestEscalation:
    """Verify escalation is atomic and updates all required fields."""

    def test_escalate_with_assignee(self, tenant, admin_user):
        """Escalation reassigns and increments escalation_count."""
        from apps.tickets.services import escalate_ticket

        statuses = _make_statuses(tenant)

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
            assignee=admin_user,
        )

        result = escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Needs specialist attention",
            assignee=agent,
        )
        clear_current_tenant()

        assert result.escalation_count == 1
        assert result.escalated_at is not None
        assert result.assignee == agent

    def test_escalate_with_queue(self, tenant, admin_user):
        """Escalation can change the queue."""
        from apps.tickets.services import escalate_ticket

        statuses = _make_statuses(tenant)

        set_current_tenant(tenant)
        new_queue = QueueFactory(tenant=tenant, name="Tier 2 Support")
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )

        result = escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Tier 2 needed",
            queue=new_queue,
        )
        clear_current_tenant()

        assert result.queue == new_queue
        assert result.escalation_count == 1

    def test_escalate_creates_internal_comment(self, tenant, admin_user):
        """Escalation posts an internal comment with the reason."""
        from django.contrib.contenttypes.models import ContentType

        from apps.comments.models import Comment
        from apps.tickets.models import Ticket
        from apps.tickets.services import escalate_ticket

        statuses = _make_statuses(tenant)

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )

        escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="VIP customer complaint",
            assignee=agent,
        )
        clear_current_tenant()

        ticket_ct = ContentType.objects.get_for_model(Ticket)
        comment = Comment.unscoped.filter(
            content_type=ticket_ct,
            object_id=ticket.pk,
            is_internal=True,
        ).first()
        assert comment is not None
        assert "VIP customer complaint" in comment.body

    def test_escalate_records_timeline(self, tenant, admin_user):
        """Escalation creates an ESCALATED_MANUAL timeline entry."""
        from apps.tickets.models import TicketActivity
        from apps.tickets.services import escalate_ticket

        statuses = _make_statuses(tenant)

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )

        escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Escalation test",
            assignee=agent,
        )
        clear_current_tenant()

        activity = TicketActivity.unscoped.filter(
            ticket=ticket,
            event=TicketActivity.Event.ESCALATED_MANUAL,
        ).first()
        assert activity is not None
        assert "Escalation test" in activity.message

    def test_escalate_invalid_assignee_rolls_back(self, tenant, admin_user):
        """Escalation with non-member assignee raises and rolls back."""
        from apps.tickets.services import escalate_ticket

        statuses = _make_statuses(tenant)
        non_member = UserFactory()  # Not a member of this tenant

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )

        with pytest.raises(ValueError, match="not an active member"):
            escalate_ticket(
                ticket=ticket,
                actor=admin_user,
                reason="This should fail",
                assignee=non_member,
            )
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.escalation_count == 0
        assert ticket.escalated_at is None

    def test_escalate_recalculates_sla_on_policy_change(self, tenant, admin_user):
        """If the ticket priority has a different SLA policy, deadlines are reset."""
        from django.utils import timezone

        from apps.tickets.models import SLAPolicy
        from apps.tickets.services import escalate_ticket

        statuses = _make_statuses(tenant)
        now = timezone.now()

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)

        # Create an SLA policy for medium priority
        policy = SLAPolicy.objects.create(
            tenant=tenant,
            name="Medium SLA",
            priority="medium",
            first_response_minutes=120,
            resolution_minutes=480,
            business_hours_only=False,
            is_active=True,
        )

        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
            priority="medium",
            # No sla_policy attached yet
        )

        escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Escalation with SLA recalc",
            assignee=agent,
        )
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.sla_policy == policy
        assert ticket.sla_first_response_due is not None
        assert ticket.sla_resolution_due is not None

    def test_escalate_increments_count(self, tenant, admin_user):
        """Multiple escalations increment the count correctly."""
        from apps.tickets.services import escalate_ticket

        statuses = _make_statuses(tenant)

        agent1 = UserFactory()
        agent2 = UserFactory()
        MembershipFactory(
            user=agent1, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )
        MembershipFactory(
            user=agent2, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )

        escalate_ticket(ticket, admin_user, "First escalation", assignee=agent1)
        ticket.refresh_from_db()
        assert ticket.escalation_count == 1

        escalate_ticket(ticket, admin_user, "Second escalation", assignee=agent2)
        ticket.refresh_from_db()
        assert ticket.escalation_count == 2
        assert ticket.assignee == agent2

        clear_current_tenant()

    def test_escalate_api(self, admin_client, tenant, admin_user):
        """Test the escalate API action."""
        statuses = _make_statuses(tenant)

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )
        clear_current_tenant()

        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/escalate/",
            {
                "assignee": str(agent.pk),
                "reason": "API escalation test",
            },
            format="json",
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.data}"
        assert resp.data["escalation_count"] == 1
        assert str(resp.data["assignee"]) == str(agent.pk)

    def test_escalate_api_requires_reason(self, admin_client, tenant, admin_user):
        """Escalation without reason is rejected."""
        statuses = _make_statuses(tenant)

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )
        clear_current_tenant()

        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/escalate/",
            {"assignee": str(admin_user.pk)},
            format="json",
        )
        assert resp.status_code == 400

    def test_escalate_api_requires_target(self, admin_client, tenant, admin_user):
        """Escalation without assignee or queue is rejected."""
        statuses = _make_statuses(tenant)

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant,
            status=statuses["in-progress"],
            created_by=admin_user,
        )
        clear_current_tenant()

        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/escalate/",
            {"reason": "No target provided"},
            format="json",
        )
        assert resp.status_code == 400
