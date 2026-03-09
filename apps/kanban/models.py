"""
Models for the kanban app.

Provides Board, Column, and CardPosition models for managing kanban-style
boards that can track tickets, deals, or other entities via generic foreign keys.
"""

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from main.models import TenantScopedModel


class Board(TenantScopedModel):
    """
    A kanban board belonging to a tenant.

    Each board tracks a specific resource type (tickets, deals, etc.) and can
    optionally be marked as the default board for that resource type.
    """

    class ResourceType(models.TextChoices):
        TICKET = "ticket", "Ticket"
        DEAL = "deal", "Deal"

    name = models.CharField(max_length=100)
    resource_type = models.CharField(
        max_length=20,
        choices=ResourceType.choices,
        help_text="The type of entity this board tracks.",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Whether this is the default board for its resource type.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_boards",
    )

    class Meta:
        verbose_name = "board"
        verbose_name_plural = "boards"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_resource_type_display()})"


class Column(TenantScopedModel):
    """
    A column within a kanban board, representing a stage in the workflow.

    Columns are ordered by the ``order`` field and can optionally be mapped
    to a TicketStatus for automatic status synchronisation.
    """

    board = models.ForeignKey(
        Board,
        on_delete=models.CASCADE,
        related_name="columns",
    )
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(
        help_text="Display order of the column within the board.",
    )
    status = models.ForeignKey(
        "tickets.TicketStatus",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kanban_columns",
        help_text="Optional mapping to a ticket status.",
    )
    wip_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum number of cards allowed in this column. 0 or null means no limit.",
    )
    color = models.CharField(
        max_length=7,
        null=True,
        blank=True,
        help_text="Hex colour code for the column header (e.g. '#ff6600').",
    )

    class Meta:
        verbose_name = "column"
        verbose_name_plural = "columns"
        ordering = ["order"]
        unique_together = [("board", "order")]

    def __str__(self):
        return f"{self.board.name} - {self.name} (#{self.order})"


class CardPosition(models.Model):
    """
    Tracks the position of an entity (ticket, deal, etc.) within a kanban column.

    Uses Django's contenttypes framework for a generic foreign key so that any
    model instance can be placed on a board.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    column = models.ForeignKey(
        Column,
        on_delete=models.CASCADE,
        related_name="cards",
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
    )
    object_id = models.UUIDField()
    content_object = GenericForeignKey("content_type", "object_id")
    order = models.PositiveIntegerField(
        default=0,
        help_text="Position of this card within its column.",
    )

    class Meta:
        verbose_name = "card position"
        verbose_name_plural = "card positions"
        ordering = ["order"]
        unique_together = [("column", "content_type", "object_id")]

    def __str__(self):
        return f"Card {self.object_id} in {self.column.name} @ position {self.order}"
