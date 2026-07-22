"""Regression tests for the ticket bulk-delete audit/soft-delete fix.

`bulk_update_tickets` 'delete' used to call `ticket.delete()` — a HARD delete
with no ActivityLog and no `deleted_by`, diverging from the single-ticket
DELETE path (which soft-deletes and is reversible via `POST /restore/`). Bulk-
deleted tickets were therefore irreversibly gone with zero record of who
removed them, and left orphan kanban cards via post_delete. The fix aligns bulk
delete with the single-ticket path: soft-delete + `deleted_by` + an audit row.
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.comments.models import ActivityLog
from apps.tickets.models import Ticket
from main.context import tenant_context

from conftest import TicketFactory


@pytest.mark.django_db
class TestBulkDeleteSoftDeletesWithAudit:
    def _make_ticket(self, tenant, user):
        with tenant_context(tenant):
            return TicketFactory(tenant=tenant, created_by=user)

    def test_bulk_delete_soft_deletes_stamps_deleted_by_and_audits(
        self, tenant, manager_user, manager_client
    ):
        ticket = self._make_ticket(tenant, manager_user)

        resp = manager_client.post(
            "/api/v1/tickets/tickets/bulk-action/",
            {"action": "delete", "ticket_ids": [str(ticket.id)]},
            format="json",
        )
        assert resp.status_code == 200

        fresh = Ticket.unscoped.get(pk=ticket.id)  # still present (soft-deleted)
        assert fresh.is_deleted is True
        assert fresh.deleted_at is not None
        assert fresh.deleted_by_id == manager_user.id

        ct = ContentType.objects.get_for_model(Ticket)
        assert ActivityLog.unscoped.filter(
            content_type=ct,
            object_id=ticket.id,
            action=ActivityLog.Action.DELETED,
        ).exists()

    def test_bulk_deleted_ticket_hidden_from_default_manager(
        self, tenant, manager_user, manager_client
    ):
        ticket = self._make_ticket(tenant, manager_user)
        manager_client.post(
            "/api/v1/tickets/tickets/bulk-action/",
            {"action": "delete", "ticket_ids": [str(ticket.id)]},
            format="json",
        )
        with tenant_context(tenant):
            assert not Ticket.objects.filter(pk=ticket.id).exists()

    def test_agent_cannot_bulk_delete(self, tenant, agent_user, agent_client):
        ticket = self._make_ticket(tenant, agent_user)
        resp = agent_client.post(
            "/api/v1/tickets/tickets/bulk-action/",
            {"action": "delete", "ticket_ids": [str(ticket.id)]},
            format="json",
        )
        assert resp.status_code == 403
        assert Ticket.unscoped.get(pk=ticket.id).is_deleted is False

    def test_bulk_delete_multiple_tickets_all_soft_deleted_and_audited(
        self, tenant, manager_user, manager_client
    ):
        t1 = self._make_ticket(tenant, manager_user)
        t2 = self._make_ticket(tenant, manager_user)
        t3 = self._make_ticket(tenant, manager_user)

        resp = manager_client.post(
            "/api/v1/tickets/tickets/bulk-action/",
            {"action": "delete", "ticket_ids": [str(t1.id), str(t2.id), str(t3.id)]},
            format="json",
        )
        assert resp.status_code == 200

        ct = ContentType.objects.get_for_model(Ticket)
        for t in (t1, t2, t3):
            fresh = Ticket.unscoped.get(pk=t.id)
            assert fresh.is_deleted is True
            assert fresh.deleted_by_id == manager_user.id
            assert ActivityLog.unscoped.filter(
                content_type=ct, object_id=t.id, action=ActivityLog.Action.DELETED,
            ).exists()

    def test_bulk_deleted_ticket_is_restorable(
        self, tenant, manager_user, manager_client
    ):
        """The fix's stated motivation: bulk-deleted tickets are reversible via
        POST /restore/ (unlike the old hard-delete)."""
        ticket = self._make_ticket(tenant, manager_user)
        manager_client.post(
            "/api/v1/tickets/tickets/bulk-action/",
            {"action": "delete", "ticket_ids": [str(ticket.id)]},
            format="json",
        )
        assert Ticket.unscoped.get(pk=ticket.id).is_deleted is True

        resp = manager_client.post(
            f"/api/v1/tickets/tickets/{ticket.id}/restore/", {}, format="json",
        )
        assert resp.status_code == 200
        fresh = Ticket.unscoped.get(pk=ticket.id)
        assert fresh.is_deleted is False
        assert fresh.deleted_at is None
        assert fresh.deleted_by_id is None
        with tenant_context(tenant):
            assert Ticket.objects.filter(pk=ticket.id).exists()  # back in default view
