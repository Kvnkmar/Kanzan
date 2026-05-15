"""
ViewSet for managing tenant-scoped API keys.

The cleartext secret is returned **once** by ``create`` and ``regenerate`` —
the viewset hand-builds the response so the secret is never persisted in any
serializer instance state. All mutation paths route through
``apps.api_keys.services`` so audit logs + emails fire consistently.

Locked-down to tenant admins via ``IsTenantAdmin`` (stricter than
``HasTenantPermission`` with ``settings`` — Managers cannot create or rotate
keys).
"""

from __future__ import annotations

from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import Role
from apps.accounts.permissions import IsTenantAdmin
from apps.api_keys.models import APIKey
from apps.api_keys.serializers import (
    APIKeyCreateSerializer,
    APIKeyCreatedResponseSerializer,
    APIKeyDetailSerializer,
    APIKeyListSerializer,
)
from apps.api_keys.services import (
    create_api_key,
    regenerate_api_key,
    revoke_api_key,
)


def _serialize_with_cleartext(apikey: APIKey, cleartext: str) -> dict:
    """
    Build the one-time response payload used by ``create`` and ``regenerate``.

    The cleartext is stitched onto the detail payload at response-construction
    time; it is never stored anywhere persistent.
    """
    payload = APIKeyDetailSerializer(apikey).data
    payload["key"] = cleartext
    return payload


@extend_schema_view(
    list=extend_schema(
        summary="List API keys",
        description=(
            "Returns all API keys for the current tenant. Cleartext secrets "
            "are never returned by this endpoint — only the masked prefix, "
            "metadata, and usage stats. Tenant admins only."
        ),
        tags=["API Keys"],
        responses={200: APIKeyListSerializer(many=True)},
    ),
    create=extend_schema(
        summary="Create a new API key",
        description=(
            "Mints a new tenant-scoped service-account API key. The cleartext "
            "secret is returned **once** in the `key` field of the response. "
            "Save it immediately — it will never be shown again. Tenant "
            "admins only.\n\n"
            "The key inherits the permissions of the role assigned at "
            "creation. Default role is Admin; pass `role_id` to select a "
            "less-privileged role (Manager / Agent / Viewer) for "
            "least-privilege integrations."
        ),
        tags=["API Keys"],
        request=APIKeyCreateSerializer,
        responses={
            201: APIKeyCreatedResponseSerializer,
            403: {"description": "Not a tenant admin"},
        },
        examples=[
            OpenApiExample(
                "Zapier integration (default Admin role)",
                value={"name": "Zapier", "expires_at": None},
                request_only=True,
            ),
            OpenApiExample(
                "Reporting bot (specific role)",
                value={
                    "name": "Reporting bot",
                    "role_id": "11111111-1111-1111-1111-111111111111",
                },
                request_only=True,
            ),
        ],
    ),
    retrieve=extend_schema(
        summary="Get API key details",
        description="Returns a single API key's metadata. Never includes the cleartext secret.",
        tags=["API Keys"],
        responses={200: APIKeyDetailSerializer},
    ),
    destroy=extend_schema(
        summary="Hard-delete an API key",
        description=(
            "Permanently deletes the key row, the service-account user, and "
            "the tenant membership. Prefer the `revoke` action for "
            "soft-delete — it preserves audit history."
        ),
        tags=["API Keys"],
    ),
)
class APIKeyViewSet(viewsets.ModelViewSet):
    """CRUD + ``regenerate`` + ``revoke`` for tenant-scoped API keys."""

    serializer_class = APIKeyListSerializer
    permission_classes = [IsAuthenticated, IsTenantAdmin]
    search_fields = ["name", "prefix"]
    ordering_fields = ["created_at", "last_used_at", "name"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        return {
            "create": APIKeyCreateSerializer,
            "list": APIKeyListSerializer,
            "retrieve": APIKeyDetailSerializer,
        }.get(self.action, APIKeyListSerializer)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return APIKey.objects.none()
        return APIKey.objects.select_related(
            "role", "created_by", "service_user"
        )

    def create(self, request, *args, **kwargs):
        input_ser = APIKeyCreateSerializer(
            data=request.data, context={"request": request}
        )
        input_ser.is_valid(raise_exception=True)

        tenant = request.tenant
        role_id = input_ser.validated_data.get("role_id")
        if role_id:
            role = Role.unscoped.get(tenant=tenant, pk=role_id)
        else:
            role = Role.unscoped.get(tenant=tenant, slug="admin")

        apikey, cleartext = create_api_key(
            tenant=tenant,
            name=input_ser.validated_data["name"],
            role=role,
            created_by=request.user,
            expires_at=input_ser.validated_data.get("expires_at"),
        )

        payload = _serialize_with_cleartext(apikey, cleartext)
        return Response(payload, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Regenerate an API key",
        description=(
            "Rotates the key — old cleartext is immediately invalid; new "
            "cleartext is returned **once** in the `key` field. The "
            "service-account user, role, and audit history are preserved so "
            "existing references stay intact."
        ),
        tags=["API Keys"],
        request=None,
        responses={200: APIKeyCreatedResponseSerializer},
    )
    @action(detail=True, methods=["post"], url_path="regenerate")
    def regenerate(self, request, pk=None):
        apikey = self.get_object()
        cleartext = regenerate_api_key(apikey, by=request.user)
        apikey.refresh_from_db()
        payload = _serialize_with_cleartext(apikey, cleartext)
        return Response(payload, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Revoke an API key",
        description=(
            "Soft-revokes the key — subsequent requests with this key return "
            "401. Audit trail is preserved (the API key row, its service "
            "user, and its tenant membership all flip to `is_active=False`)."
        ),
        tags=["API Keys"],
        request=None,
        responses={200: APIKeyDetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        apikey = self.get_object()
        revoke_api_key(apikey, by=request.user)
        apikey.refresh_from_db()
        return Response(
            APIKeyDetailSerializer(apikey).data,
            status=status.HTTP_200_OK,
        )
