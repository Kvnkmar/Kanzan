"""
DRF ViewSets for the kanban app.

* ``BoardViewSet``        -- CRUD for boards, plus a detail_with_cards action.
* ``ColumnViewSet``       -- CRUD for columns scoped to a board.
* ``CardPositionViewSet`` -- manage card positions; move and reorder actions.
"""

import logging

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsTenantMember
from apps.kanban.models import Board, CardPosition, Column
from apps.kanban.serializers import (
    BoardDetailSerializer,
    BoardSerializer,
    CardMoveSerializer,
    CardPositionSerializer,
    ColumnSerializer,
)
from apps.kanban.services import move_card, populate_board_from_tickets, sync_board_with_statuses

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BoardViewSet
# ---------------------------------------------------------------------------


class BoardViewSet(viewsets.ModelViewSet):
    """
    CRUD operations for kanban boards.

    Boards are automatically scoped to the current tenant via
    ``TenantAwareManager``.

    Extra actions:
        detail_with_cards -- GET /boards/{id}/detail/
            Returns the full board including all columns and cards with
            resolved content-object data.
    """

    serializer_class = BoardSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]
    lookup_field = "pk"

    def get_queryset(self):
        return Board.objects.prefetch_related("columns").all()

    def get_serializer_class(self):
        if self.action == "detail_with_cards":
            return BoardDetailSerializer
        return BoardSerializer

    def perform_create(self, serializer):
        board = serializer.save(created_by=self.request.user, tenant=self.request.tenant)

        if board.resource_type == Board.ResourceType.TICKET:
            # Auto-create columns from ticket statuses when requested
            auto_columns = self.request.data.get("auto_columns", False)
            if auto_columns and not board.columns.exists():
                from apps.tickets.models import TicketStatus

                statuses = TicketStatus.objects.filter(
                    tenant=self.request.tenant
                ).order_by("order", "name")
                for idx, status in enumerate(statuses):
                    Column.objects.create(
                        tenant=self.request.tenant,
                        board=board,
                        name=status.name,
                        order=idx,
                        status=status,
                    )

            # Auto-populate with existing tickets
            populate_board_from_tickets(board)

    @action(detail=True, methods=["get"], url_path="detail")
    def detail_with_cards(self, request, pk=None):
        """Return the board with all columns and their cards."""
        board = self.get_object()
        serializer = self.get_serializer(board)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="populate")
    def populate(self, request, pk=None):
        """Populate the board with cards from existing tickets based on column-status mapping."""
        board = self.get_object()
        cards_created = populate_board_from_tickets(board)
        return Response(
            {"cards_created": cards_created},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# ColumnViewSet
# ---------------------------------------------------------------------------


class ColumnViewSet(viewsets.ModelViewSet):
    """
    CRUD operations for columns within a specific board.

    The board is identified by the ``board_pk`` URL kwarg, set up via
    nested routing.
    """

    serializer_class = ColumnSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        from django.db.models import Count

        board_pk = self.kwargs.get("board_pk")
        return (
            Column.objects.filter(board_id=board_pk)
            .select_related("board")
            .annotate(card_count=Count("cards"))
            .order_by("order")
        )

    def perform_create(self, serializer):
        board_pk = self.kwargs.get("board_pk")
        try:
            board = Board.objects.get(pk=board_pk)
        except Board.DoesNotExist:
            from rest_framework.exceptions import NotFound

            raise NotFound("Board not found.")
        column = serializer.save(board=board)

        # Auto-populate this column with matching tickets if it has a status mapping
        if column.status_id and board.resource_type == Board.ResourceType.TICKET:
            from django.contrib.contenttypes.models import ContentType
            from apps.tickets.models import Ticket

            ticket_ct = ContentType.objects.get_for_model(Ticket)
            existing_ids = set(
                CardPosition.objects.filter(
                    column__board=board, content_type=ticket_ct,
                ).values_list("object_id", flat=True)
            )
            tickets = Ticket.objects.filter(
                tenant=board.tenant, status=column.status,
            ).exclude(id__in=existing_ids)
            order = column.cards.count()
            for ticket in tickets:
                CardPosition.objects.create(
                    column=column,
                    content_type=ticket_ct,
                    object_id=ticket.id,
                    order=order,
                )
                order += 1


# ---------------------------------------------------------------------------
# CardPositionViewSet
# ---------------------------------------------------------------------------


class CardPositionViewSet(viewsets.ModelViewSet):
    """
    CRUD and movement operations for card positions within a board.

    The board is identified by the ``board_pk`` URL kwarg.

    Extra actions:
        move    -- POST /boards/{board_pk}/cards/move/
        reorder -- POST /boards/{board_pk}/cards/reorder/
    """

    serializer_class = CardPositionSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        board_pk = self.kwargs.get("board_pk")
        return (
            CardPosition.objects.filter(column__board_id=board_pk)
            .select_related("column", "content_type")
            .order_by("column__order", "order")
        )

    @action(detail=False, methods=["post"], url_path="move")
    def move(self, request, board_pk=None):
        """
        Move a card to a different column at a specified position.

        Expects JSON body:
            {
                "card_id": "<uuid>",
                "target_column_id": "<uuid>",
                "position": <int>
            }
        """
        serializer = CardMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        card = serializer.validated_data["card"]
        target_column = serializer.validated_data["target_column"]
        position = serializer.validated_data["position"]

        try:
            updated_card = move_card(card, target_column, position)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            CardPositionSerializer(updated_card).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request, board_pk=None):
        """
        Reorder a card within its current column.

        Accepts the same payload as ``move`` (card_id, target_column_id, position)
        but the target_column_id must match the card's current column.
        """
        serializer = CardMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        card = serializer.validated_data["card"]
        target_column = serializer.validated_data["target_column"]
        position = serializer.validated_data["position"]

        if card.column_id != target_column.id:
            return Response(
                {"detail": "Reorder requires the target column to match the card's current column. Use the move action instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated_card = move_card(card, target_column, position)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            CardPositionSerializer(updated_card).data,
            status=status.HTTP_200_OK,
        )
