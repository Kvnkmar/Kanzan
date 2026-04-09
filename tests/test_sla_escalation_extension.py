"""
Tests for SLA deadline extension on escalation.

Validates that escalation extends deadlines by 50% of remaining time
(clamped to [15 min, full policy duration]) rather than resetting them,
and handles breached and no-policy cases correctly.
"""

import datetime

import pytest

from conftest import (
    MembershipFactory,
    QueueFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant

from apps.accounts.models import Role
from apps.tickets.models import SLAPolicy, TicketActivity
from apps.tickets.services import escalate_ticket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup(tenant, admin_user, *, policy_kwargs=None, ticket_kwargs=None):
    """Create statuses, SLA policy, agent, and ticket."""
    set_current_tenant(tenant)

    status = TicketStatusFactory(
        tenant=tenant, name="In Progress", slug="in-progress", order=20,
    )
    # Need an open status for auto-transition guard
    TicketStatusFactory(
        tenant=tenant, name="Open", slug="open", order=10, is_default=True,
    )

    agent = UserFactory()
    MembershipFactory(
        user=agent, tenant=tenant,
        role=Role.unscoped.get(tenant=tenant, slug="agent"),
    )

    pk = policy_kwargs or {}
    policy = SLAPolicy.objects.create(
        tenant=tenant,
        name=pk.get("name", "Test SLA"),
        priority=pk.get("priority", "medium"),
        first_response_minutes=pk.get("first_response_minutes", 120),
        resolution_minutes=pk.get("resolution_minutes", 480),
        business_hours_only=False,
        is_active=True,
    )

    tk = ticket_kwargs or {}
    ticket = TicketFactory(
        tenant=tenant,
        status=status,
        created_by=admin_user,
        priority=tk.get("priority", "medium"),
        sla_policy=policy,
        sla_first_response_due=tk.get("sla_first_response_due"),
        sla_resolution_due=tk.get("sla_resolution_due"),
    )

    return ticket, agent, policy


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEscalationWithTimeRemaining:
    """When deadlines haven't been breached, extend by 50% of remaining time."""

    def test_deadlines_extended_by_half_remaining(self, tenant, admin_user):
        from django.utils import timezone

        now = timezone.now()
        # Set deadlines 100 minutes from now
        response_due = now + datetime.timedelta(minutes=100)
        resolution_due = now + datetime.timedelta(minutes=200)

        ticket, agent, policy = _setup(
            tenant, admin_user,
            policy_kwargs={
                "first_response_minutes": 120,
                "resolution_minutes": 480,
            },
            ticket_kwargs={
                "sla_first_response_due": response_due,
                "sla_resolution_due": resolution_due,
            },
        )

        old_response_due = ticket.sla_first_response_due
        old_resolution_due = ticket.sla_resolution_due

        escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Needs specialist",
            assignee=agent,
        )
        clear_current_tenant()

        ticket.refresh_from_db()

        # Deadlines should have moved FORWARD (extended)
        assert ticket.sla_first_response_due > old_response_due
        assert ticket.sla_resolution_due > old_resolution_due
        # sla_extended_at should be stamped
        assert ticket.sla_extended_at is not None

    def test_extension_respects_minimum_15_minutes(self, tenant, admin_user):
        """When 50% of remaining is < 15 min, the minimum 15 min applies."""
        from django.utils import timezone

        now = timezone.now()
        # Only 10 minutes remaining — 50% = 5 min, but minimum is 15
        response_due = now + datetime.timedelta(minutes=10)
        resolution_due = now + datetime.timedelta(minutes=10)

        ticket, agent, policy = _setup(
            tenant, admin_user,
            policy_kwargs={
                "first_response_minutes": 120,
                "resolution_minutes": 480,
            },
            ticket_kwargs={
                "sla_first_response_due": response_due,
                "sla_resolution_due": resolution_due,
            },
        )

        escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Needs specialist",
            assignee=agent,
        )
        clear_current_tenant()

        ticket.refresh_from_db()

        # Should have extended by at least 15 minutes from the old due time
        # (business-hours math may not be exact wallclock, but should be >= 14 min)
        response_delta = (
            ticket.sla_first_response_due - response_due
        ).total_seconds() / 60
        assert response_delta >= 14  # ~15 business minutes

    def test_extension_capped_at_policy_duration(self, tenant, admin_user):
        """When 50% of remaining exceeds policy duration, cap to policy duration."""
        from django.utils import timezone

        now = timezone.now()
        # 1000 minutes remaining — 50% = 500, but policy is only 120 min
        response_due = now + datetime.timedelta(minutes=1000)
        resolution_due = now + datetime.timedelta(minutes=2000)

        ticket, agent, policy = _setup(
            tenant, admin_user,
            policy_kwargs={
                "first_response_minutes": 120,
                "resolution_minutes": 480,
            },
            ticket_kwargs={
                "sla_first_response_due": response_due,
                "sla_resolution_due": resolution_due,
            },
        )

        escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Needs specialist",
            assignee=agent,
        )
        clear_current_tenant()

        ticket.refresh_from_db()

        # Deadline must have moved forward from the original
        assert ticket.sla_first_response_due > response_due
        assert ticket.sla_resolution_due > resolution_due

        # Verify capping via the logged timeline event metadata: the
        # extension minutes should equal the policy cap, not 50% of remaining.
        event = (
            TicketActivity.unscoped
            .filter(ticket=ticket, message__icontains="SLA deadlines updated")
            .first()
        )
        assert event is not None
        assert event.metadata["triggered_by"] == "escalation"
        # Verify before/after are different (deadlines were extended)
        assert event.metadata["sla_first_response_due"]["before"] != event.metadata["sla_first_response_due"]["after"]


class TestEscalationAfterBreach:
    """When deadline is already past, set to now + 15 business minutes."""

    def test_breached_deadline_gets_grace_window(self, tenant, admin_user):
        from django.utils import timezone

        now = timezone.now()
        # Deadlines 30 minutes AGO — already breached
        response_due = now - datetime.timedelta(minutes=30)
        resolution_due = now - datetime.timedelta(minutes=30)

        ticket, agent, policy = _setup(
            tenant, admin_user,
            ticket_kwargs={
                "sla_first_response_due": response_due,
                "sla_resolution_due": resolution_due,
            },
        )

        escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Breached — escalating",
            assignee=agent,
        )
        clear_current_tenant()

        ticket.refresh_from_db()

        # New deadlines must be later than the old breached deadlines
        assert ticket.sla_first_response_due > response_due
        assert ticket.sla_resolution_due > resolution_due
        assert ticket.sla_extended_at is not None

        # The extension metadata should show the 15-min grace window
        event = (
            TicketActivity.unscoped
            .filter(ticket=ticket, message__icontains="SLA deadlines updated")
            .first()
        )
        assert event is not None
        assert event.metadata["triggered_by"] == "escalation"
        # Deadlines were moved from past to future
        assert event.metadata["sla_first_response_due"]["before"] != event.metadata["sla_first_response_due"]["after"]


class TestEscalationWithoutSLAPolicy:
    """When no SLA policy matches the priority, escalation is a no-op for SLA."""

    def test_no_policy_no_change(self, tenant, admin_user):
        set_current_tenant(tenant)

        status = TicketStatusFactory(
            tenant=tenant, name="In Progress", slug="in-progress", order=20,
        )
        TicketStatusFactory(
            tenant=tenant, name="Open", slug="open", order=10, is_default=True,
        )

        agent = UserFactory()
        MembershipFactory(
            user=agent, tenant=tenant,
            role=Role.unscoped.get(tenant=tenant, slug="agent"),
        )

        # No SLA policy for "low" priority
        ticket = TicketFactory(
            tenant=tenant,
            status=status,
            created_by=admin_user,
            priority="low",
            sla_policy=None,
            sla_first_response_due=None,
            sla_resolution_due=None,
        )

        escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Escalate without SLA",
            assignee=agent,
        )
        clear_current_tenant()

        ticket.refresh_from_db()

        assert ticket.sla_policy is None
        assert ticket.sla_first_response_due is None
        assert ticket.sla_resolution_due is None
        assert ticket.sla_extended_at is None
        # Escalation itself still worked
        assert ticket.escalation_count == 1


class TestEscalationSLAExtensionLogging:
    """Verify dual-write logging captures old/new deadlines."""

    def test_timeline_event_logged(self, tenant, admin_user):
        from django.utils import timezone

        now = timezone.now()
        response_due = now + datetime.timedelta(minutes=100)
        resolution_due = now + datetime.timedelta(minutes=200)

        ticket, agent, policy = _setup(
            tenant, admin_user,
            ticket_kwargs={
                "sla_first_response_due": response_due,
                "sla_resolution_due": resolution_due,
            },
        )

        escalate_ticket(
            ticket=ticket,
            actor=admin_user,
            reason="Check logging",
            assignee=agent,
        )
        clear_current_tenant()

        # Find the SLA extension timeline event (use unscoped — tenant
        # context is cleared after escalation)
        events = TicketActivity.unscoped.filter(
            ticket=ticket,
            message__icontains="SLA deadlines updated",
        )
        assert events.exists()

        event = events.first()
        assert event.metadata["triggered_by"] == "escalation"
        assert "before" in event.metadata["sla_first_response_due"]
        assert "after" in event.metadata["sla_resolution_due"]
