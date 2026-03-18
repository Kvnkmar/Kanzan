"""
DRF ViewSet for the inbound email log.

Read-only API for admins/managers to monitor inbound email processing.
"""

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from apps.accounts.permissions import HasTenantPermission
from apps.inbound_email.models import InboundEmail
from apps.inbound_email.serializers import (
    InboundEmailDetailSerializer,
    InboundEmailListSerializer,
)


class InboundEmailViewSet(ReadOnlyModelViewSet):
    """
    Read-only viewset for viewing inbound email processing history.

    Restricted to admins and managers (hierarchy_level <= 20).
    """

    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "inbound_email"
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["sender_email", "subject", "sender_name"]
    ordering_fields = ["created_at", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return InboundEmail.objects.none()
        return (
            InboundEmail.objects.filter(tenant=tenant)
            .select_related("ticket")
        )

    def get_serializer_class(self):
        if self.action == "retrieve":
            return InboundEmailDetailSerializer
        return InboundEmailListSerializer
