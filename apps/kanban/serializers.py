"""
DRF serializers for the kanban app.

Provides representations for Board, Column, and CardPosition resources,
as well as action-specific serializers for card movement operations.
"""

from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from apps.kanban.models import Board, CardPosition, Column


# ---------------------------------------------------------------------------
# Card / Column serializers
# ---------------------------------------------------------------------------


class CardPositionSerializer(serializers.ModelSerializer):
    """
    Serializer for CardPosition.

    Includes the content type app label and model name so the client knows
    which entity the card represents.
    """

    content_type_label = serializers.SerializerMethodField()

    class Meta:
        model = CardPosition
        fields = [
            "id",
            "column",
            "content_type",
            "content_type_label",
            "object_id",
            "order",
        ]
        read_only_fields = ["id"]

    def get_content_type_label(self, obj):
        """Return 'app_label.model' for the linked content type."""
        if obj.content_type_id:
            ct = ContentType.objects.get_for_id(obj.content_type_id)
            return f"{ct.app_label}.{ct.model}"
        return None


class ColumnSerializer(serializers.ModelSerializer):
    """
    Serializer for Column, including a computed card count.
    """

    card_count = serializers.SerializerMethodField()

    def get_card_count(self, obj):
        # Use annotated value when available (from ColumnViewSet), else fallback.
        if hasattr(obj, "card_count"):
            return obj.card_count
        return obj.cards.count()

    class Meta:
        model = Column
        fields = [
            "id",
            "board",
            "name",
            "order",
            "status",
            "wip_limit",
            "color",
            "card_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "board", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Board serializers
# ---------------------------------------------------------------------------


class BoardSerializer(serializers.ModelSerializer):
    """
    Board serializer with nested read-only columns (summary view).
    """

    columns = ColumnSerializer(many=True, read_only=True)

    class Meta:
        model = Board
        fields = [
            "id",
            "name",
            "resource_type",
            "is_default",
            "created_by",
            "columns",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]


class BoardDetailSerializer(serializers.ModelSerializer):
    """
    Detailed board serializer that includes columns **and** their cards
    with resolved content object data.

    Used by the ``detail_with_cards`` action to provide a full board snapshot
    in a single API call.
    """

    columns = serializers.SerializerMethodField()

    class Meta:
        model = Board
        fields = [
            "id",
            "name",
            "resource_type",
            "is_default",
            "created_by",
            "columns",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_columns(self, board):
        columns = board.columns.prefetch_related("cards__content_type").all()
        allowed_ids = self._get_allowed_ticket_ids()

        # Batch-fetch all content objects to avoid N+1 GenericFK lookups.
        # Group card object_ids by content_type, fetch each group in one
        # query, then map back so _serialize_card can use the cache.
        all_cards = []
        for col in columns:
            all_cards.extend(col.cards.all())

        objects_by_ct = {}  # {content_type_id: {object_id: instance}}
        ct_groups = {}      # {content_type_id: [object_id, ...]}
        for card in all_cards:
            ct_groups.setdefault(card.content_type_id, []).append(card.object_id)

        for ct_id, obj_ids in ct_groups.items():
            ct = ContentType.objects.get_for_id(ct_id)
            model_class = ct.model_class()
            if model_class is None:
                continue
            qs = model_class._default_manager.filter(pk__in=obj_ids)
            # Optimise ticket lookups with select_related
            if hasattr(model_class, "status") and hasattr(model_class, "assignee"):
                qs = qs.select_related("status", "assignee")
            objects_by_ct[ct_id] = {obj.pk: obj for obj in qs}

        self._content_object_cache = objects_by_ct
        return [self._serialize_column(col, allowed_ids) for col in columns]

    def _get_allowed_ticket_ids(self):
        """Return set of allowed ticket IDs for viewers, or None if no filtering."""
        request = self.context.get("request")
        if not request or not hasattr(request, "user"):
            return None
        user = request.user
        if user.is_superuser:
            return None
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return None

        from apps.accounts.models import TenantMembership

        cache_attr = "_cached_tenant_membership"
        if hasattr(request, cache_attr):
            membership = getattr(request, cache_attr)
        else:
            membership = (
                TenantMembership.objects.select_related("role")
                .filter(user=user, tenant=tenant, is_active=True)
                .first()
            )
            setattr(request, cache_attr, membership)

        if membership and membership.role.hierarchy_level > 20:
            from django.db.models import Q

            from apps.tickets.models import Ticket

            return set(
                str(tid)
                for tid in Ticket.unscoped.filter(tenant=tenant)
                .filter(Q(created_by=user) | Q(assignee=user))
                .values_list("id", flat=True)
            )
        return None

    def _serialize_column(self, column, allowed_ticket_ids=None):
        cards = column.cards.select_related("content_type").all()

        if allowed_ticket_ids is not None:
            cards = [
                card
                for card in cards
                if card.content_type.model != "ticket"
                or str(card.object_id) in allowed_ticket_ids
            ]
        else:
            cards = list(cards)

        return {
            "id": str(column.id),
            "name": column.name,
            "order": column.order,
            "status": str(column.status_id) if column.status_id else None,
            "wip_limit": column.wip_limit,
            "color": column.color,
            "card_count": len(cards),
            "cards": [self._serialize_card(card) for card in cards],
        }

    def _serialize_card(self, card):
        """
        Serialize a card position with a summary of its content object.

        Content object data is included as a flat ``data`` dict. If the object
        cannot be resolved (e.g. it was deleted), ``data`` is ``None``.

        Uses the pre-populated ``_content_object_cache`` to avoid per-card
        GenericFK lookups (N+1 → 1 query per content type).
        """
        cache = getattr(self, "_content_object_cache", {})
        ct_objects = cache.get(card.content_type_id, {})
        content_obj = ct_objects.get(card.object_id)
        data = None
        if content_obj is not None:
            data = self._resolve_content_data(content_obj)

        return {
            "id": str(card.id),
            "content_type": f"{card.content_type.app_label}.{card.content_type.model}",
            "object_id": str(card.object_id),
            "order": card.order,
            "data": data,
        }

    @staticmethod
    def _resolve_content_data(obj):
        """
        Extract a summary dict from a content object.

        Attempts common field names so it works generically across tickets,
        deals, and other entity types.
        """
        data = {"id": str(obj.pk)}

        for field in ("title", "name", "subject"):
            if hasattr(obj, field):
                data["title"] = getattr(obj, field)
                break

        for field in ("status", "stage"):
            if hasattr(obj, field):
                value = getattr(obj, field)
                if value is not None and hasattr(value, "name"):
                    data["status"] = {
                        "name": value.name,
                        "color": getattr(value, "color", None),
                    }
                else:
                    data["status"] = str(value) if value is not None else None
                break

        for field in ("assignee", "assigned_to"):
            if hasattr(obj, field):
                assigned = getattr(obj, field)
                if assigned is not None:
                    data["assigned_to"] = {
                        "id": str(assigned.pk),
                        "name": assigned.get_full_name(),
                        "email": assigned.email,
                    }
                break

        if hasattr(obj, "number"):
            data["number"] = getattr(obj, "number")

        if hasattr(obj, "priority"):
            data["priority"] = getattr(obj, "priority")

        if hasattr(obj, "created_at"):
            created_at = getattr(obj, "created_at")
            data["created_at"] = created_at.isoformat() if created_at else None

        return data


# ---------------------------------------------------------------------------
# Action serializers
# ---------------------------------------------------------------------------


class CardMoveSerializer(serializers.Serializer):
    """
    Input serializer for the card move / reorder actions.

    Accepts:
        card_id          -- UUID of the CardPosition to move.
        target_column_id -- UUID of the destination Column.
        position         -- Desired zero-based order in the target column.
    """

    card_id = serializers.UUIDField()
    target_column_id = serializers.UUIDField()
    position = serializers.IntegerField(min_value=0)

    def validate_card_id(self, value):
        try:
            card = CardPosition.objects.select_related("column").get(id=value)
        except CardPosition.DoesNotExist:
            raise serializers.ValidationError("Card position not found.")
        self._card = card
        return value

    def validate_target_column_id(self, value):
        try:
            column = Column.objects.get(id=value)
        except Column.DoesNotExist:
            raise serializers.ValidationError("Target column not found.")
        self._target_column = column
        return value

    def validate(self, attrs):
        """Ensure card and target column belong to the same board."""
        card = getattr(self, "_card", None)
        target = getattr(self, "_target_column", None)

        if card and target:
            if card.column.board_id != target.board_id:
                raise serializers.ValidationError(
                    "Cannot move a card to a column on a different board."
                )

        attrs["card"] = card
        attrs["target_column"] = target
        return attrs
