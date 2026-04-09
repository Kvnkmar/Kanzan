"""
Ticket escalation and SLA tests.

Covers: SLA breach detection, escalation rules (priority bump, reassign),
escalation dedup, SLA pause/resume, pause prevents false breach, manual
escalation via status transitions, and invalid transition blocking.
"""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from freezegun import freeze_time
from rest_framework.test import APIClient

from apps.accounts.models import Role, TenantMembership
from apps.billing.models import Plan, Subscription, UsageTracker
from apps.comments.models import ActivityLog
from apps.tenants.models import Tenant
from apps.tickets.models import (
    EscalationRule,
    SLAPause,
    SLAPolicy,
    Ticket,
    TicketActivity,
    TicketStatus,
)
from apps.tickets.services import (
    change_ticket_status,
    transition_ticket_status,
    validate_status_transition,
)
from apps.tickets.tasks import check_sla_breaches
from main.context import clear_current_tenant, set_current_tenant

User = get_user_model()


class EscalationTestBase(TestCase):
    """Shared setUp for escalation tests."""

    @classmethod
    def setUpTestData(cls):
        # --- Tenant ---
        cls.tenant = Tenant.objects.create(name="Escalation Tenant", slug="esc-t")

        # --- Roles ---
        cls.role_admin = Role.unscoped.get(tenant=cls.tenant, slug="admin")
        cls.role_agent = Role.unscoped.get(tenant=cls.tenant, slug="agent")

        # --- Users ---
        cls.admin_user = User.objects.create_user(
            email="admin@esc-t.test", password="testpass123",
            first_name="Admin", last_name="Esc",
        )
        cls.agent_user = User.objects.create_user(
            email="agent@esc-t.test", password="testpass123",
            first_name="Agent", last_name="Esc",
        )
        cls.escalation_target = User.objects.create_user(
            email="target@esc-t.test", password="testpass123",
            first_name="Target", last_name="Esc",
        )

        # --- Memberships ---
        TenantMembership.objects.create(
            user=cls.admin_user, tenant=cls.tenant, role=cls.role_admin,
        )
        TenantMembership.objects.create(
            user=cls.agent_user, tenant=cls.tenant, role=cls.role_agent,
        )
        TenantMembership.objects.create(
            user=cls.escalation_target, tenant=cls.tenant, role=cls.role_agent,
        )

        # --- Ticket Statuses ---
        cls.status_open = TicketStatus(
            name="Open", slug="open", order=0, is_default=True, tenant=cls.tenant,
        )
        cls.status_open.save()
        cls.status_in_progress = TicketStatus(
            name="In Progress", slug="in-progress", order=1, tenant=cls.tenant,
        )
        cls.status_in_progress.save()
        cls.status_waiting = TicketStatus(
            name="Waiting", slug="waiting", order=2, pauses_sla=True,
            tenant=cls.tenant,
        )
        cls.status_waiting.save()
        cls.status_resolved = TicketStatus(
            name="Resolved", slug="resolved", order=3, tenant=cls.tenant,
        )
        cls.status_resolved.save()
        cls.status_closed = TicketStatus(
            name="Closed", slug="closed", order=4, is_closed=True, tenant=cls.tenant,
        )
        cls.status_closed.save()

        # --- Plan ---
        cls.free_plan = Plan.objects.create(
            tier=Plan.Tier.FREE, name="Free",
            stripe_product_id="prod_free_esc",
            max_users=10, max_contacts=500,
            max_tickets_per_month=1000, max_storage_mb=1024,
        )
        Subscription.objects.create(
            tenant=cls.tenant, plan=cls.free_plan,
            status=Subscription.Status.ACTIVE,
            stripe_subscription_id="sub_esc_t",
            current_period_start=timezone.make_aware(timezone.datetime(2026, 1, 1)),
            current_period_end=timezone.make_aware(timezone.datetime(2026, 12, 31)),
        )
        UsageTracker.objects.create(
            tenant=cls.tenant, period_start=date(2026, 1, 1),
        )

    def setUp(self):
        clear_current_tenant()

    def tearDown(self):
        clear_current_tenant()

    def _make_ticket(self, priority="high", **kwargs):
        """Create a ticket directly in the DB with tenant context."""
        set_current_tenant(self.tenant)
        ticket = Ticket.objects.create(
            subject=kwargs.pop("subject", "Escalation test ticket"),
            description=kwargs.pop("description", "Test"),
            status=kwargs.pop("status", self.status_open),
            priority=priority,
            created_by=kwargs.pop("created_by", self.agent_user),
            assignee=kwargs.pop("assignee", self.agent_user),
            **kwargs,
        )
        clear_current_tenant()
        return ticket

    def _make_sla_policy(self, priority="high", first_response_minutes=1,
                         resolution_minutes=2):
        """Create an SLAPolicy."""
        set_current_tenant(self.tenant)
        policy = SLAPolicy.objects.create(
            name=f"SLA {priority}",
            priority=priority,
            first_response_minutes=first_response_minutes,
            resolution_minutes=resolution_minutes,
            business_hours_only=False,
            is_active=True,
        )
        clear_current_tenant()
        return policy


# =========================================================================
# 1. SLA breach detection
# =========================================================================


class TestSLABreachDetection(EscalationTestBase):
    """SLA breach flags are set after time exceeds policy thresholds."""

    def test_response_and_resolution_breach(self):
        policy = self._make_sla_policy(
            priority="high",
            first_response_minutes=1,
            resolution_minutes=2,
        )

        # Create ticket at a known time
        with freeze_time("2026-04-05 10:00:00"):
            ticket = self._make_ticket(priority="high")
            # Manually set SLA fields
            from apps.tickets.services import initialize_sla

            set_current_tenant(self.tenant)
            initialize_sla(ticket)
            clear_current_tenant()
            ticket.refresh_from_db()

        # Fast-forward past both SLA deadlines
        with freeze_time("2026-04-05 10:05:00"):
            check_sla_breaches()

        ticket.refresh_from_db()
        self.assertTrue(
            ticket.sla_response_breached,
            "Response SLA should be breached after 5 minutes (threshold: 1 min)",
        )
        self.assertTrue(
            ticket.sla_resolution_breached,
            "Resolution SLA should be breached after 5 minutes (threshold: 2 min)",
        )

    def test_breach_flags_set_before_notifications(self):
        """Breach flags are persisted to DB before notification dispatch."""
        policy = self._make_sla_policy(priority="urgent", first_response_minutes=1,
                                       resolution_minutes=2)

        with freeze_time("2026-04-05 10:00:00"):
            ticket = self._make_ticket(priority="urgent")
            set_current_tenant(self.tenant)
            from apps.tickets.services import initialize_sla
            initialize_sla(ticket)
            clear_current_tenant()

        # Patch notification sending to capture when flags are set
        flags_at_notification = {}

        original_send = None
        try:
            from apps.notifications import services as notif_svc
            original_send = notif_svc.send_notification

            def spy_send(*args, **kwargs):
                # Read the ticket state from DB at notification time
                t = Ticket.unscoped.get(pk=ticket.pk)
                flags_at_notification["response"] = t.sla_response_breached
                flags_at_notification["resolution"] = t.sla_resolution_breached
                return original_send(*args, **kwargs)

            notif_svc.send_notification = spy_send

            with freeze_time("2026-04-05 10:05:00"):
                check_sla_breaches()

            # Flags should have been True when notification was sent
            self.assertTrue(
                flags_at_notification.get("response", False),
                "Response breach flag should be set before notification fires",
            )
        finally:
            if original_send:
                notif_svc.send_notification = original_send


# =========================================================================
# 2. Escalation rule — priority bump
# =========================================================================


class TestEscalationPriorityBump(EscalationTestBase):
    """Escalation rule bumps priority up one level."""

    def test_priority_bump_on_response_breach(self):
        policy = self._make_sla_policy(priority="medium", first_response_minutes=1,
                                       resolution_minutes=60)

        set_current_tenant(self.tenant)
        rule = EscalationRule.objects.create(
            sla_policy=policy,
            trigger=EscalationRule.Trigger.RESPONSE_BREACH,
            threshold_minutes=0,
            action=EscalationRule.Action.CHANGE_PRIORITY,
            order=1,
        )
        clear_current_tenant()

        with freeze_time("2026-04-05 10:00:00"):
            ticket = self._make_ticket(priority="medium")
            set_current_tenant(self.tenant)
            from apps.tickets.services import initialize_sla
            initialize_sla(ticket)
            clear_current_tenant()

        with freeze_time("2026-04-05 10:05:00"):
            check_sla_breaches()

        ticket.refresh_from_db()
        self.assertEqual(
            ticket.priority, "high",
            f"Expected priority bumped to 'high', got '{ticket.priority}'",
        )

        # Check TicketActivity has an escalated event
        escalated = TicketActivity.unscoped.filter(
            ticket=ticket, event=TicketActivity.Event.ESCALATED,
        )
        self.assertTrue(
            escalated.exists(),
            "Expected an ESCALATED TicketActivity event for priority bump",
        )


# =========================================================================
# 3. Escalation rule — reassign
# =========================================================================


class TestEscalationReassign(EscalationTestBase):
    """Escalation rule reassigns ticket to a target user."""

    def test_reassign_on_response_breach(self):
        policy = self._make_sla_policy(priority="high", first_response_minutes=1,
                                       resolution_minutes=60)

        set_current_tenant(self.tenant)
        rule = EscalationRule.objects.create(
            sla_policy=policy,
            trigger=EscalationRule.Trigger.RESPONSE_BREACH,
            threshold_minutes=0,
            action=EscalationRule.Action.ASSIGN,
            target_user=self.escalation_target,
            order=1,
        )
        clear_current_tenant()

        with freeze_time("2026-04-05 10:00:00"):
            ticket = self._make_ticket(priority="high", assignee=self.agent_user)
            set_current_tenant(self.tenant)
            from apps.tickets.services import initialize_sla
            initialize_sla(ticket)
            clear_current_tenant()

        with freeze_time("2026-04-05 10:05:00"):
            check_sla_breaches()

        ticket.refresh_from_db()
        self.assertEqual(
            ticket.assignee_id, self.escalation_target.pk,
            f"Expected reassignment to target user, but assignee is {ticket.assignee}",
        )

        # Check TicketActivity logs the reassignment
        escalated = TicketActivity.unscoped.filter(
            ticket=ticket, event=TicketActivity.Event.ESCALATED,
        )
        self.assertTrue(escalated.exists(), "Expected ESCALATED activity for reassignment")
        # Verify the metadata mentions the rule
        meta = escalated.first().metadata
        self.assertEqual(meta.get("escalation_rule_id"), str(rule.id))


# =========================================================================
# 4. Escalation dedup
# =========================================================================


class TestEscalationDedup(EscalationTestBase):
    """Same escalation rule fires only once even if check_sla_breaches
    is called multiple times."""

    def test_escalation_fires_only_once(self):
        policy = self._make_sla_policy(priority="high", first_response_minutes=1,
                                       resolution_minutes=60)

        set_current_tenant(self.tenant)
        rule = EscalationRule.objects.create(
            sla_policy=policy,
            trigger=EscalationRule.Trigger.RESPONSE_BREACH,
            threshold_minutes=0,
            action=EscalationRule.Action.CHANGE_PRIORITY,
            order=1,
        )
        clear_current_tenant()

        with freeze_time("2026-04-05 10:00:00"):
            ticket = self._make_ticket(priority="high")
            set_current_tenant(self.tenant)
            from apps.tickets.services import initialize_sla
            initialize_sla(ticket)
            clear_current_tenant()

        # Run breach check twice
        with freeze_time("2026-04-05 10:05:00"):
            check_sla_breaches()
            check_sla_breaches()

        ticket.refresh_from_db()

        # Count escalation activities for this specific rule
        escalation_activities = TicketActivity.unscoped.filter(
            ticket=ticket,
            event=TicketActivity.Event.ESCALATED,
            metadata__escalation_rule_id=str(rule.id),
        )
        # There may be 1 rule-triggered escalation + 1 breach notification escalation
        # The rule should only fire once
        self.assertEqual(
            escalation_activities.count(), 1,
            f"Rule should fire only once, fired {escalation_activities.count()} times",
        )

        # Priority should only be bumped once (high → urgent, NOT beyond)
        self.assertEqual(
            ticket.priority, "urgent",
            f"Expected 'urgent' (one bump from 'high'), got '{ticket.priority}'",
        )


# =========================================================================
# 5. SLA pause — waiting status
# =========================================================================


class TestSLAPauseWaitingStatus(EscalationTestBase):
    """SLA clock pauses when ticket enters 'waiting' status."""

    def test_sla_pause_on_waiting(self):
        policy = self._make_sla_policy(priority="high", first_response_minutes=60,
                                       resolution_minutes=480)

        # Use a weekday (Monday 2026-04-06) during business hours so
        # elapsed_business_minutes counts the pause correctly.
        with freeze_time("2026-04-06 10:00:00"):
            ticket = self._make_ticket(priority="high", status=self.status_in_progress)
            set_current_tenant(self.tenant)
            from apps.tickets.services import initialize_sla
            initialize_sla(ticket)
            clear_current_tenant()
            ticket.refresh_from_db()

            original_response_due = ticket.sla_first_response_due
            original_resolution_due = ticket.sla_resolution_due

        # Transition to 'waiting' — SLA should pause
        with freeze_time("2026-04-06 10:10:00"):
            set_current_tenant(self.tenant)
            change_ticket_status(ticket, self.status_waiting, actor=self.admin_user)
            clear_current_tenant()

        # Verify SLAPause record was created
        pauses = SLAPause.unscoped.filter(ticket=ticket, resumed_at__isnull=True)
        self.assertTrue(pauses.exists(), "SLAPause record should exist")

        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.sla_paused_at, "sla_paused_at should be set")

        # Fast-forward 30 minutes while paused
        with freeze_time("2026-04-06 10:40:00"):
            # Transition back to 'in-progress'
            set_current_tenant(self.tenant)
            change_ticket_status(ticket, self.status_in_progress, actor=self.admin_user)
            clear_current_tenant()

        # Verify SLAPause was closed
        pause = SLAPause.unscoped.filter(ticket=ticket).first()
        self.assertIsNotNone(pause.resumed_at, "SLAPause.resumed_at should be set")

        # Verify SLA deadlines were shifted forward
        ticket.refresh_from_db()
        if original_response_due and ticket.sla_first_response_due:
            shift = ticket.sla_first_response_due - original_response_due
            # Should have shifted by approximately 30 minutes
            self.assertGreaterEqual(
                shift.total_seconds(), 25 * 60,
                f"SLA deadline should shift forward by ~30 min, shifted by {shift}",
            )


# =========================================================================
# 6. SLA pause prevents false breach
# =========================================================================


class TestSLAPausePreventsBreech(EscalationTestBase):
    """A ticket paused the entire time should NOT breach SLA."""

    def test_no_breach_while_paused(self):
        policy = self._make_sla_policy(priority="high", first_response_minutes=10,
                                       resolution_minutes=20)

        with freeze_time("2026-04-05 10:00:00"):
            ticket = self._make_ticket(priority="high")
            set_current_tenant(self.tenant)
            from apps.tickets.services import initialize_sla
            initialize_sla(ticket)
            clear_current_tenant()

        # Immediately move to 'waiting' (SLA paused)
        with freeze_time("2026-04-05 10:00:30"):
            set_current_tenant(self.tenant)
            change_ticket_status(ticket, self.status_waiting, actor=self.admin_user)
            clear_current_tenant()

        # Fast-forward 60 minutes while still paused
        with freeze_time("2026-04-05 11:00:30"):
            check_sla_breaches()

        ticket.refresh_from_db()
        self.assertFalse(
            ticket.sla_response_breached,
            "Response SLA should NOT be breached while ticket is paused",
        )
        self.assertFalse(
            ticket.sla_resolution_breached,
            "Resolution SLA should NOT be breached while ticket is paused",
        )


# =========================================================================
# 7. Manual escalation via status transitions
# =========================================================================


class TestManualStatusTransitions(EscalationTestBase):
    """Valid status transitions succeed and log TicketActivity."""

    def test_valid_transition_chain(self):
        """open → in-progress → waiting → in-progress — all valid."""
        ticket = self._make_ticket(status=self.status_open)

        set_current_tenant(self.tenant)

        # open → in-progress
        transition_ticket_status(ticket, self.status_in_progress, self.admin_user)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status.slug, "in-progress")

        # in-progress → waiting
        transition_ticket_status(ticket, self.status_waiting, self.admin_user)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status.slug, "waiting")

        # waiting → in-progress
        transition_ticket_status(ticket, self.status_in_progress, self.admin_user)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status.slug, "in-progress")

        clear_current_tenant()

        # Check that TicketActivity has status_changed events for each transition
        status_changes = TicketActivity.unscoped.filter(
            ticket=ticket,
            event=TicketActivity.Event.STATUS_CHANGED,
        )
        self.assertGreaterEqual(
            status_changes.count(), 3,
            f"Expected at least 3 status_changed events, got {status_changes.count()}",
        )


# =========================================================================
# 8. Invalid transition blocked
# =========================================================================


class TestInvalidTransitionBlocked(EscalationTestBase):
    """Transitioning from 'closed' to any other status is blocked."""

    def test_closed_to_open_blocked(self):
        """Closed is terminal — no transitions allowed."""
        ticket = self._make_ticket(status=self.status_closed)

        set_current_tenant(self.tenant)
        with self.assertRaises(ValidationError):
            validate_status_transition(ticket, self.status_open)
        clear_current_tenant()

        # Verify ticket status unchanged
        ticket.refresh_from_db()
        self.assertEqual(ticket.status.slug, "closed")

    def test_closed_to_in_progress_blocked(self):
        ticket = self._make_ticket(status=self.status_closed)

        set_current_tenant(self.tenant)
        with self.assertRaises(ValidationError):
            validate_status_transition(ticket, self.status_in_progress)
        clear_current_tenant()

    def test_closed_to_waiting_blocked(self):
        ticket = self._make_ticket(status=self.status_closed)

        set_current_tenant(self.tenant)
        with self.assertRaises(ValidationError):
            validate_status_transition(ticket, self.status_waiting)
        clear_current_tenant()

    def test_closed_to_resolved_blocked(self):
        ticket = self._make_ticket(status=self.status_closed)

        set_current_tenant(self.tenant)
        with self.assertRaises(ValidationError):
            validate_status_transition(ticket, self.status_resolved)
        clear_current_tenant()

    def test_change_status_api_returns_400_for_closed(self):
        """The change-status API endpoint returns 400 for invalid transitions."""
        ticket = self._make_ticket(status=self.status_closed)

        client = APIClient()
        client.force_authenticate(user=self.admin_user)
        client.defaults["SERVER_NAME"] = "esc-t.localhost"

        resp = client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/change-status/",
            data={"status": str(self.status_open.pk)},
            format="json",
        )
        self.assertEqual(
            resp.status_code, 400,
            f"Expected 400 for closed→open transition, got {resp.status_code}: {resp.data}",
        )

        # Verify status unchanged in DB
        ticket.refresh_from_db()
        self.assertEqual(ticket.status.slug, "closed")
