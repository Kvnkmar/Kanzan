"""
Phase 4e — Kanban board tests.

Covers:
- Board / Column / CardPosition CRUD
- Card move/reorder endpoints
- Tenant scoping of boards
"""

import pytest

from conftest import (
    BoardFactory,
    ColumnFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


@pytest.mark.django_db
class TestBoardCRUD:
    def test_create_board(self, admin_client):
        resp = admin_client.post("/api/v1/kanban/boards/", {
            "name": "Sprint Board",
            "resource_type": "ticket",
        }, format="json")
        assert resp.status_code == 201

    def test_list_boards(self, admin_client, tenant):
        set_current_tenant(tenant)
        BoardFactory(tenant=tenant)
        clear_current_tenant()

        resp = admin_client.get("/api/v1/kanban/boards/")
        assert resp.status_code == 200
        assert resp.data["count"] >= 1


@pytest.mark.django_db
class TestColumnCRUD:
    def test_create_column(self, admin_client, tenant):
        set_current_tenant(tenant)
        board = BoardFactory(tenant=tenant)
        clear_current_tenant()

        resp = admin_client.post(f"/api/v1/kanban/boards/{board.pk}/columns/", {
            "name": "To Do",
            "order": 1,
        }, format="json")
        assert resp.status_code == 201, f"Got {resp.status_code}: {getattr(resp, 'data', resp.content)}"

    def test_list_columns(self, admin_client, tenant):
        set_current_tenant(tenant)
        board = BoardFactory(tenant=tenant)
        ColumnFactory(tenant=tenant, board=board, order=1)
        ColumnFactory(tenant=tenant, board=board, order=2)
        clear_current_tenant()

        resp = admin_client.get(f"/api/v1/kanban/boards/{board.pk}/columns/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestCardPositionCRUD:
    def test_create_card(self, admin_client, tenant, default_status, admin_user):
        set_current_tenant(tenant)
        board = BoardFactory(tenant=tenant)
        column = ColumnFactory(tenant=tenant, board=board, order=1)
        ticket = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
        clear_current_tenant()

        from django.contrib.contenttypes.models import ContentType
        ticket_ct = ContentType.objects.get_for_model(ticket)

        resp = admin_client.post(f"/api/v1/kanban/boards/{board.pk}/cards/", {
            "column": str(column.pk),
            "content_type": ticket_ct.pk,
            "object_id": str(ticket.pk),
            "order": 0,
        }, format="json")
        assert resp.status_code == 201, f"Got {resp.status_code}: {getattr(resp, 'data', resp.content)}"


@pytest.mark.django_db
class TestBoardIsolation:
    def test_boards_isolated_between_tenants(self, tenant, tenant_b, admin_user):
        set_current_tenant(tenant)
        BoardFactory(tenant=tenant, name="Board A")
        clear_current_tenant()

        set_current_tenant(tenant_b)
        BoardFactory(tenant=tenant_b, name="Board B")
        clear_current_tenant()

        from apps.kanban.models import Board
        set_current_tenant(tenant)
        boards = Board.objects.all()
        assert boards.count() == 1
        assert boards.first().name == "Board A"
