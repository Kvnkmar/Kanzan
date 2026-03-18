"""
DRF ViewSets for the custom_fields app.

Provides CRUD for custom field definitions (admin-only) and read-only
access to custom field values, with filtering by module, content type,
and object ID.
"""

import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import HasTenantPermission
from apps.custom_fields.models import CustomFieldDefinition, CustomFieldValue
from apps.custom_fields.serializers import (
    CustomFieldDefinitionCreateSerializer,
    CustomFieldDefinitionSerializer,
    CustomFieldValueSerializer,
)

logger = logging.getLogger(__name__)


class CustomFieldDefinitionViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for custom field definitions.

    Intended for tenant administrators. Supports filtering by ``module``
    via query parameter and includes a ``reorder`` action for bulk
    position updates.
    """

    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "custom_field"
    search_fields = ["name", "slug"]
    ordering_fields = ["name", "order", "module", "created_at"]
    ordering = ["module", "order"]

    def get_queryset(self):
        qs = CustomFieldDefinition.objects.prefetch_related("visible_to_roles").all()

        # Filter by module if provided.
        module = self.request.query_params.get("module")
        if module:
            qs = qs.filter(module=module)

        return qs

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return CustomFieldDefinitionCreateSerializer
        return CustomFieldDefinitionSerializer

    def perform_create(self, serializer):
        from apps.billing.services import PlanLimitChecker

        module = serializer.validated_data.get("module", "")
        PlanLimitChecker(self.request.tenant).check_can_add_custom_field(module)
        serializer.save()

    # ------------------------------------------------------------------
    # Custom actions
    # ------------------------------------------------------------------

    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request):
        """
        Bulk update the display order of field definitions.

        POST /custom-fields/definitions/reorder/
        {
            "order": [
                {"id": "<uuid>", "order": 0},
                {"id": "<uuid>", "order": 1},
                ...
            ]
        }
        """
        order_data = request.data.get("order", [])

        if not isinstance(order_data, list) or not order_data:
            return Response(
                {"detail": "A non-empty 'order' list is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated_count = 0
        errors = []

        for item in order_data:
            field_id = item.get("id")
            new_order = item.get("order")

            if field_id is None or new_order is None:
                errors.append(f"Item missing 'id' or 'order': {item}")
                continue

            try:
                field_def = self.get_queryset().get(id=field_id)
                field_def.order = int(new_order)
                field_def.save(update_fields=["order", "updated_at"])
                updated_count += 1
            except CustomFieldDefinition.DoesNotExist:
                errors.append(f"Field definition not found: {field_id}")
            except (ValueError, TypeError) as exc:
                errors.append(f"Invalid order value for {field_id}: {exc}")

        response_data = {
            "updated": updated_count,
        }
        if errors:
            response_data["errors"] = errors

        return Response(response_data, status=status.HTTP_200_OK)


class CustomFieldValueViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only access to custom field values.

    Supports filtering by:
        - ``module``: the field definition's module (ticket, contact, company)
        - ``content_type``: the content type ID of the related entity
        - ``object_id``: the UUID of the related entity
        - ``field``: the field definition ID
    """

    serializer_class = CustomFieldValueSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "custom_field"
    ordering_fields = ["created_at", "field__order"]
    ordering = ["field__order"]

    def get_queryset(self):
        qs = CustomFieldValue.objects.select_related("field").all()

        # Filter by module.
        module = self.request.query_params.get("module")
        if module:
            qs = qs.filter(field__module=module)

        # Filter by content_type.
        content_type = self.request.query_params.get("content_type")
        if content_type:
            qs = qs.filter(content_type_id=content_type)

        # Filter by object_id.
        object_id = self.request.query_params.get("object_id")
        if object_id:
            qs = qs.filter(object_id=object_id)

        # Filter by field.
        field_id = self.request.query_params.get("field")
        if field_id:
            qs = qs.filter(field_id=field_id)

        return qs
