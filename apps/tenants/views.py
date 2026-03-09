"""
DRF ViewSets for the tenants app.

* ``TenantViewSet``         -- read-only for regular users; full CRUD for superadmins.
* ``TenantSettingsViewSet`` -- tenant admins can view/update their own settings.
"""

from rest_framework import parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsTenantAdmin
from apps.tenants.models import Tenant, TenantSettings
from apps.tenants.serializers import (
    TenantListSerializer,
    TenantSerializer,
    TenantSettingsSerializer,
)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class IsSuperAdmin(permissions.BasePermission):
    """Allow access only to superusers."""

    def has_permission(self, request, view):
        return request.user and request.user.is_superuser


# ---------------------------------------------------------------------------
# ViewSets
# ---------------------------------------------------------------------------


class TenantViewSet(viewsets.ModelViewSet):
    """
    Tenant resource.

    - **list / retrieve**: any authenticated user (read-only).
    - **create / update / partial_update / destroy**: superadmins only.
    """

    queryset = Tenant.objects.all()
    lookup_field = "slug"

    def get_serializer_class(self):
        if self.action in ("list",):
            return TenantListSerializer
        return TenantSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsSuperAdmin()]


class TenantSettingsViewSet(viewsets.GenericViewSet):
    """
    Tenant settings resource scoped to the current request tenant.

    Only **retrieve** and **partial_update** are exposed -- settings are
    auto-created by a signal and should never be created or deleted via
    the API.
    """

    serializer_class = TenantSettingsSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantAdmin]

    def get_object(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            # Should not happen behind TenantMiddleware, but guard anyway.
            from rest_framework.exceptions import NotFound

            raise NotFound("No tenant context available.")
        return TenantSettings.objects.select_related("tenant").get(tenant=tenant)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(
        detail=False,
        methods=["post", "delete"],
        url_path="logo",
        parser_classes=[parsers.MultiPartParser, parsers.FormParser],
    )
    def logo(self, request, *args, **kwargs):
        """Upload or remove the tenant logo."""
        tenant = request.tenant
        if request.method == "DELETE":
            if tenant.logo:
                tenant.logo.delete(save=True)
            return Response({"logo_url": None})
        logo_file = request.FILES.get("logo")
        if not logo_file:
            return Response(
                {"detail": "No file provided."}, status=status.HTTP_400_BAD_REQUEST
            )
        # Validate file type
        content_type = logo_file.content_type or ""
        if not content_type.startswith("image/"):
            return Response(
                {"detail": "File must be an image."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Limit to 2MB
        if logo_file.size > 2 * 1024 * 1024:
            return Response(
                {"detail": "Logo must be under 2 MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Delete old logo if exists
        if tenant.logo:
            tenant.logo.delete(save=False)
        tenant.logo = logo_file
        tenant.save(update_fields=["logo"])
        logo_url = request.build_absolute_uri(tenant.logo.url)
        return Response({"logo_url": logo_url})
