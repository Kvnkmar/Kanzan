"""
Tests for multi-tenant row-level isolation.

Verifies that the TenantAwareManager correctly scopes queries,
that TenantScopedModel auto-assigns tenant on save, and that
cross-tenant data is invisible via the default manager.
"""

from django.test import TestCase

from apps.contacts.models import Contact
from apps.kanban.models import Board, CardPosition, Column
from apps.tickets.models import Ticket, TicketStatus
from main.context import clear_current_tenant, set_current_tenant

from tests.base import TenantTestCase


class TenantManagerScopingTest(TenantTestCase):
    """TenantAwareManager must filter by the current tenant context."""

    def test_tickets_scoped_to_active_tenant(self):
        """Tickets created in tenant A are invisible in tenant B context."""
        ticket_a = self.make_ticket(self.tenant_a, self.admin_a)

        # In tenant A context, ticket is visible
        self.set_tenant(self.tenant_a)
        self.assertIn(ticket_a, Ticket.objects.all())

        # In tenant B context, ticket is invisible
        self.set_tenant(self.tenant_b)
        self.assertNotIn(ticket_a, Ticket.objects.all())
        self.assertEqual(Ticket.objects.count(), 0)

    def test_unscoped_manager_returns_all_tenants(self):
        """The unscoped manager bypasses tenant filtering."""
        self.make_ticket(self.tenant_a, self.admin_a)
        self.make_ticket(self.tenant_b, self.admin_b)

        # Even with tenant A set, unscoped sees both
        self.set_tenant(self.tenant_a)
        self.assertEqual(Ticket.unscoped.count(), 2)

    def test_no_tenant_context_returns_empty(self):
        """Without a tenant context, the default manager returns nothing."""
        self.make_ticket(self.tenant_a, self.admin_a)
        clear_current_tenant()
        self.assertEqual(Ticket.objects.count(), 0)

    def test_ticket_status_scoped_per_tenant(self):
        """Each tenant sees only its own statuses."""
        self.set_tenant(self.tenant_a)
        statuses_a = list(TicketStatus.objects.values_list("slug", flat=True))

        self.set_tenant(self.tenant_b)
        statuses_b = list(TicketStatus.objects.values_list("slug", flat=True))

        # Both have "open" but they are different objects
        self.assertIn("open", statuses_a)
        self.assertIn("open", statuses_b)
        self.assertNotEqual(
            TicketStatus.unscoped.filter(tenant=self.tenant_a, slug="open").first().pk,
            TicketStatus.unscoped.filter(tenant=self.tenant_b, slug="open").first().pk,
        )

    def test_contact_scoped_per_tenant(self):
        """Contacts in tenant A are invisible in tenant B."""
        self.set_tenant(self.tenant_a)
        contact = Contact.objects.create(
            email="customer@example.com",
            first_name="Test",
            last_name="Customer",
        )

        self.set_tenant(self.tenant_b)
        self.assertEqual(Contact.objects.count(), 0)
        self.assertFalse(Contact.objects.filter(pk=contact.pk).exists())


class TenantAutoAssignTest(TenantTestCase):
    """TenantScopedModel must auto-assign tenant from context on save."""

    def test_ticket_gets_tenant_from_context(self):
        """A ticket saved without explicit tenant picks it up from context."""
        self.set_tenant(self.tenant_a)
        ticket = Ticket(
            subject="Auto-tenant test",
            description="Should get tenant A",
            status=self.status_open_a,
            created_by=self.admin_a,
        )
        ticket.save()
        self.assertEqual(ticket.tenant_id, self.tenant_a.pk)

    def test_save_without_context_raises(self):
        """Saving without tenant context raises ValueError."""
        clear_current_tenant()
        ticket = Ticket(
            subject="No context",
            description="Should fail",
            status=self.status_open_a,
            created_by=self.admin_a,
        )
        with self.assertRaises(ValueError):
            ticket.save()


class CardPositionTenantIsolationTest(TenantTestCase):
    """CardPosition (previously missing tenant FK) must be tenant-scoped."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.board_a = Board.objects.create(
            name="Board A",
            resource_type=Board.ResourceType.TICKET,
        )
        self.column_a = Column.objects.create(
            board=self.board_a, name="Open", order=0,
        )

        self.set_tenant(self.tenant_b)
        self.board_b = Board.objects.create(
            name="Board B",
            resource_type=Board.ResourceType.TICKET,
        )
        self.column_b = Column.objects.create(
            board=self.board_b, name="Open", order=0,
        )

    def test_cardposition_has_tenant(self):
        """CardPosition inherits TenantScopedModel and gets tenant."""
        from django.contrib.contenttypes.models import ContentType

        self.set_tenant(self.tenant_a)
        ticket = self.make_ticket(self.tenant_a, self.admin_a)
        ticket_ct = ContentType.objects.get_for_model(Ticket)

        card = CardPosition.objects.create(
            column=self.column_a,
            content_type=ticket_ct,
            object_id=ticket.pk,
            order=0,
        )
        self.assertEqual(card.tenant_id, self.tenant_a.pk)

    def test_cardposition_isolated_between_tenants(self):
        """Cards in tenant A are invisible in tenant B context."""
        from django.contrib.contenttypes.models import ContentType

        ticket_ct = ContentType.objects.get_for_model(Ticket)

        self.set_tenant(self.tenant_a)
        ticket_a = self.make_ticket(self.tenant_a, self.admin_a)
        CardPosition.objects.create(
            column=self.column_a,
            content_type=ticket_ct,
            object_id=ticket_a.pk,
            order=0,
        )

        # Tenant B should not see tenant A's cards
        self.set_tenant(self.tenant_b)
        self.assertEqual(CardPosition.objects.count(), 0)
