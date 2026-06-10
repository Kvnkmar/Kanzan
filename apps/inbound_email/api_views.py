"""
DRF ViewSets for the inbound email app.

Includes:
- InboundEmailViewSet: read-only log of all inbound/outbound emails
- InboxViewSet: agent-facing inbox workflow (list, link, action, ignore)
"""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from apps.accounts.permissions import (
    HasTenantPermission,
    IsTenantMember,
    _get_membership,
)
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


@extend_schema_view(
    list=extend_schema(summary="List inbound/outbound emails", tags=["Inbound Email"]),
    retrieve=extend_schema(summary="Retrieve an email", tags=["Inbound Email"]),
)
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

        qs = InboundEmail.objects.filter(tenant=tenant).select_related("ticket")
        params = self.request.query_params

        # Emails explicitly handed to this agent (assigned from the Inbox
        # Hub). These are the ORIGINAL customer messages, so they bypass the
        # internal/customer split and the bounce hiding below — the agent
        # asked to work them. ``status``/``direction`` query params still
        # narrow further via the filter backend.
        if params.get("assigned") == "me":
            return qs.filter(assignee=self.request.user).select_related(
                "ticket", "assignee",
            )

        # Internal-only view (the personal Emails page). External/customer
        # mail is triaged in Inbox Hub, so exclude it here to keep the two
        # surfaces from showing the same messages. Leaves system + agent
        # (internal) records: escalation/assignment notifications, etc.
        # Other consumers (audit log, agent inbox) omit the param and are
        # unaffected.
        if params.get("internal") == "true":
            qs = qs.exclude(sender_type=InboundEmail.SenderType.CUSTOMER)

        # Personal view: only mail addressed to the requesting user — sent to
        # them by another agent. Auto-generated system notification emails
        # (assignment/escalation alerts) are dropped: they duplicate the
        # in-app bell and aren't actionable here. The actual assigned message
        # arrives via ?assigned=me instead.
        if params.get("mine") == "true":
            user_email = (getattr(self.request.user, "email", "") or "").strip()
            if not user_email:
                return qs.none()
            qs = qs.filter(recipient_email__iexact=user_email).exclude(
                direction=InboundEmail.Direction.OUTBOUND,
                sender_type=InboundEmail.SenderType.SYSTEM,
            )

        # Hide bounce DSNs from the default list so the inbox stays useful.
        # They still live in the table (and in BounceLog) for audit; callers
        # opt back in with ?status=bounced or ?include_bounces=true.
        if params.get("status") or params.get("include_bounces") == "true":
            return qs
        return qs.exclude(status=InboundEmail.Status.BOUNCED)

    def get_serializer_class(self):
        if self.action == "retrieve":
            return InboundEmailDetailSerializer
        return InboundEmailListSerializer

    def get_permissions(self):
        # create_ticket is an agent action gated on role inside the handler,
        # not on the read-oriented inbound_email codename map.
        if self.action == "create_ticket":
            return [IsAuthenticated(), IsTenantMember()]
        return super().get_permissions()

    @extend_schema(
        summary="Create a ticket from this email",
        tags=["Inbound Email"],
    )
    @action(detail=True, methods=["post"], url_path="create-ticket")
    def create_ticket(self, request, pk=None):
        """Create a ticket from this email (e.g. one assigned to the agent).

        Reuses the Inbox Hub conversion when the email was parked there so the
        ticket is identical to a Hub convert; falls back to the legacy direct
        create for emails that never went through the Hub. Idempotent — an
        email that already has a ticket returns 400 with the existing number.
        """
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response({"detail": "No tenant."}, status=status.HTTP_400_BAD_REQUEST)

        email = (
            InboundEmail.objects.filter(tenant=tenant, pk=pk)
            .select_related("ticket")
            .first()
        )
        if email is None:
            return Response(
                {"detail": "Email not found."}, status=status.HTTP_404_NOT_FOUND,
            )

        # Agent-or-above only (viewers cannot create tickets).
        membership = _get_membership(request, tenant)
        if membership is None or membership.effective_role.hierarchy_level > 30:
            return Response(
                {"detail": "You do not have permission to create tickets."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if email.ticket_id:
            return Response(
                {
                    "detail": f"This email already has ticket #{email.ticket.number}.",
                    "ticket_id": str(email.ticket_id),
                    "ticket_number": email.ticket.number,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            hub_email = email.hub_email
        except Exception:
            hub_email = None

        if hub_email is not None:
            from apps.inbox_hub.services import convert_to_ticket

            ticket = convert_to_ticket(hub_email, actor=request.user)
        else:
            from apps.inbound_email.services import (
                _create_ticket_from_email,
                find_or_create_contact,
            )

            contact, _ = find_or_create_contact(
                tenant, email.sender_email, email.sender_name,
            )
            ticket = _create_ticket_from_email(email, tenant, contact, request.user)

        return Response(
            {
                "ticket_id": str(ticket.pk),
                "ticket_number": ticket.number,
                "ticket_subject": ticket.subject,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    list=extend_schema(summary="List agent inbox emails", tags=["Inbound Email"]),
    link=extend_schema(summary="Link an email to a ticket", tags=["Inbound Email"]),
    action=extend_schema(summary="Action an email (open/assign/close)", tags=["Inbound Email"]),
    ignore=extend_schema(summary="Mark email as ignored", tags=["Inbound Email"]),
)
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
