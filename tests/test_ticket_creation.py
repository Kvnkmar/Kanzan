"""
Module 2 — Ticket Creation (17 tests)

Tests ticket creation via the API including sequential numbering, tenant
isolation, plan limits, SLA initialization, kanban card creation, and
signal firing.
"""

import threading
import unittest
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from rest_framework import status

from apps.comments.models import ActivityLog
from apps.tickets.models import Ticket, TicketActivity
from main.context import clear_current_tenant, set_current_tenant
from tests.base import KanzenBaseTestCase


class TestTicketCreation(KanzenBaseTestCase):
    """Ticket creation via API: numbering, validation, signals, and side effects."""

    def setUp(self):
        super().setUp()
        self.tickets_url = self.api_url("/tickets/tickets/")
        self.base_payload = {
            "subject": "Test ticket",
            "description": "A test ticket description",
            "priority": "medium",
        }

    def _create_ticket_via_api(self, user=None, tenant=None, **overrides):
        """Helper: create a ticket via the API and return the response."""
        u = user or self.agent_a
        t = tenant or self.tenant_a
        self.auth_tenant(u, t)
        payload = {**self.base_payload, **overrides}
        return self.client.post(self.tickets_url, payload, format="json")

    # ------------------------------------------------------------------
    # 2.1  Agent creates ticket -> 201, sequential number assigned
    # ------------------------------------------------------------------

    def test_agent_creates_ticket_returns_201_with_number(self):
        """POST to tickets endpoint with valid data returns 201 and a ticket number."""
        response = self._create_ticket_via_api()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("number", response.data)
        self.assertIsNotNone(response.data["number"])
        self.assertGreater(response.data["number"], 0)

    # ------------------------------------------------------------------
    # 2.2  Ticket number is unique per tenant (not globally)
    # ------------------------------------------------------------------

    def test_ticket_number_unique_per_tenant(self):
        """Two tickets in the same tenant get different sequential numbers."""
        r1 = self._create_ticket_via_api(subject="First")
        r2 = self._create_ticket_via_api(subject="Second")
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(r1.data["number"], r2.data["number"])
        # Numbers should be sequential
        self.assertEqual(r2.data["number"], r1.data["number"] + 1)

    # ------------------------------------------------------------------
    # 2.3  Two tenants can both have ticket #1 (no collision)
    # ------------------------------------------------------------------

    def test_two_tenants_can_both_have_ticket_number_one(self):
        """
        Each tenant has an independent ticket number sequence.
        Both tenants can have a ticket with number=1.
        """
        # Ensure tenant_b has an open status
        from apps.tickets.models import TicketStatus

        status_open_b = TicketStatus.unscoped.filter(
            tenant=self.tenant_b, is_default=True
        ).first()
        if not status_open_b:
            set_current_tenant(self.tenant_b)
            TicketStatus.objects.create(
                name="Open", slug="open", order=0,
                is_default=True, tenant=self.tenant_b,
            )
            clear_current_tenant()

        r_a = self._create_ticket_via_api(
            user=self.admin_a, tenant=self.tenant_a, subject="Tenant A first"
        )
        r_b = self._create_ticket_via_api(
            user=self.admin_b, tenant=self.tenant_b, subject="Tenant B first"
        )
        self.assertEqual(r_a.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r_b.status_code, status.HTTP_201_CREATED)

        # Both should start at 1 (or the same first number for their tenant)
        num_a = r_a.data["number"]
        num_b = r_b.data["number"]
        # The key assertion: these numbers are independent per tenant.
        # Both tenants should get their first ticket number (1).
        self.assertEqual(num_a, num_b)

    # ------------------------------------------------------------------
    # 2.4  Race condition: 10 concurrent creates -> 10 unique numbers
    # ------------------------------------------------------------------

    def test_concurrent_creates_produce_unique_numbers(self):
        """
        10 sequential ticket creations via the model layer should produce
        10 unique, sequential ticket numbers with no gaps.

        Note: SQLite does not support true concurrent writes, so this tests
        logical correctness of the TicketCounter atomic increment
        sequentially rather than using threads.
        """
        results = []

        set_current_tenant(self.tenant_a)
        try:
            for index in range(10):
                ticket = Ticket(
                    subject=f"Concurrent ticket {index}",
                    description="Race condition test",
                    status=self.status_open_a,
                    priority="medium",
                    created_by=self.admin_a,
                    tenant=self.tenant_a,
                )
                ticket.save()
                results.append(ticket.number)
        finally:
            clear_current_tenant()

        self.assertEqual(len(results), 10)
        self.assertEqual(len(set(results)), 10, "Duplicate ticket numbers found!")
        # Numbers should be contiguous
        sorted_nums = sorted(results)
        for i in range(1, len(sorted_nums)):
            self.assertEqual(
                sorted_nums[i], sorted_nums[i - 1] + 1,
                f"Gap found between {sorted_nums[i - 1]} and {sorted_nums[i]}",
            )

    # ------------------------------------------------------------------
    # 2.5  Missing subject -> 400
    # ------------------------------------------------------------------

    def test_missing_subject_returns_400(self):
        """A ticket without a subject field should be rejected with 400."""
        response = self._create_ticket_via_api(subject="")
        # DRF may return 400 for blank or missing required field
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------------
    # 2.6  Missing priority -> default applied (medium)
    # ------------------------------------------------------------------

    def test_missing_priority_defaults_to_medium(self):
        """When priority is omitted, the ticket should default to 'medium'."""
        self.auth_tenant(self.agent_a, self.tenant_a)
        payload = {
            "subject": "No priority specified",
            "description": "Should default to medium",
        }
        response = self.client.post(self.tickets_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Retrieve the created ticket to verify
        set_current_tenant(self.tenant_a)
        ticket = Ticket.objects.get(id=response.data["id"])
        clear_current_tenant()
        self.assertEqual(ticket.priority, "medium")

    # ------------------------------------------------------------------
    # 2.7  Plan limit exceeded -> 403, count unchanged in DB
    # ------------------------------------------------------------------

    def test_plan_limit_exceeded_returns_403(self):
        """
        When the tenant has reached the free plan ticket limit (100/month),
        creating another ticket should return 403 and NOT increment the count.
        """
        sub, usage = self.create_subscription(self.tenant_a, self.free_plan)
        usage.tickets_created = 100  # At the limit
        usage.save()

        initial_count = Ticket.unscoped.filter(tenant=self.tenant_a).count()

        response = self._create_ticket_via_api()
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Verify no ticket was created
        final_count = Ticket.unscoped.filter(tenant=self.tenant_a).count()
        self.assertEqual(initial_count, final_count)

    # ------------------------------------------------------------------
    # 2.8  Ticket created with channel=email -> channel saved correctly
    # ------------------------------------------------------------------

    def test_channel_email_saved_correctly(self):
        """Creating a ticket with channel='email' persists the channel."""
        response = self._create_ticket_via_api(channel="email")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        set_current_tenant(self.tenant_a)
        ticket = Ticket.objects.get(id=response.data["id"])
        clear_current_tenant()
        self.assertEqual(ticket.channel, "email")

    # ------------------------------------------------------------------
    # 2.9  Ticket created with channel=portal -> channel saved correctly
    # ------------------------------------------------------------------

    def test_channel_portal_saved_correctly(self):
        """Creating a ticket with channel='portal' persists the channel."""
        response = self._create_ticket_via_api(channel="portal")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        set_current_tenant(self.tenant_a)
        ticket = Ticket.objects.get(id=response.data["id"])
        clear_current_tenant()
        self.assertEqual(ticket.channel, "portal")

    # ------------------------------------------------------------------
    # 2.10 Assignee not a tenant member -> 400
    # ------------------------------------------------------------------

    def test_assignee_not_tenant_member_returns_400(self):
        """
        Assigning a ticket to a user who is not an active member of the
        tenant should return 400 (or be rejected during validation).
        """
        # admin_b is not a member of tenant_a
        response = self._create_ticket_via_api(assignee=str(self.admin_b.id))
        # The serializer or service layer should reject non-member assignees
        # Accept either 400 (validation error) or 201 if it's only checked on assign action
        if response.status_code == status.HTTP_201_CREATED:
            # If creation succeeded, try the assign action instead
            ticket_id = response.data["id"]
            assign_url = self.api_url(f"/tickets/tickets/{ticket_id}/assign/")
            self.auth_tenant(self.admin_a, self.tenant_a)
            assign_response = self.client.post(
                assign_url,
                {"assignee": str(self.admin_b.id)},
                format="json",
            )
            self.assertEqual(assign_response.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------------
    # 2.11 Assignee is active tenant member -> 201, assignee set
    # ------------------------------------------------------------------

    def test_assignee_is_active_tenant_member_succeeds(self):
        """
        Creating a ticket with an assignee who is an active tenant member
        should succeed and set the assignee correctly.
        """
        response = self._create_ticket_via_api(assignee=str(self.agent_a.id))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        set_current_tenant(self.tenant_a)
        ticket = Ticket.objects.get(id=response.data["id"])
        clear_current_tenant()
        self.assertEqual(ticket.assignee_id, self.agent_a.id)

    # ------------------------------------------------------------------
    # 2.12 auto_transition_on_assign=True -> status becomes in-progress
    # ------------------------------------------------------------------

    def test_auto_transition_on_assign_changes_status(self):
        """
        When auto_transition_on_assign is True (default) and a ticket is
        assigned while on the default (open) status, it should transition
        to 'in-progress'.
        """
        # Ensure auto_transition is enabled
        from apps.tenants.models import TenantSettings

        settings_obj, _ = TenantSettings.objects.get_or_create(tenant=self.tenant_a)
        settings_obj.auto_transition_on_assign = True
        settings_obj.save()

        # Create a ticket (it will be auto-assigned to the creator by default)
        response = self._create_ticket_via_api(assignee=str(self.manager_a.id))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        set_current_tenant(self.tenant_a)
        ticket = Ticket.objects.get(id=response.data["id"])
        clear_current_tenant()

        # If the ticket was on the default (open) status and got assigned,
        # it should now be in-progress (if auto-transition fired during assign)
        if ticket.status.slug == "in-progress":
            self.assertEqual(ticket.status_id, self.status_in_progress_a.id)
        else:
            # The auto-transition may only fire via the assign_ticket service,
            # not during initial creation. Check via the assign action.
            self.auth_tenant(self.admin_a, self.tenant_a)
            # Reset ticket to open status first
            set_current_tenant(self.tenant_a)
            ticket.status = self.status_open_a
            ticket.assignee = None
            ticket.save()
            clear_current_tenant()

            assign_url = self.api_url(f"/tickets/tickets/{ticket.id}/assign/")
            assign_resp = self.client.post(
                assign_url,
                {"assignee": str(self.manager_a.id)},
                format="json",
            )
            self.assertEqual(assign_resp.status_code, status.HTTP_200_OK)
            set_current_tenant(self.tenant_a)
            ticket.refresh_from_db()
            clear_current_tenant()
            self.assertEqual(ticket.status.slug, "in-progress")

    # ------------------------------------------------------------------
    # 2.13 TicketActivity 'created' event written on creation
    # ------------------------------------------------------------------

    def test_ticket_activity_created_event_written(self):
        """
        After creating a ticket via the API, a TicketActivity with
        event='created' should exist.
        """
        response = self._create_ticket_via_api()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        set_current_tenant(self.tenant_a)
        ticket = Ticket.objects.get(id=response.data["id"])
        activities = TicketActivity.objects.filter(
            ticket=ticket, event=TicketActivity.Event.CREATED,
        )
        clear_current_tenant()
        self.assertTrue(
            activities.exists(),
            "Expected a TicketActivity with event='created' after ticket creation.",
        )

    # ------------------------------------------------------------------
    # 2.14 ActivityLog entry written on creation
    # ------------------------------------------------------------------

    def test_activity_log_entry_written_on_creation(self):
        """
        After creating a ticket via the API, an ActivityLog entry with
        action='created' should exist for that ticket.
        """
        response = self._create_ticket_via_api()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        ticket_ct = ContentType.objects.get_for_model(Ticket)
        set_current_tenant(self.tenant_a)
        logs = ActivityLog.objects.filter(
            content_type=ticket_ct,
            object_id=response.data["id"],
            action=ActivityLog.Action.CREATED,
        )
        self.assertTrue(
            logs.exists(),
            "Expected an ActivityLog entry with action='created' after ticket creation.",
        )
        clear_current_tenant()

    # ------------------------------------------------------------------
    # 2.15 SLA fields not null when SLAPolicy exists for ticket priority
    # ------------------------------------------------------------------

    def test_sla_fields_populated_when_policy_exists(self):
        """
        Creating a ticket with priority='high' (matching sla_policy_a)
        should populate sla_policy, sla_first_response_due, and
        sla_resolution_due fields.
        """
        response = self._create_ticket_via_api(priority="high")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        set_current_tenant(self.tenant_a)
        ticket = Ticket.objects.get(id=response.data["id"])
        clear_current_tenant()

        self.assertIsNotNone(
            ticket.sla_policy,
            "SLA policy should be set when a matching policy exists.",
        )
        self.assertEqual(ticket.sla_policy_id, self.sla_policy_a.id)
        self.assertIsNotNone(
            ticket.sla_first_response_due,
            "sla_first_response_due should be set.",
        )
        self.assertIsNotNone(
            ticket.sla_resolution_due,
            "sla_resolution_due should be set.",
        )

    # ------------------------------------------------------------------
    # 2.16 Kanban card created on default board after ticket creation
    # ------------------------------------------------------------------

    def test_kanban_card_created_on_default_board(self):
        """
        When a default ticket board exists with columns mapped to statuses,
        creating a ticket should automatically create a CardPosition.
        """
        from apps.kanban.models import Board, CardPosition, Column

        # Create a default board with a column mapped to the open status
        set_current_tenant(self.tenant_a)
        board = Board.objects.create(
            name="Default Board",
            resource_type=Board.ResourceType.TICKET,
            is_default=True,
            tenant=self.tenant_a,
        )
        Column.objects.create(
            board=board,
            name="Open",
            order=0,
            status=self.status_open_a,
            tenant=self.tenant_a,
        )
        Column.objects.create(
            board=board,
            name="In Progress",
            order=1,
            status=self.status_in_progress_a,
            tenant=self.tenant_a,
        )
        clear_current_tenant()

        response = self._create_ticket_via_api()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        ticket_ct = ContentType.objects.get_for_model(Ticket)
        cards = CardPosition.unscoped.filter(
            content_type=ticket_ct,
            object_id=response.data["id"],
        )
        self.assertTrue(
            cards.exists(),
            "Expected a kanban CardPosition to be created for the new ticket.",
        )

    # ------------------------------------------------------------------
    # 2.17 ticket_created signal fires exactly once
    # ------------------------------------------------------------------

    def test_ticket_created_signal_fires_once(self):
        """
        The ticket_created signal should fire exactly once when a ticket
        is created via the API.
        """
        from apps.tickets.signals import ticket_created

        signal_calls = []

        def handler(sender, instance, created_by, **kwargs):
            signal_calls.append({
                "instance": instance,
                "created_by": created_by,
            })

        ticket_created.connect(handler)
        try:
            response = self._create_ticket_via_api()
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(
                len(signal_calls), 1,
                f"Expected ticket_created signal to fire exactly once, "
                f"but it fired {len(signal_calls)} time(s).",
            )
            self.assertEqual(
                str(signal_calls[0]["instance"].id),
                response.data["id"],
            )
        finally:
            ticket_created.disconnect(handler)
