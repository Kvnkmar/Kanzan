"""
DRF ViewSets for the kanban app.

* ``BoardViewSet``        -- CRUD for boards, plus a detail_with_cards action.
* ``ColumnViewSet``       -- CRUD for columns scoped to a board.
* ``CardPositionViewSet`` -- manage card positions; move and reorder actions.
"""

import logging

from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
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


def _accessible_boards_q(user):
    """Return a Q filter that hides personal boards owned by other users."""
    return Q(is_personal=False) | Q(is_personal=True, created_by=user)


# ---------------------------------------------------------------------------
# BoardViewSet
# ---------------------------------------------------------------------------


@extend_schema_view(
    list=extend_schema(summary="List kanban boards", tags=["Kanban"]),
    create=extend_schema(summary="Create a kanban board", tags=["Kanban"]),
    retrieve=extend_schema(summary="Retrieve a kanban board", tags=["Kanban"]),
    update=extend_schema(summary="Replace a kanban board", tags=["Kanban"]),
    partial_update=extend_schema(summary="Patch a kanban board", tags=["Kanban"]),
    destroy=extend_schema(summary="Delete a kanban board", tags=["Kanban"]),
    detail_with_cards=extend_schema(summary="Board with columns + cards", tags=["Kanban"]),
    populate=extend_schema(summary="Populate board from tickets", tags=["Kanban"]),
)
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
    permission_resource = "kanban"
    lookup_field = "pk"

    def get_queryset(self):
        # Order: team boards before personal, default within each group first,
        # then alphabetical. Keeps "Support Pipeline" et al ahead of any
        # personal board in the picker.
        return (
            Board.objects.prefetch_related("columns")
            .filter(_accessible_boards_q(self.request.user))
            .order_by("is_personal", "-is_default", "name", "-created_at")
        )

    def get_serializer_class(self):
        if self.action == "detail_with_cards":
            return BoardDetailSerializer
        return BoardSerializer

    def perform_create(self, serializer):
        is_personal = bool(self.request.data.get("is_personal", False))
        board = serializer.save(
            created_by=self.request.user,
            tenant=self.request.tenant,
            is_personal=is_personal,
            # Personal boards can't be the team default.
            is_default=False if is_personal else serializer.validated_data.get("is_default", False),
        )

        # Personal boards stay empty — user adds tickets manually — so skip
        # auto-column generation and ticket auto-population.
        if is_personal:
            return

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


@extend_schema_view(
    list=extend_schema(summary="List kanban columns", tags=["Kanban"]),
    create=extend_schema(summary="Create a kanban column", tags=["Kanban"]),
    retrieve=extend_schema(summary="Retrieve a kanban column", tags=["Kanban"]),
    update=extend_schema(summary="Replace a kanban column", tags=["Kanban"]),
    partial_update=extend_schema(summary="Patch a kanban column", tags=["Kanban"]),
    destroy=extend_schema(summary="Delete a kanban column", tags=["Kanban"]),
)
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
        user = self.request.user
        return (
            Column.objects.filter(board_id=board_pk)
            .filter(
                Q(board__is_personal=False)
                | Q(board__is_personal=True, board__created_by=user)
            )
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

        # Block creating columns on another user's personal board.
        if board.is_personal and board.created_by_id != self.request.user.id:
            from rest_framework.exceptions import NotFound

            raise NotFound("Board not found.")

        # Personal boards don't sync to ticket statuses — strip the mapping.
        if board.is_personal:
            column = serializer.save(board=board, status=None)
        else:
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


@extend_schema_view(
    list=extend_schema(summary="List card positions", tags=["Kanban"]),
    create=extend_schema(summary="Create a card position", tags=["Kanban"]),
    retrieve=extend_schema(summary="Retrieve a card position", tags=["Kanban"]),
    update=extend_schema(summary="Replace a card position", tags=["Kanban"]),
    partial_update=extend_schema(summary="Patch a card position", tags=["Kanban"]),
    destroy=extend_schema(summary="Delete a card position", tags=["Kanban"]),
    add_ticket=extend_schema(summary="Add a ticket as a card", tags=["Kanban"]),
    move=extend_schema(summary="Move a card", tags=["Kanban"]),
    reorder=extend_schema(summary="Reorder cards within a column", tags=["Kanban"]),
)
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
        user = self.request.user
        return (
            CardPosition.objects.filter(column__board_id=board_pk)
            .filter(
                Q(column__board__is_personal=False)
                | Q(column__board__is_personal=True, column__board__created_by=user)
            )
            .select_related("column", "content_type")
            .order_by("column__order", "order")
        )

    @action(detail=False, methods=["post"], url_path="add-ticket")
    def add_ticket(self, request, board_pk=None):
        """
        Add an existing ticket as a card at the bottom of a column.

        Used by personal boards (which start empty) but works for any board.

        Body:
            {
                "column_id": "<uuid>",
                "ticket_id": "<uuid>"
            }
        """
        from django.contrib.contenttypes.models import ContentType
        from apps.tickets.models import Ticket

        column_id = request.data.get("column_id")
        ticket_id = request.data.get("ticket_id")
        if not column_id or not ticket_id:
            return Response(
                {"detail": "column_id and ticket_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            column = (
                Column.objects.select_related("board")
                .filter(board_id=board_pk, id=column_id)
                .first()
            )
        except (ValueError, TypeError):
            column = None
        if column is None:
            return Response(
                {"detail": "Column not found on this board."},
                status=status.HTTP_404_NOT_FOUND,
            )

        board = column.board
        if board.is_personal and board.created_by_id != request.user.id:
            return Response(
                {"detail": "Board not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        ticket = Ticket.objects.filter(id=ticket_id).first()
        if ticket is None:
            return Response(
                {"detail": "Ticket not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        ticket_ct = ContentType.objects.get_for_model(Ticket)

        # Prevent duplicates anywhere on the board.
        already = CardPosition.objects.filter(
            column__board_id=board_pk,
            content_type=ticket_ct,
            object_id=ticket.id,
        ).first()
        if already is not None:
            return Response(
                {"detail": "Ticket is already on this board.",
                 "card_id": str(already.id),
                 "column_id": str(already.column_id)},
                status=status.HTTP_409_CONFLICT,
            )

        # Respect WIP limits.
        if column.wip_limit and column.cards.count() >= column.wip_limit:
            return Response(
                {"detail": f"Column '{column.name}' has reached its WIP limit "
                           f"of {column.wip_limit}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order = column.cards.count()
        card = CardPosition.objects.create(
            column=column,
            content_type=ticket_ct,
            object_id=ticket.id,
            order=order,
        )
        return Response(
            CardPositionSerializer(card).data,
            status=status.HTTP_201_CREATED,
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
