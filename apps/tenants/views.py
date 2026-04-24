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

    - **list**: authenticated users see only tenants they belong to;
      superadmins see all.
    - **retrieve**: authenticated users can retrieve their own tenant.
    - **create / update / partial_update / destroy**: superadmins only.
    """

    lookup_field = "slug"

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Tenant.objects.all()
        from apps.accounts.models import TenantMembership

        tenant_ids = TenantMembership.objects.filter(
            user=user, is_active=True,
        ).values_list("tenant_id", flat=True)
        return Tenant.objects.filter(id__in=tenant_ids)

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
        # Validate file type via python-magic (true MIME detection)
        try:
            import magic

            mime = magic.from_buffer(logo_file.read(2048), mime=True)
            logo_file.seek(0)
        except ImportError:
            mime = logo_file.content_type or ""
        if not mime.startswith("image/"):
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

    @action(detail=False, methods=["post"], url_path="test-email")
    def test_email(self, request, *args, **kwargs):
        """
        Send a test outbound email via the configured EMAIL_BACKEND.

        Lets tenant admins verify SMTP credentials without waiting for a
        ticket event. Recipient defaults to the caller's email address.
        Logs the send to the email log so it appears on /inbound-email/.
        """
        from django.conf import settings as dj_settings
        from django.core.mail import EmailMultiAlternatives

        from apps.inbound_email.services import log_outbound_email

        tenant = request.tenant
        recipient = (request.data.get("recipient") or request.user.email or "").strip()
        if not recipient:
            return Response(
                {"detail": "No recipient address available."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subject = f"[{tenant.name}] Test email from Kanzen Suite"
        body_text = (
            "This is a test email sent from your Kanzen Suite instance.\n\n"
            f"Tenant: {tenant.name} ({tenant.slug})\n"
            f"Triggered by: {request.user.email}\n"
            f"Backend: {getattr(dj_settings, 'EMAIL_BACKEND', 'unknown')}\n"
            f"Host: {getattr(dj_settings, 'EMAIL_HOST', '(filebased)')}\n\n"
            "If you received this, outbound mail is working."
        )

        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=body_text,
                from_email=getattr(dj_settings, "DEFAULT_FROM_EMAIL", ""),
                to=[recipient],
            )
            email.send(fail_silently=False)
        except Exception as exc:
            return Response(
                {"detail": f"Send failed: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        log_outbound_email(
            tenant=tenant,
            recipient_email=recipient,
            subject=subject,
            body_text=body_text,
        )

        return Response(
            {
                "detail": "Test email dispatched.",
                "recipient": recipient,
                "backend": getattr(dj_settings, "EMAIL_BACKEND", ""),
            },
            status=status.HTTP_200_OK,
        )
