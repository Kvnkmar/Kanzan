"""Regression tests for the 2026-07-10 go-live audit fix batch.

- Health/readiness probes (`/healthz/`, `/readyz/`) resolve without a tenant.
- Single-ticket DELETE now writes a DELETED ActivityLog row (audit parity with
  the bulk-delete path).
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.comments.models import ActivityLog
from apps.tickets.models import Ticket
from main.context import tenant_context

from conftest import TicketFactory


@pytest.mark.django_db
def test_healthz_liveness_is_tenant_agnostic(client):
    resp = client.get("/healthz/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.django_db
def test_readyz_reports_dependency_health(client):
    resp = client.get("/readyz/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["checks"]["database"] == "ok"
    assert data["checks"]["cache"] == "ok"


@pytest.mark.django_db
def test_single_ticket_delete_writes_audit_row(tenant, manager_user, manager_client):
    with tenant_context(tenant):
        ticket = TicketFactory(tenant=tenant, created_by=manager_user)

    resp = manager_client.delete(f"/api/v1/tickets/tickets/{ticket.id}/")
    assert resp.status_code == 204

    fresh = Ticket.unscoped.get(pk=ticket.id)
    assert fresh.is_deleted is True
    assert fresh.deleted_by_id == manager_user.id

    ct = ContentType.objects.get_for_model(Ticket)
    assert ActivityLog.unscoped.filter(
        content_type=ct,
        object_id=ticket.id,
        action=ActivityLog.Action.DELETED,
    ).exists()
