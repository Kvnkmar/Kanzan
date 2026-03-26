"""
DRF ViewSet for the inbound email log.

Read-only API for viewing inbound email processing history.
Admins/managers see all tenant emails; agents see only emails
linked to tickets they created or are assigned to.
"""

from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from apps.accounts.permissions import HasTenantPermission, _get_membership
from apps.inbound_email.models import InboundEmail
from apps.inbound_email.serializers import (
    InboundEmailDetailSerializer,
    InboundEmailListSerializer,
)


class InboundEmailViewSet(ReadOnlyModelViewSet):
    """
    Read-only viewset for viewing inbound email processing history.

    - Admin / Manager (hierarchy_level <= 20): see all tenant emails.
    - Agent (hierarchy_level <= 30): see emails linked to their own
      tickets (created_by or assignee) plus unlinked emails.
    """

    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "inbound_email"
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "direction"]
    search_fields = ["sender_email", "subject", "sender_name"]
    ordering_fields = ["created_at", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return InboundEmail.objects.none()

        qs = InboundEmail.objects.filter(tenant=tenant).select_related("ticket")

        user = self.request.user
        if not user.is_superuser:
            membership = _get_membership(self.request, tenant)
            if membership and membership.role.hierarchy_level > 30:
                # Viewer: only emails linked to their tickets
                # or unlinked emails
                qs = qs.filter(
                    Q(ticket__assignee=user)
                    | Q(ticket__created_by=user)
                    | Q(ticket__isnull=True)
                )

        return qs

    def get_serializer_class(self):
        if self.action == "retrieve":
            return InboundEmailDetailSerializer
        return InboundEmailListSerializer
