"""
Tests for SLA pause/resume logic.

Covers:
- Pause created when ticket status changes to pauses_sla=True
- Resume when status changes away from pausing status
- Resume on inbound customer reply
- No duplicate pauses
- Timeline activity events logged for pause/resume
"""

import pytest
from django.utils import timezone

from apps.tickets.models import SLAPause, TicketActivity
from apps.tickets.signals import _resume_sla_pause
from conftest import (
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


@pytest.mark.django_db
class TestSLAPauseOnStatusChange:
    def test_pause_created_on_pausing_status(self, tenant):
        """Changing to a pauses_sla=True status creates an open SLAPause."""
        set_current_tenant(tenant)
        open_status = TicketStatusFactory(
            tenant=tenant, is_default=True, name="Open", slug="open-p"
        )
        waiting_status = TicketStatusFactory(
            tenant=tenant, name="Waiting", slug="waiting-p", pauses_sla=True
        )
        user = UserFactory()
        ticket = TicketFactory(
            tenant=tenant, status=open_status, created_by=user
        )

        # Change status to waiting (pauses SLA)
        ticket.status = waiting_status
        ticket.save()

        pauses = SLAPause.unscoped.filter(ticket=ticket)
        assert pauses.count() == 1
        pause = pauses.first()
        assert pause.resumed_at is None
        assert pause.reason == "waiting_on_customer"

        # Check timeline event
        activity = TicketActivity.unscoped.filter(
            ticket=ticket, event=TicketActivity.Event.SLA_PAUSED
        )
        assert activity.exists()
        clear_current_tenant()

    def test_resume_on_leaving_pausing_status(self, tenant):
        """Changing from pauses_sla=True to False closes the open SLAPause."""
        set_current_tenant(tenant)
        open_status = TicketStatusFactory(
            tenant=tenant, is_default=True, name="Open", slug="open-r"
        )
        waiting_status = TicketStatusFactory(
            tenant=tenant, name="Waiting", slug="waiting-r", pauses_sla=True
        )
        user = UserFactory()
        ticket = TicketFactory(
            tenant=tenant, status=waiting_status, created_by=user
        )
        # Create an open pause manually (simulating previous status change)
        pause = SLAPause.unscoped.create(
            tenant=tenant,
            ticket=ticket,
            paused_at=timezone.now(),
            reason="waiting_on_customer",
        )

        # Change status away from waiting
        ticket.status = open_status
        ticket.save()

        pause.refresh_from_db()
        assert pause.resumed_at is not None

        # Check timeline event
        activity = TicketActivity.unscoped.filter(
            ticket=ticket, event=TicketActivity.Event.SLA_RESUMED
        )
        assert activity.exists()
        clear_current_tenant()

    def test_no_pause_when_both_statuses_pause(self, tenant):
        """No new pause/resume when both old and new status pause SLA."""
        set_current_tenant(tenant)
        waiting1 = TicketStatusFactory(
            tenant=tenant, name="Waiting 1", slug="w1", pauses_sla=True
        )
        waiting2 = TicketStatusFactory(
            tenant=tenant, name="Waiting 2", slug="w2", pauses_sla=True
        )
        user = UserFactory()
        ticket = TicketFactory(
            tenant=tenant, status=waiting1, created_by=user
        )
        initial_pause_count = SLAPause.unscoped.filter(ticket=ticket).count()

        ticket.status = waiting2
        ticket.save()

        # No new pause should be created
        assert SLAPause.unscoped.filter(ticket=ticket).count() == initial_pause_count
        clear_current_tenant()

    def test_no_action_on_non_status_change(self, tenant):
        """Saving ticket without status change creates no pause."""
        set_current_tenant(tenant)
        status = TicketStatusFactory(
            tenant=tenant, is_default=True, name="Open", slug="open-ns"
        )
        user = UserFactory()
        ticket = TicketFactory(
            tenant=tenant, status=status, created_by=user
        )

        # Update something other than status
        ticket.subject = "Updated subject"
        ticket.save()

        assert SLAPause.unscoped.filter(ticket=ticket).count() == 0
        clear_current_tenant()


@pytest.mark.django_db
class TestSLAResumeOnReply:
    def test_resume_sla_pause_closes_open_pause(self, tenant):
        """_resume_sla_pause closes the most recent open pause."""
        set_current_tenant(tenant)
        status = TicketStatusFactory(
            tenant=tenant, is_default=True, name="Open", slug="open-rr"
        )
        user = UserFactory()
        ticket = TicketFactory(
            tenant=tenant, status=status, created_by=user
        )
        pause = SLAPause.unscoped.create(
            tenant=tenant,
            ticket=ticket,
            paused_at=timezone.now(),
            reason="waiting_on_customer",
        )

        _resume_sla_pause(ticket, reason="customer_reply")

        pause.refresh_from_db()
        assert pause.resumed_at is not None

        activity = TicketActivity.unscoped.filter(
            ticket=ticket, event=TicketActivity.Event.SLA_RESUMED
        )
        assert activity.exists()
        assert "customer_reply" in activity.first().metadata.get("reason", "")
        clear_current_tenant()

    def test_resume_noop_when_no_open_pause(self, tenant):
        """_resume_sla_pause does nothing when there's no open pause."""
        set_current_tenant(tenant)
        status = TicketStatusFactory(
            tenant=tenant, is_default=True, name="Open", slug="open-nn"
        )
        user = UserFactory()
        ticket = TicketFactory(
            tenant=tenant, status=status, created_by=user
        )

        # Should not raise or create any activity
        _resume_sla_pause(ticket, reason="customer_reply")

        assert TicketActivity.unscoped.filter(
            ticket=ticket, event=TicketActivity.Event.SLA_RESUMED
        ).count() == 0
        clear_current_tenant()
