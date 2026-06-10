"""REST views for the Inbox Hub.

HubEmail surface:
- ``GET    /api/v1/inbox-hub/hub-emails/``                — list
- ``GET    /api/v1/inbox-hub/hub-emails/{id}/``            — retrieve
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/convert-to-ticket/``
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/dismiss/``
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/assign/``     — manual assign
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/reassign/``   — move to another agent
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/claim/``      — self-assign a held email
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/escalate/``
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/transition/`` — state change
- ``POST   /api/v1/inbox-hub/hub-emails/{id}/note/``       — internal note

Config surface (manager+): Department / RoutingRule / HubEmailSLA / QueueRouting.

Permission stack for HubEmail: ``[IsAuthenticated, IsTenantMember,
HubEmailPermission, IsHubEmailAccessible]`` — ``HubEmailPermission`` carries the
Inbox-Hub-local action→codename map (see apps/inbox_hub/permissions.py).
"""

from django.db.models import Q
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import (
    IsTenantAdminOrManager,
    IsTenantMember,
    _get_membership,
)
from apps.inbox_hub.models import (
    Department,
    HubEmail,
    HubEmailSLA,
    QueueRouting,
    RoutingRule,
)
from apps.inbox_hub.permissions import HubEmailPermission, IsHubEmailAccessible
from apps.inbox_hub.serializers import (
    AssignSerializer,
    ConvertToTicketSerializer,
    DepartmentSerializer,
    DismissSerializer,
    EscalateSerializer,
    HubEmailDetailSerializer,
    HubEmailListSerializer,
    HubEmailNoteSerializer,
    HubEmailSLASerializer,
    NoteSerializer,
    QueueRoutingSerializer,
    RoutingRuleSerializer,
    TransitionSerializer,
)
from apps.inbox_hub.services import (
    add_hub_email_note,
    convert_to_ticket,
    dismiss_hub_email,
    escalate_hub_email,
    reassign_hub_email,
    transition_hub_email,
)
from apps.tickets.serializers import TicketDetailSerializer


class HubEmailViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Read + lifecycle actions on HubEmail rows."""

    # NB: no class-level ``queryset`` (TenantAwareManager evaluates at import
    # time → bound to .none() forever). Built fresh in ``get_queryset``.
    permission_classes = [
        IsAuthenticated,
        IsTenantMember,
        HubEmailPermission,
        IsHubEmailAccessible,
    ]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["inbound__subject", "inbound__sender_email", "inbound__sender_name"]
    ordering_fields = ["created_at", "priority", "state", "sla_response_due_at"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        return {
            "list": HubEmailListSerializer,
            "convert_to_ticket": ConvertToTicketSerializer,
            "dismiss": DismissSerializer,
            "assign": AssignSerializer,
            "reassign": AssignSerializer,
            "escalate": EscalateSerializer,
            "transition": TransitionSerializer,
            "note": NoteSerializer,
        }.get(self.action, HubEmailDetailSerializer)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return HubEmail.objects.none()
        qs = HubEmail.objects.select_related(
            "inbound", "contact", "department", "queue", "assignee",
            "converted_ticket", "escalated_to",
        ).prefetch_related("notes", "notes__author")
        # Agent-tier members (the group-gated tier; Manager+ are unrestricted)
        # only ever see the untriaged backlog plus mail assigned to them, so a
        # crafted ``?state=`` param can't surface another agent's in-flight
        # email. Row-level access is also enforced by IsHubEmailAccessible.
        membership = _get_membership(self.request, getattr(self.request, "tenant", None))
        if membership and membership.effective_role.hierarchy_level > 20:
            qs = qs.filter(
                Q(state=HubEmail.State.NEW) | Q(assignee=self.request.user)
            )
        state = self.request.query_params.get("state")
        if state:
            qs = qs.filter(state=state)
        priority = self.request.query_params.get("priority")
        if priority:
            qs = qs.filter(priority=priority)
        assignee = self.request.query_params.get("assignee")
        if assignee == "me":
            qs = qs.filter(assignee=self.request.user)
        elif assignee == "unassigned":
            qs = qs.filter(assignee__isnull=True)
        elif assignee:
            qs = qs.filter(assignee_id=assignee)
        queue = self.request.query_params.get("queue")
        if queue:
            qs = qs.filter(queue_id=queue)
        department = self.request.query_params.get("department")
        if department:
            qs = qs.filter(department_id=department)
        # "SLA at risk" lens: rows with a live (un-breached) response deadline.
        # The client pairs this with ?ordering=sla_response_due_at (soonest
        # first); OrderingFilter applies the sort so we only filter here.
        if self.request.query_params.get("sla_risk") == "true":
            qs = qs.filter(
                sla_response_due_at__isnull=False,
                response_breached=False,
            )
        return qs

    # -- terminal actions ----------------------------------------------------

    @action(detail=True, methods=["post"], url_path="convert-to-ticket")
    def convert_to_ticket(self, request, pk=None):
        """Agent-driven conversion: creates a Ticket from this HubEmail."""
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
            hub_email, actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(self._detail(hub_email), status=status.HTTP_200_OK)

    # -- assignment ----------------------------------------------------------

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        """Manually assign/reassign this email to a tenant member (override)."""
        return self._do_assign(request)

    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        """Alias of assign — move an already-assigned email to another agent."""
        return self._do_assign(request)

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        """Self-assign a held/NEW email to the requesting agent."""
        hub_email = self.get_object()
        reassign_hub_email(hub_email, request.user, actor=request.user, reason="Claimed")
        return Response(self._detail(hub_email), status=status.HTTP_200_OK)

    def _do_assign(self, request):
        hub_email = self.get_object()
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_user = serializer.validated_data["assignee"]
        self._require_member(new_user)
        reassign_hub_email(
            hub_email, new_user, actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(self._detail(hub_email), status=status.HTTP_200_OK)

    # -- workflow ------------------------------------------------------------

    @action(detail=True, methods=["post"])
    def escalate(self, request, pk=None):
        hub_email = self.get_object()
        serializer = EscalateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        escalate_hub_email(
            hub_email, actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(self._detail(hub_email), status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        hub_email = self.get_object()
        serializer = TransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            transition_hub_email(
                hub_email,
                serializer.validated_data["state"],
                actor=request.user,
                note=serializer.validated_data.get("note", ""),
            )
        except ValueError as exc:
            raise ValidationError({"state": str(exc)})
        return Response(self._detail(hub_email), status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def note(self, request, pk=None):
        hub_email = self.get_object()
        serializer = NoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        created = add_hub_email_note(
            hub_email, request.user, serializer.validated_data["body"],
        )
        return Response(
            HubEmailNoteSerializer(created).data, status=status.HTTP_201_CREATED,
        )

    # -- customer context ----------------------------------------------------

    @action(detail=True, methods=["get"])
    def context(self, request, pk=None):
        """Customer-context summary for the triage cockpit.

        Served from the Hub viewset (NOT ``/contacts/{id}/context/``) so it
        respects the Hub's own ``IsHubEmailAccessible`` row-scoping. A
        freshly-parked email's contact has no ticket yet, so the contacts
        viewset would 404 it for agents (``hierarchy_level > 20`` are scoped to
        contacts on tickets they own) — exactly the users who live in the Hub.
        """
        from apps.contacts.context import build_contact_context

        hub_email = self.get_object()
        if not hub_email.contact_id:
            return Response({"contact": None, "stats": None, "recent_tickets": []})
        return Response(build_contact_context(hub_email.contact, request.tenant))

    # -- helpers -------------------------------------------------------------

    def _detail(self, hub_email):
        hub_email.refresh_from_db()
        return HubEmailDetailSerializer(hub_email).data

    def _require_member(self, user):
        from apps.accounts.models import TenantMembership

        is_member = TenantMembership.objects.filter(
            tenant=self.request.tenant, user=user, is_active=True,
        ).exists()
        if not is_member:
            raise ValidationError({"assignee_id": "User is not an active member of this tenant."})


# ---------------------------------------------------------------------------
# Config viewsets (manager+). Department list/retrieve open to members so the
# Hub UI can render department filters; writes are manager-gated.
# ---------------------------------------------------------------------------


class _TenantConfigViewSet(viewsets.ModelViewSet):
    """Shared base: fresh tenant-scoped queryset + manager-gated writes."""

    model = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.model.objects.none()
        return self.model.objects.all()


class DepartmentViewSet(_TenantConfigViewSet):
    model = Department
    serializer_class = DepartmentSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsTenantMember()]
        return [IsAuthenticated(), IsTenantMember(), IsTenantAdminOrManager()]

    @action(detail=True, methods=["post"], url_path="add-members")
    def add_members(self, request, pk=None):
        from apps.inbox_hub.models import DepartmentMembership

        department = self.get_object()
        user_ids = request.data.get("user_ids") or []
        added = 0
        for uid in user_ids:
            self._require_member(uid)
            _, created = DepartmentMembership.objects.get_or_create(
                tenant=request.tenant, department=department, user_id=uid,
            )
            added += int(created)
        return Response({"added": added}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="remove-members")
    def remove_members(self, request, pk=None):
        from apps.inbox_hub.models import DepartmentMembership

        department = self.get_object()
        user_ids = request.data.get("user_ids") or []
        deleted, _ = DepartmentMembership.objects.filter(
            tenant=request.tenant, department=department, user_id__in=user_ids,
        ).delete()
        return Response({"removed": deleted}, status=status.HTTP_200_OK)

    def _require_member(self, user_id):
        from apps.accounts.models import TenantMembership

        if not TenantMembership.objects.filter(
            tenant=self.request.tenant, user_id=user_id, is_active=True,
        ).exists():
            raise ValidationError({"user_ids": f"{user_id} is not an active tenant member."})


class RoutingRuleViewSet(_TenantConfigViewSet):
    model = RoutingRule
    serializer_class = RoutingRuleSerializer
    permission_classes = [IsAuthenticated, IsTenantMember, IsTenantAdminOrManager]

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        """Persist a new ``order`` for a list of rule ids: ``{"ids": [...]}``."""
        ids = request.data.get("ids") or []
        for index, rule_id in enumerate(ids):
            RoutingRule.objects.filter(pk=rule_id).update(order=(index + 1) * 10)
        return Response({"reordered": len(ids)}, status=status.HTTP_200_OK)


class HubEmailSLAViewSet(_TenantConfigViewSet):
    model = HubEmailSLA
    serializer_class = HubEmailSLASerializer
    permission_classes = [IsAuthenticated, IsTenantMember, IsTenantAdminOrManager]


class QueueRoutingViewSet(_TenantConfigViewSet):
    model = QueueRouting
    serializer_class = QueueRoutingSerializer
    permission_classes = [IsAuthenticated, IsTenantMember, IsTenantAdminOrManager]
