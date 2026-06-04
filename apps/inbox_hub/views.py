"""REST views for the Inbox Hub.

Phase 1A surface (read + two terminal actions):
- ``GET    /api/v1/inbox-hub/hub-emails/``                — list
- ``GET    /api/v1/inbox-hub/hub-emails/{id}/``            — retrieve
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/convert-to-ticket/``
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/dismiss/``

assign / reassign / transition / escalate / reply land in later Phase 1
slices. Department + RoutingRule + HubEmailSLA viewsets are deferred
until the admin pages need them.

Permission stack: ``[IsAuthenticated, IsTenantMember, HasTenantPermission,
IsHubEmailAccessible]``. ``permission_resource = "hub_email"`` drives
the codename map in ``HasTenantPermission`` (list/retrieve →
``hub_email.view``, custom actions get an explicit mapping via the
ACTION_MAP override per the existing pattern in TicketViewSet).
"""

from django.shortcuts import get_object_or_404
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import HasTenantPermission, IsTenantMember
from apps.inbox_hub.models import HubEmail
from apps.inbox_hub.permissions import IsHubEmailAccessible
from apps.inbox_hub.serializers import (
    ConvertToTicketSerializer,
    DismissSerializer,
    HubEmailDetailSerializer,
    HubEmailListSerializer,
)
from apps.inbox_hub.services import convert_to_ticket, dismiss_hub_email
from apps.tickets.serializers import TicketDetailSerializer


class HubEmailViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Read + terminal-actions on HubEmail rows.

    The base list/retrieve is read-only. State mutations live behind the
    ``convert_to_ticket`` and ``dismiss`` ``@action`` endpoints (the only
    two terminal transitions Phase 1A ships).
    """

    # NB: do NOT set a class-level ``queryset`` here. ``HubEmail.objects``
    # is the TenantAwareManager which evaluates ``get_current_tenant()``
    # at queryset construction time — at module import time that is
    # always None, leaving the cached queryset bound to ``.none()``
    # forever. We build the queryset fresh in ``get_queryset`` instead.
    # ``serializer_class`` set via ``get_serializer_class`` below.
    permission_classes = [
        IsAuthenticated,
        IsTenantMember,
        HasTenantPermission,
        IsHubEmailAccessible,
    ]
    permission_resource = "hub_email"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["inbound__subject", "inbound__sender_email", "inbound__sender_name"]
    ordering_fields = ["created_at", "priority", "state"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return HubEmailListSerializer
        if self.action == "convert_to_ticket":
            return ConvertToTicketSerializer
        if self.action == "dismiss":
            return DismissSerializer
        return HubEmailDetailSerializer

    def get_queryset(self):
        # drf-spectacular schema generation calls get_queryset() outside
        # a request context — short-circuit so the swappable_dependency
        # walk doesn't choke on TenantAwareManager's empty fallback.
        if getattr(self, "swagger_fake_view", False):
            return HubEmail.objects.none()
        # Built fresh per request so TenantAwareManager re-evaluates the
        # current-tenant contextvar each call (see class-level comment).
        qs = HubEmail.objects.select_related(
            "inbound", "contact", "department", "queue", "assignee",
            "converted_ticket",
        )
        # Optional filter chips for the list page.
        state = self.request.query_params.get("state")
        if state:
            qs = qs.filter(state=state)
        priority = self.request.query_params.get("priority")
        if priority:
            qs = qs.filter(priority=priority)
        assignee = self.request.query_params.get("assignee")
        if assignee == "me":
            qs = qs.filter(assignee=self.request.user)
        elif assignee:
            qs = qs.filter(assignee_id=assignee)
        queue = self.request.query_params.get("queue")
        if queue:
            qs = qs.filter(queue_id=queue)
        department = self.request.query_params.get("department")
        if department:
            qs = qs.filter(department_id=department)
        return qs

    @action(detail=True, methods=["post"], url_path="convert-to-ticket")
    def convert_to_ticket(self, request, pk=None):
        """Agent-driven conversion: creates a Ticket from this HubEmail.

        Reuses the legacy ``_create_ticket_from_email`` so the resulting
        ticket is field-identical to one auto-created by the inbound
        pipeline. Optional payload fields override queue/status/assignee/
        priority on the new ticket.

        Idempotent — already-converted HubEmails return the existing
        ticket with HTTP 200 rather than creating a duplicate.
        """
        hub_email = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        overrides = serializer.validated_data
        ticket = convert_to_ticket(
            hub_email,
            actor=request.user,
            queue=overrides.get("queue"),
            status=overrides.get("status"),
            assignee=overrides.get("assignee"),
            priority=overrides.get("priority"),
        )
        return Response(
            {
                "ticket": TicketDetailSerializer(ticket).data,
                "hub_email": HubEmailDetailSerializer(hub_email).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def dismiss(self, request, pk=None):
        """Agent-driven dismiss. Terminal state — does NOT create a ticket."""
        hub_email = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dismiss_hub_email(
            hub_email,
            actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(
            HubEmailDetailSerializer(hub_email).data,
            status=status.HTTP_200_OK,
        )
