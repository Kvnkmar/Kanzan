"""Regression tests for bulk-action authorization + row-scoping.

Two holes closed (2026-07-10 go-live audit):

1. ``ContactViewSet.bulk_action`` 'delete' hard-deleted via ``Contact.objects``
   with NO delete-privilege gate — bulk_action maps to the "update" permission,
   which agents hold — so any Agent could permanently wipe the tenant's entire
   contact database, including contacts outside their row-level visibility.
   Fix: 'delete' now requires Manager+ (hierarchy_level <= 20), and every branch
   is scoped to ``self.get_queryset()``.

2. ``TicketViewSet.bulk_action`` targeted ``Ticket.objects`` (tenant-scoped
   only), not ``get_queryset``, so an Agent could change_status / change_priority
   / assign / tag tickets they cannot see. Fix: scoped to ``self.get_queryset()``.
"""

import pytest

from apps.contacts.models import Contact
from apps.tickets.models import Ticket
from main.context import tenant_context

from conftest import ContactFactory, ContactGroupFactory, TicketFactory


@pytest.mark.django_db
class TestContactBulkDeleteAuthz:
    def _contact(self, tenant):
        with tenant_context(tenant):
            return ContactFactory(tenant=tenant)

    def test_agent_cannot_bulk_delete_contacts(self, tenant, agent_client):
        c = self._contact(tenant)
        resp = agent_client.post(
            "/api/v1/contacts/contacts/bulk-action/",
            {"action": "delete", "contact_ids": [str(c.id)]},
            format="json",
        )
        assert resp.status_code == 403
        assert Contact.unscoped.filter(pk=c.id).exists()

    def test_agent_cannot_mass_delete_whole_tenant(self, tenant, agent_client):
        ids = [str(self._contact(tenant).id) for _ in range(5)]
        resp = agent_client.post(
            "/api/v1/contacts/contacts/bulk-action/",
            {"action": "delete", "contact_ids": ids},
            format="json",
        )
        assert resp.status_code == 403
        assert Contact.unscoped.filter(pk__in=ids).count() == 5

    def test_manager_can_bulk_delete_contacts(self, tenant, manager_client):
        c = self._contact(tenant)
        resp = manager_client.post(
            "/api/v1/contacts/contacts/bulk-action/",
            {"action": "delete", "contact_ids": [str(c.id)]},
            format="json",
        )
        assert resp.status_code == 200
        assert not Contact.unscoped.filter(pk=c.id).exists()

    def test_agent_bulk_action_scoped_to_visible_contacts(self, tenant, agent_client):
        """A contact not linked to any ticket the agent can see is invisible to
        the agent, so even a non-delete bulk action (add_to_group) 404s instead
        of silently operating on an out-of-visibility row."""
        c = self._contact(tenant)
        with tenant_context(tenant):
            grp = ContactGroupFactory(tenant=tenant)
        resp = agent_client.post(
            "/api/v1/contacts/contacts/bulk-action/",
            {
                "action": "add_to_group",
                "contact_ids": [str(c.id)],
                "params": {"group_id": str(grp.id)},
            },
            format="json",
        )
        assert resp.status_code == 404
        with tenant_context(tenant):
            assert grp.contacts.count() == 0


@pytest.mark.django_db
class TestTicketBulkActionScoping:
    def test_agent_cannot_bulk_act_on_invisible_ticket(
        self, tenant, manager_user, agent_client
    ):
        """A ticket created by someone else and unassigned is outside the agent's
        row visibility — bulk_action must 404, not mutate it."""
        with tenant_context(tenant):
            other = TicketFactory(tenant=tenant, created_by=manager_user, priority="low")
        resp = agent_client.post(
            "/api/v1/tickets/tickets/bulk-action/",
            {
                "action": "change_priority",
                "ticket_ids": [str(other.id)],
                "params": {"priority": "high"},
            },
            format="json",
        )
        assert resp.status_code == 404
        assert Ticket.unscoped.get(pk=other.id).priority == "low"

    def test_agent_can_bulk_act_on_own_ticket(self, tenant, agent_user, agent_client):
        """Guard against over-restriction: an agent may still bulk-act on a
        ticket they created (within their visibility)."""
        with tenant_context(tenant):
            mine = TicketFactory(tenant=tenant, created_by=agent_user, priority="low")
        resp = agent_client.post(
            "/api/v1/tickets/tickets/bulk-action/",
            {
                "action": "change_priority",
                "ticket_ids": [str(mine.id)],
                "params": {"priority": "high"},
            },
            format="json",
        )
        assert resp.status_code == 200
        assert Ticket.unscoped.get(pk=mine.id).priority == "high"
