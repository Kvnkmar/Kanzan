"""
Tests for ticket creation, auto-numbering, and status transitions.
"""

from apps.tickets.models import Ticket, TicketStatus
from apps.tickets.services import assign_ticket, change_ticket_status

from tests.base import TenantTestCase


class TicketAutoNumberTest(TenantTestCase):
    """Ticket.number is auto-incremented per tenant."""

    def test_first_ticket_gets_number_1(self):
        ticket = self.make_ticket(self.tenant_a, self.admin_a)
        self.assertEqual(ticket.number, 1)

    def test_sequential_numbering(self):
        t1 = self.make_ticket(self.tenant_a, self.admin_a, subject="First")
        t2 = self.make_ticket(self.tenant_a, self.admin_a, subject="Second")
        t3 = self.make_ticket(self.tenant_a, self.admin_a, subject="Third")
        self.assertEqual(t1.number, 1)
        self.assertEqual(t2.number, 2)
        self.assertEqual(t3.number, 3)

    def test_numbering_independent_per_tenant(self):
        """Each tenant has its own independent numbering sequence."""
        t_a = self.make_ticket(self.tenant_a, self.admin_a, subject="Tenant A")
        t_b = self.make_ticket(self.tenant_b, self.admin_b, subject="Tenant B")

        # Both start at 1 since they're in different tenants
        self.assertEqual(t_a.number, 1)
        self.assertEqual(t_b.number, 1)

    def test_number_not_reused_when_other_tickets_exist(self):
        """Number increments based on the highest existing ticket."""
        t1 = self.make_ticket(self.tenant_a, self.admin_a, subject="First")
        t2 = self.make_ticket(self.tenant_a, self.admin_a, subject="Second")
        # Delete the first, create another — should get number 3 (not 1)
        # because t2 (number=2) still exists
        t1.delete()
        t3 = self.make_ticket(self.tenant_a, self.admin_a, subject="Third")
        self.assertEqual(t3.number, t2.number + 1)


class TicketServiceTest(TenantTestCase):
    """Tests for the ticket service layer."""

    def test_assign_ticket(self):
        """assign_ticket sets assignee and creates assignment record."""
        ticket = self.make_ticket(self.tenant_a, self.admin_a)
        request = self.make_request(self.admin_a, self.tenant_a)

        assign_ticket(ticket, self.agent_a, self.admin_a, request=request)
        ticket.refresh_from_db()

        self.assertEqual(ticket.assignee_id, self.agent_a.pk)
        self.assertEqual(ticket.assigned_by_id, self.admin_a.pk)
        self.assertIsNotNone(ticket.assigned_at)

    def test_change_status(self):
        """change_ticket_status updates the status."""
        ticket = self.make_ticket(self.tenant_a, self.admin_a)
        request = self.make_request(self.admin_a, self.tenant_a)

        change_ticket_status(
            ticket, self.status_closed_a, self.admin_a, request=request,
        )
        ticket.refresh_from_db()

        self.assertEqual(ticket.status_id, self.status_closed_a.pk)

    def test_closing_sets_closed_at_on_instance(self):
        """pre_save signal sets closed_at on the instance when closing."""
        ticket = self.make_ticket(self.tenant_a, self.admin_a)
        self.assertIsNone(ticket.closed_at)

        # Simulate what happens during a full save (not update_fields)
        self.set_tenant(self.tenant_a)
        ticket.status = self.status_closed_a
        ticket.save()
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.closed_at)

    def test_reopening_clears_closed_at(self):
        """Reopening a closed ticket clears closed_at."""
        # Create ticket with closed status via full save so pre_save sets closed_at
        self.set_tenant(self.tenant_a)
        ticket = Ticket(
            subject="Closed ticket",
            description="Will reopen",
            status=self.status_closed_a,
            created_by=self.admin_a,
        )
        ticket.save()

        # Reopen via full save
        ticket.status = self.status_open_a
        ticket.save()
        ticket.refresh_from_db()
        self.assertIsNone(ticket.closed_at)
