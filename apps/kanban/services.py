"""
Business-logic services for the kanban app.

Keeps views thin by encapsulating board creation, status synchronisation,
and card movement logic here.
"""

import logging

from django.db import models, transaction
from django.db.models import F

from apps.kanban.models import Board, CardPosition, Column

logger = logging.getLogger(__name__)


def create_default_board(tenant):
    """
    Create a default ticket board for the given tenant.

    Columns are generated from the tenant's existing TicketStatus records,
    ordered by their natural ordering. If no statuses exist, a minimal set
    of sensible defaults is created.

    Returns:
        Board: The newly created default board.
    """
    from apps.tickets.models import TicketStatus

    with transaction.atomic():
        board = Board(
            tenant=tenant,
            name="Default Ticket Board",
            resource_type=Board.ResourceType.TICKET,
            is_default=True,
        )
        board.save()

        statuses = list(
            TicketStatus.objects.filter(tenant=tenant).order_by("order", "name")
        )

        if statuses:
            for idx, status in enumerate(statuses):
                Column(
                    tenant=tenant,
                    board=board,
                    name=status.name,
                    order=idx,
                    status=status,
                ).save()
        else:
            # Fallback: create generic columns when no statuses are configured.
            default_columns = [
                ("Backlog", "#6c757d"),
                ("To Do", "#0d6efd"),
                ("In Progress", "#ffc107"),
                ("Review", "#fd7e14"),
                ("Done", "#198754"),
            ]
            for idx, (name, color) in enumerate(default_columns):
                Column(
                    tenant=tenant,
                    board=board,
                    name=name,
                    order=idx,
                    color=color,
                ).save()

        logger.info(
            "Created default ticket board '%s' with %d columns for tenant '%s'.",
            board.name,
            board.columns.count(),
            tenant,
        )

    return board


def sync_board_with_statuses(board):
    """
    Synchronise a board's columns with the current set of TicketStatus records.

    - New statuses get a column appended at the end.
    - Removed statuses have their column's status FK cleared (column is kept
      so existing cards are not lost).
    - Existing columns with a valid status are left untouched.

    Only applies to boards with ``resource_type == 'ticket'``.

    Returns:
        tuple[int, int]: (columns_added, columns_unlinked)
    """
    from apps.tickets.models import TicketStatus

    if board.resource_type != Board.ResourceType.TICKET:
        logger.warning(
            "sync_board_with_statuses called on non-ticket board '%s'; skipping.",
            board,
        )
        return 0, 0

    columns_added = 0
    columns_unlinked = 0

    with transaction.atomic():
        current_statuses = set(
            TicketStatus.objects.filter(tenant=board.tenant)
            .values_list("id", flat=True)
        )

        existing_columns = board.columns.select_related("status").all()
        mapped_status_ids = set()

        for col in existing_columns:
            if col.status_id is not None:
                if col.status_id in current_statuses:
                    mapped_status_ids.add(col.status_id)
                else:
                    # Status was deleted -- unlink column but preserve cards.
                    col.status = None
                    col.save(update_fields=["status", "updated_at"])
                    columns_unlinked += 1

        # Determine the next available order value.
        max_order = (
            board.columns.aggregate(max_order=models.Max("order"))["max_order"]
            if board.columns.exists()
            else -1
        )
        if max_order is None:
            max_order = -1

        # Add columns for any statuses not yet represented.
        new_status_ids = current_statuses - mapped_status_ids
        if new_status_ids:
            new_statuses = TicketStatus.objects.filter(id__in=new_status_ids).order_by(
                "order", "name"
            )
            for status in new_statuses:
                max_order += 1
                Column(
                    tenant=board.tenant,
                    board=board,
                    name=status.name,
                    order=max_order,
                    status=status,
                ).save()
                columns_added += 1

        logger.info(
            "Synced board '%s': %d columns added, %d columns unlinked.",
            board,
            columns_added,
            columns_unlinked,
        )

    return columns_added, columns_unlinked


def populate_board_from_tickets(board):
    """Populate a board with cards from existing tickets based on column-status mapping."""
    from django.contrib.contenttypes.models import ContentType
    from apps.tickets.models import Ticket

    ticket_ct = ContentType.objects.get_for_model(Ticket)
    created_count = 0

    with transaction.atomic():
        # Collect ticket IDs that already have a card anywhere on this board
        existing_ticket_ids = set(
            CardPosition.objects.filter(
                column__board=board,
                content_type=ticket_ct,
            ).values_list("object_id", flat=True)
        )

        for column in board.columns.filter(status__isnull=False).select_related("status"):
            tickets = Ticket.objects.filter(
                tenant=board.tenant, status=column.status
            ).exclude(id__in=existing_ticket_ids)
            existing_order = column.cards.count()
            for ticket in tickets:
                CardPosition.objects.create(
                    column=column,
                    content_type=ticket_ct,
                    object_id=ticket.id,
                    order=existing_order,
                )
                existing_ticket_ids.add(ticket.id)
                existing_order += 1
                created_count += 1

    return created_count


def move_card(card_position, target_column, position):
    """
    Move a card to a different column (or reorder within the same column).

    Handles reordering of surrounding cards so that order values remain
    contiguous and consistent.

    Args:
        card_position: The CardPosition instance to move.
        target_column: The Column instance to move the card into.
        position: The desired zero-based order within the target column.

    Returns:
        CardPosition: The updated card position instance.

    Raises:
        ValueError: If the target column's WIP limit would be exceeded.
    """
    source_column = card_position.column
    is_same_column = source_column.id == target_column.id

    with transaction.atomic():
        # Check WIP limit on target column (only when moving to a different column).
        if not is_same_column and target_column.wip_limit:
            current_count = target_column.cards.count()
            if current_count >= target_column.wip_limit:
                raise ValueError(
                    f"Column '{target_column.name}' has reached its WIP limit "
                    f"of {target_column.wip_limit}."
                )

        if is_same_column:
            _reorder_within_column(card_position, position)
        else:
            _move_across_columns(card_position, source_column, target_column, position)

    card_position.refresh_from_db()

    # Sync ticket status if the target column is mapped to a TicketStatus
    if target_column.status_id is not None:
        content_obj = card_position.content_object
        if content_obj is not None and hasattr(content_obj, 'status_id'):
            if content_obj.status_id != target_column.status_id:
                content_obj.status = target_column.status
                content_obj.save(update_fields=["status", "updated_at"])

    return card_position


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _reorder_within_column(card_position, new_position):
    """Reorder a card within its current column."""
    old_position = card_position.order
    if old_position == new_position:
        return

    column = card_position.column

    # Lock all cards in this column to prevent concurrent reorder corruption.
    list(column.cards.select_for_update().order_by("order"))

    if new_position < old_position:
        # Card moved up: shift cards in [new_position, old_position) down by 1.
        column.cards.filter(
            order__gte=new_position,
            order__lt=old_position,
        ).update(order=F("order") + 1)
    else:
        # Card moved down: shift cards in (old_position, new_position] up by 1.
        column.cards.filter(
            order__gt=old_position,
            order__lte=new_position,
        ).update(order=F("order") - 1)

    card_position.order = new_position
    card_position.save(update_fields=["order"])


def _move_across_columns(card_position, source_column, target_column, position):
    """Move a card from one column to another."""
    old_position = card_position.order

    # Lock cards in both columns to prevent concurrent move corruption.
    list(source_column.cards.select_for_update().order_by("order"))
    list(target_column.cards.select_for_update().order_by("order"))

    # Close the gap in the source column.
    source_column.cards.filter(
        order__gt=old_position,
    ).update(order=F("order") - 1)

    # Open a gap in the target column.
    target_column.cards.filter(
        order__gte=position,
    ).update(order=F("order") + 1)

    # Move the card.
    card_position.column = target_column
    card_position.order = position
    card_position.save(update_fields=["column", "order"])
