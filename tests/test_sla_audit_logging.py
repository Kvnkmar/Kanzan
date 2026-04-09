"""
Tests for SLA audit logging.

Validates that:
- Editing an SLAPolicy writes ActivityLog entries for all affected open tickets
- Initial ticket creation does NOT write an sla_updated entry
- Pause/resume SLA shift writes an sla_updated entry with triggered_by="pause_resume"
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
from apps.comments.models import ActivityLog
from apps.tickets.models import SLAPolicy, Ticket, TicketActivity


class TestPolicyEditPropagation:
    """Editing an SLAPolicy writes sla_updated entries for affected open tickets."""

    def test_policy_edit_writes_audit_for_open_tickets(self, tenant, admin_user):
        set_current_tenant(tenant)
        open_status = TicketStatusFactory(
            tenant=tenant, name="Open", slug="open", is_default=True,
        )
        closed_status = TicketStatusFactory(
            tenant=tenant, name="Closed", slug="closed", is_closed=True,
        )

        policy = SLAPolicy.objects.create(
            tenant=tenant, name="Medium SLA", priority="medium",
            first_response_minutes=60, resolution_minutes=240,
            business_hours_only=False, is_active=True,
        )

        # Create two open tickets and one closed ticket with this policy
        from apps.tickets.sla import add_business_minutes
        from django.utils import timezone
        now = timezone.now()

        open_ticket_1 = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
            priority="medium", sla_policy=policy,
            sla_first_response_due=add_business_minutes(now, 60, tenant),
            sla_resolution_due=add_business_minutes(now, 240, tenant),
        )
        open_ticket_2 = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
            priority="medium", sla_policy=policy,
            sla_first_response_due=add_business_minutes(now, 60, tenant),
            sla_resolution_due=add_business_minutes(now, 240, tenant),
        )
        closed_ticket = TicketFactory(
            tenant=tenant, status=closed_status, created_by=admin_user,
            priority="medium", sla_policy=policy,
            sla_first_response_due=add_business_minutes(now, 60, tenant),
            sla_resolution_due=add_business_minutes(now, 240, tenant),
        )

        # Clear any pre-existing activity logs
        ActivityLog.unscoped.filter(action=ActivityLog.Action.SLA_UPDATED).delete()

        # Edit the policy — triggers post_save signal
        policy.first_response_minutes = 30
        policy.resolution_minutes = 120
        policy.save()

        clear_current_tenant()

        # Should have sla_updated entries for the two open tickets
        sla_logs = ActivityLog.unscoped.filter(
            action=ActivityLog.Action.SLA_UPDATED,
        )
        affected_ticket_ids = set(sla_logs.values_list("object_id", flat=True))
        assert open_ticket_1.pk in affected_ticket_ids
        assert open_ticket_2.pk in affected_ticket_ids
        assert closed_ticket.pk not in affected_ticket_ids

        # Verify structure of one log entry
        log = sla_logs.filter(object_id=open_ticket_1.pk).first()
        assert log is not None
        assert log.changes["triggered_by"] == "policy_edit"
        assert "before" in log.changes["sla_first_response_due"]
        assert "after" in log.changes["sla_first_response_due"]


class TestInitialCreationNoAudit:
    """Initial ticket creation does NOT write an sla_updated entry."""

    def test_no_sla_updated_on_creation(self, tenant, admin_user, admin_client):
        set_current_tenant(tenant)
        TicketStatusFactory(
            tenant=tenant, name="Open", slug="open", is_default=True,
        )

        SLAPolicy.objects.create(
            tenant=tenant, name="Medium SLA", priority="medium",
            first_response_minutes=60, resolution_minutes=240,
            business_hours_only=False, is_active=True,
        )

        clear_current_tenant()

        # Create ticket via API (which calls initialize_sla)
        resp = admin_client.post("/api/v1/tickets/tickets/", {
            "subject": "Test ticket",
            "description": "Test description",
            "priority": "medium",
        })
        assert resp.status_code == 201

        ticket_id = resp.data["id"]

        # Verify SLA was initialized
        ticket = Ticket.unscoped.get(pk=ticket_id)
        assert ticket.sla_first_response_due is not None

        # No sla_updated ActivityLog should exist for this ticket
        sla_logs = ActivityLog.unscoped.filter(
            object_id=ticket_id,
            action=ActivityLog.Action.SLA_UPDATED,
        )
        assert sla_logs.count() == 0


class TestPauseResumeAudit:
    """Pause/resume SLA shift writes an sla_updated entry with triggered_by='pause_resume'."""

    def test_pause_resume_writes_sla_updated(self, tenant, admin_user):
        """Call log_sla_change directly to verify the pause_resume path."""
        set_current_tenant(tenant)
        open_status = TicketStatusFactory(
            tenant=tenant, name="Open", slug="open", is_default=True,
        )

        policy = SLAPolicy.objects.create(
            tenant=tenant, name="Medium SLA", priority="medium",
            first_response_minutes=120, resolution_minutes=480,
            business_hours_only=False, is_active=True,
        )

        import datetime
        from django.utils import timezone

        now = timezone.now()
        old_response_due = now + datetime.timedelta(hours=2)
        old_resolution_due = now + datetime.timedelta(hours=8)
        new_response_due = now + datetime.timedelta(hours=2, minutes=30)
        new_resolution_due = now + datetime.timedelta(hours=8, minutes=30)

        ticket = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
            priority="medium", sla_policy=policy,
            sla_first_response_due=new_response_due,
            sla_resolution_due=new_resolution_due,
        )

        from apps.tickets.services import log_sla_change
        log_sla_change(ticket, old_response_due, old_resolution_due, "pause_resume")
        clear_current_tenant()

        # Should have an sla_updated entry with triggered_by="pause_resume"
        sla_logs = ActivityLog.unscoped.filter(
            object_id=ticket.pk,
            action=ActivityLog.Action.SLA_UPDATED,
        )
        assert sla_logs.count() == 1

        log = sla_logs.first()
        assert log.changes["triggered_by"] == "pause_resume"
        assert "before" in log.changes["sla_first_response_due"]
        assert "after" in log.changes["sla_first_response_due"]
        # Verify old and new are different
        assert log.changes["sla_first_response_due"]["before"] != log.changes["sla_first_response_due"]["after"]
