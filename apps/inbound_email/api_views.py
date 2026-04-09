"""
DRF ViewSets for the inbound email app.

Includes:
- InboundEmailViewSet: read-only log of all inbound/outbound emails
- InboxViewSet: agent-facing inbox workflow (list, link, action, ignore)
"""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from apps.accounts.permissions import HasTenantPermission, IsTenantMember
from apps.inbound_email.inbox_services import (
    action_email,
    ignore_email,
    link_email_to_ticket,
)
from apps.inbound_email.models import InboundEmail
from apps.inbound_email.serializers import (
    ActionEmailSerializer,
    InboundEmailDetailSerializer,
    InboundEmailListSerializer,
    InboxEmailListSerializer,
    LinkEmailSerializer,
)

logger = logging.getLogger(__name__)


class InboundEmailViewSet(ReadOnlyModelViewSet):
    """
    Read-only viewset for viewing inbound email processing history.

    All authenticated tenant members can view all tenant emails so they
    can attach emails to the appropriate tickets.
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

        return InboundEmail.objects.filter(tenant=tenant).select_related("ticket")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return InboundEmailDetailSerializer
        return InboundEmailListSerializer


class InboxViewSet(viewsets.GenericViewSet):
    """
    Agent-facing email inbox for unlinked/linked inbound emails.

    Endpoints:
    - GET  /api/v1/emails/inbox/         — list pending/linked emails
    - POST /api/v1/emails/inbox/{id}/link/    — link email to ticket
    - POST /api/v1/emails/inbox/{id}/action/  — take action on linked email
    - POST /api/v1/emails/inbox/{id}/ignore/  — dismiss email from inbox
    """

    permission_classes = [IsAuthenticated, IsTenantMember]
    serializer_class = InboxEmailListSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return InboundEmail.objects.none()

        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return InboundEmail.objects.none()

        return (
            InboundEmail.objects.filter(
                tenant=tenant,
                inbox_status__in=[
                    InboundEmail.InboxStatus.PENDING,
                    InboundEmail.InboxStatus.LINKED,
                ],
                direction=InboundEmail.Direction.INBOUND,
            )
            .select_related("linked_ticket")
            .order_by("-created_at")
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def _get_inbox_email(self, pk):
        """Get an inbox email scoped to the current tenant."""
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return None
        return (
            InboundEmail.objects.filter(tenant=tenant, pk=pk)
            .select_related("linked_ticket")
            .first()
        )

    @action(detail=True, methods=["post"], url_path="link")
    def link(self, request, pk=None):
        serializer = LinkEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = self._get_inbox_email(pk)
        if email is None:
            return Response(
                {"detail": "Email not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            ticket = link_email_to_ticket(
                email,
                serializer.validated_data["ticket_number"],
                linked_by=request.user,
            )
        except DjangoValidationError as e:
            msg = e.message if hasattr(e, "message") else str(e)
            # Determine status code based on error
            if "not found" in str(msg).lower():
                return Response(
                    {"detail": msg},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(
                {"detail": msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Re-fetch to get updated state
        email.refresh_from_db()
        result = InboxEmailListSerializer(email).data
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="action")
    def take_action(self, request, pk=None):
        serializer = ActionEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = self._get_inbox_email(pk)
        if email is None:
            return Response(
                {"detail": "Email not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            action_email(
                email,
                action=serializer.validated_data["action"],
                actioned_by=request.user,
                assignee_id=serializer.validated_data.get("assignee"),
            )
        except DjangoValidationError as e:
            msg = e.message if hasattr(e, "message") else str(e)
            return Response(
                {"detail": msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "status": "actioned",
                "action": serializer.validated_data["action"],
                "ticket": email.linked_ticket.number if email.linked_ticket else None,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="ignore")
    def ignore(self, request, pk=None):
        email = self._get_inbox_email(pk)
        if email is None:
            return Response(
                {"detail": "Email not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            ignore_email(email, ignored_by=request.user)
        except DjangoValidationError as e:
            msg = e.message if hasattr(e, "message") else str(e)
            return Response(
                {"detail": msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_200_OK)
