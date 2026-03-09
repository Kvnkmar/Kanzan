"""
DRF ViewSets for the tickets app.

All viewsets rely on the tenant-aware default manager so that querysets are
automatically scoped to the current tenant. The ``permission_resource``
attribute is set on each viewset for integration with the platform's RBAC
permission backend.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.accounts.permissions import HasTenantPermission, IsTicketAccessible

from apps.comments.models import ActivityLog, Comment
from apps.comments.serializers import (
    ActivityLogSerializer,
    CommentCreateSerializer,
    CommentSerializer,
)
from apps.comments.services import log_activity
from apps.tickets.filters import TicketFilter
from apps.tickets.models import (
    EscalationRule,
    Queue,
    SLAPolicy,
    Ticket,
    TicketAssignment,
    TicketCategory,
    TicketStatus,
)
from apps.tickets.services import (
    assign_ticket,
    change_ticket_priority,
    change_ticket_status,
    close_ticket,
    create_ticket_activity,
    log_ticket_comment,
)
from apps.tickets.serializers import (
    EscalationRuleSerializer,
    QueueSerializer,
    SLAPolicySerializer,
    TicketActivitySerializer,
    TicketAssignmentSerializer,
    TicketCategorySerializer,
    TicketCreateSerializer,
    TicketDetailSerializer,
    TicketListSerializer,
    TicketStatusSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TicketStatus
# ---------------------------------------------------------------------------


class TicketStatusViewSet(ModelViewSet):
    """CRUD for tenant-customisable ticket statuses."""

    serializer_class = TicketStatusSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "ticket_status"
    search_fields = ["name", "slug"]
    ordering_fields = ["order", "name", "created_at"]
    ordering = ["order"]

    def get_queryset(self):
        return TicketStatus.objects.all()


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class QueueViewSet(ModelViewSet):
    """CRUD for ticket queues."""

    serializer_class = QueueSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "queue"
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        return Queue.objects.all()


# ---------------------------------------------------------------------------
# TicketCategory
# ---------------------------------------------------------------------------


class TicketCategoryViewSet(ModelViewSet):
    """CRUD for admin-configurable ticket categories."""

    serializer_class = TicketCategorySerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "ticket_category"
    search_fields = ["name", "slug"]
    ordering_fields = ["order", "name", "created_at"]
    ordering = ["order", "name"]

    def get_queryset(self):
        qs = TicketCategory.objects.all()
        # By default only return active categories (unless ?all=true)
        if self.request.query_params.get("all") != "true":
            qs = qs.filter(is_active=True)
        return qs


# ---------------------------------------------------------------------------
# SLAPolicy
# ---------------------------------------------------------------------------


class SLAPolicyViewSet(ModelViewSet):
    """CRUD for SLA policies."""

    serializer_class = SLAPolicySerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "sla_policy"
    search_fields = ["name"]
    ordering_fields = ["priority", "name", "created_at"]

    def get_queryset(self):
        return SLAPolicy.objects.all()


# ---------------------------------------------------------------------------
# EscalationRule (nested under SLA conceptually, but flat endpoint)
# ---------------------------------------------------------------------------


class EscalationRuleViewSet(ModelViewSet):
    """CRUD for escalation rules."""

    serializer_class = EscalationRuleSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "escalation_rule"
    ordering_fields = ["order", "created_at"]
    ordering = ["order"]

    def get_queryset(self):
        return EscalationRule.objects.select_related("sla_policy").all()


# ---------------------------------------------------------------------------
# Ticket
# ---------------------------------------------------------------------------


class TicketViewSet(ModelViewSet):
    """
    Full CRUD for tickets with rich filtering, search, and an ``assign``
    action for changing the ticket assignee.
    """

    permission_classes = [IsAuthenticated, HasTenantPermission, IsTicketAccessible]
    permission_resource = "ticket"
    filterset_class = TicketFilter
    search_fields = ["subject", "description", "number"]
    ordering_fields = [
        "number",
        "priority",
        "created_at",
        "updated_at",
        "due_date",
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            Ticket.objects.select_related(
                "status",
                "assignee",
                "assigned_by",
                "created_by",
                "queue",
                "contact",
                "contact__company",
                "company",
            )
            .all()
        )

        # Row-level filtering: viewers only see tickets they created or
        # are assigned to.  Admins, managers, and agents see all tickets.
        user = self.request.user
        if not user.is_superuser:
            tenant = getattr(self.request, "tenant", None)
            if tenant:
                from apps.accounts.models import TenantMembership

                cache_attr = "_cached_tenant_membership"
                if hasattr(self.request, cache_attr):
                    membership = getattr(self.request, cache_attr)
                else:
                    membership = (
                        TenantMembership.objects.select_related("role")
                        .filter(user=user, tenant=tenant, is_active=True)
                        .first()
                    )
                    setattr(self.request, cache_attr, membership)

                if membership and membership.role.hierarchy_level > 20:
                    # Agent / Viewer: only tickets they created or are assigned to
                    from django.db.models import Q

                    qs = qs.filter(Q(created_by=user) | Q(assignee=user))

        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return TicketListSerializer
        if self.action in ("create", "update", "partial_update"):
            return TicketCreateSerializer
        return TicketDetailSerializer

    # ------------------------------------------------------------------
    # Activity logging hooks
    # ------------------------------------------------------------------

    def perform_create(self, serializer):
        instance = serializer.save()
        create_ticket_activity(instance, self.request.user, request=self.request)

    def perform_update(self, serializer):
        # Use serializer.instance (not self.get_object()) to ensure the flag
        # is on the same Python object that the signal receives.
        instance = serializer.instance
        from apps.tickets.models import TicketActivity

        # Tell the signal not to log — the ViewSet handles dual-write below.
        instance._skip_signal_logging = True

        # Snapshot old values before the serializer applies changes.
        old_status = instance.status
        old_status_id = instance.status_id
        old_status_name = old_status.name if old_status_id else None
        old_priority = instance.priority
        old_priority_display = instance.get_priority_display()
        old_assignee = instance.assignee
        old_assignee_id = old_assignee.pk if old_assignee else None
        old_assignee_name = (
            old_assignee.get_full_name() or str(old_assignee)
            if old_assignee else None
        )

        # Serializer applies ALL changes in one save.
        updated = serializer.save()
        actor = self.request.user
        tenant = getattr(self.request, "tenant", None)

        # --- Status change ---
        if updated.status_id != old_status_id:
            new_status_name = updated.status.name
            was_closed = old_status.is_closed if old_status else False
            now_closed = updated.status.is_closed

            if now_closed and not was_closed:
                timeline_event = TicketActivity.Event.CLOSED
                audit_action = ActivityLog.Action.CLOSED
            elif was_closed and not now_closed:
                timeline_event = TicketActivity.Event.REOPENED
                audit_action = ActivityLog.Action.REOPENED
            else:
                timeline_event = TicketActivity.Event.STATUS_CHANGED
                audit_action = ActivityLog.Action.STATUS_CHANGED

            msg = f"Status changed from {old_status_name} to {new_status_name}"
            log_activity(
                tenant=tenant, actor=actor, content_object=updated,
                action=audit_action, description=msg,
                changes={"status": [old_status_name, new_status_name]},
                request=self.request,
            )
            TicketActivity.objects.create(
                tenant=tenant, ticket=updated, actor=actor,
                event=timeline_event, message=msg,
                metadata={"old_status": old_status_name, "new_status": new_status_name},
            )

        # --- Priority change ---
        if updated.priority != old_priority:
            new_priority_display = updated.get_priority_display()
            msg = f"Priority changed from {old_priority_display} to {new_priority_display}"
            log_activity(
                tenant=tenant, actor=actor, content_object=updated,
                action=ActivityLog.Action.FIELD_CHANGED, description=msg,
                changes={"priority": [old_priority_display, new_priority_display]},
                request=self.request,
            )
            TicketActivity.objects.create(
                tenant=tenant, ticket=updated, actor=actor,
                event=TicketActivity.Event.PRIORITY_CHANGED, message=msg,
                metadata={"old_priority": old_priority, "new_priority": updated.priority},
            )

        # --- Assignee change ---
        if updated.assignee_id != old_assignee_id:
            new_assignee_name = (
                updated.assignee.get_full_name() or str(updated.assignee)
                if updated.assignee else None
            )
            msg = f"Assigned to {new_assignee_name}" if new_assignee_name else "Unassigned"
            log_activity(
                tenant=tenant, actor=actor, content_object=updated,
                action=ActivityLog.Action.ASSIGNED, description=msg,
                changes={"assignee": [old_assignee_name, new_assignee_name]},
                request=self.request,
            )
            TicketActivity.objects.create(
                tenant=tenant, ticket=updated, actor=actor,
                event=TicketActivity.Event.ASSIGNED, message=msg,
                metadata={"previous_assignee": old_assignee_name, "new_assignee": new_assignee_name},
            )

    # ------------------------------------------------------------------
    # Custom actions
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        """
        Assign (or re-assign) a ticket to a user.

        Expects ``{"assignee": "<user-uuid>"}`` in the request body.
        Optionally accepts a ``"note"`` field.

        The assignee must be an active member of the ticket's tenant.
        On first assignment (from the default open status), the ticket
        auto-transitions to "In Progress".
        """
        ticket = self.get_object()
        assignee_id = request.data.get("assignee")

        if not assignee_id:
            return Response(
                {"detail": "The 'assignee' field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.contrib.auth import get_user_model

        User = get_user_model()

        try:
            assignee = User.objects.get(pk=assignee_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            assign_ticket(
                ticket=ticket,
                assignee=assignee,
                actor=request.user,
                request=request,
                note=request.data.get("note", ""),
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket.refresh_from_db()
        serializer = TicketDetailSerializer(ticket, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        """
        Close a ticket.

        Transitions the ticket to the tenant's closed status. If the ticket
        is already closed this is a no-op. Closed tickets disappear from
        active lists but remain searchable by case number.
        """
        ticket = self.get_object()

        try:
            close_ticket(ticket=ticket, actor=request.user, request=request)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket.refresh_from_db()
        serializer = TicketDetailSerializer(ticket, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="lookup")
    def lookup(self, request):
        """
        Search for a ticket by its case number.

        Returns tickets regardless of closed status. This endpoint is
        designed for the "search by case number" use-case where closed
        tickets must be findable.

        Query params:
            - ``number``: The ticket number to search for (exact match).
            - ``q``: Partial search on subject or number.
        """
        number = request.query_params.get("number")
        q = request.query_params.get("q", "").strip()

        # Use the base queryset (tenant-scoped) but do NOT exclude closed
        qs = (
            Ticket.objects.select_related(
                "status", "assignee", "created_by", "queue",
            ).all()
        )

        if number:
            qs = qs.filter(number=number)
        elif q:
            from django.db.models import Q

            filters = Q(subject__icontains=q)
            if q.isdigit():
                filters |= Q(number=int(q))
            qs = qs.filter(filters)
        else:
            return Response(
                {"detail": "Provide 'number' or 'q' query parameter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = qs.order_by("-created_at")[:50]
        serializer = TicketListSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="teammates")
    def teammates(self, request):
        """
        List active tenant members who can be assigned tickets.

        Any authenticated tenant member can view the teammate list (agents
        need this to see who they can transfer tickets to). The list is
        restricted to active members with Agent-level or higher roles
        (hierarchy_level <= 30).

        GET /tickets/teammates/?search=<query>
        """
        from django.contrib.auth import get_user_model
        from django.db.models import Q

        from apps.accounts.models import TenantMembership

        User = get_user_model()
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response([], status=status.HTTP_200_OK)

        # Active members with Agent-level or higher roles
        member_ids = (
            TenantMembership.objects.filter(
                tenant=tenant, is_active=True,
            )
            .select_related("role")
            .filter(role__hierarchy_level__lte=30)
            .values_list("user_id", flat=True)
        )
        qs = User.objects.filter(id__in=member_ids)

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )

        qs = qs.order_by("first_name", "last_name")[:50]
        results = [
            {
                "id": str(u.pk),
                "email": u.email,
                "full_name": u.get_full_name() or u.email,
            }
            for u in qs
        ]
        return Response({"results": results}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="team-progress")
    def team_progress(self, request):
        """
        Team progress metrics for admins/managers.

        Returns per-agent ticket counts: open, closed (this month),
        and total assigned. Only accessible to Admin/Manager roles
        (hierarchy_level <= 20).

        GET /tickets/team-progress/
        """
        from django.contrib.auth import get_user_model
        from django.db.models import Count, Q
        from django.utils import timezone as tz

        from apps.accounts.models import TenantMembership
        from apps.accounts.permissions import _get_membership

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response(
                {"detail": "Tenant context required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership = _get_membership(request, tenant)
        if not request.user.is_superuser:
            if membership is None or membership.role.hierarchy_level > 20:
                return Response(
                    {"detail": "Admin or Manager access required."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        User = get_user_model()

        # Active agent+ members
        agent_ids = (
            TenantMembership.objects.filter(
                tenant=tenant, is_active=True,
                role__hierarchy_level__lte=30,
            ).values_list("user_id", flat=True)
        )

        now = tz.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        agents = (
            User.objects.filter(id__in=agent_ids)
            .annotate(
                open_count=Count(
                    "assigned_tickets",
                    filter=Q(
                        assigned_tickets__status__is_closed=False,
                        assigned_tickets__tenant=tenant,
                    ),
                ),
                closed_this_month=Count(
                    "assigned_tickets",
                    filter=Q(
                        assigned_tickets__status__is_closed=True,
                        assigned_tickets__tenant=tenant,
                        assigned_tickets__closed_at__gte=month_start,
                    ),
                ),
                total_assigned=Count(
                    "assigned_tickets",
                    filter=Q(assigned_tickets__tenant=tenant),
                ),
            )
            .order_by("first_name", "last_name")
        )

        results = [
            {
                "id": str(a.pk),
                "full_name": a.get_full_name() or a.email,
                "email": a.email,
                "open_count": a.open_count,
                "closed_this_month": a.closed_this_month,
                "total_assigned": a.total_assigned,
            }
            for a in agents
        ]
        return Response({"results": results}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="comments")
    def comments(self, request, pk=None):
        """
        GET: List comments for this ticket.
        POST: Add a comment to this ticket.
        """
        ticket = self.get_object()
        ct = ContentType.objects.get_for_model(Ticket)

        if request.method == "GET":
            comments_qs = (
                Comment.objects.filter(content_type=ct, object_id=ticket.pk)
                .select_related("author", "content_type", "parent")
                .prefetch_related("mentions__mentioned_user")
                .order_by("created_at")
            )
            page = self.paginate_queryset(comments_qs)
            if page is not None:
                serializer = CommentSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = CommentSerializer(comments_qs, many=True)
            return Response(serializer.data)

        # POST - create a comment
        data = request.data.copy()
        data["content_type"] = "tickets.ticket"
        data["object_id"] = str(ticket.pk)
        serializer = CommentCreateSerializer(
            data=data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        comment = serializer.save()

        log_activity(
            tenant=ticket.tenant,
            actor=request.user,
            content_object=ticket,
            action=ActivityLog.Action.COMMENTED,
            description=f"Added a {'internal ' if comment.is_internal else ''}comment.",
            request=request,
        )

        # Ticket timeline (TicketActivity)
        log_ticket_comment(ticket, request.user, is_internal=comment.is_internal)

        # Track first agent response
        from apps.tickets.services import record_first_response

        record_first_response(ticket, request.user)

        return Response(
            CommentSerializer(comment).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"], url_path="activity")
    def activity(self, request, pk=None):
        """List activity log entries for this ticket."""
        ticket = self.get_object()
        ct = ContentType.objects.get_for_model(Ticket)

        activity_qs = (
            ActivityLog.objects.filter(content_type=ct, object_id=ticket.pk)
            .select_related("actor", "content_type")
            .order_by("-created_at")
        )
        page = self.paginate_queryset(activity_qs)
        if page is not None:
            serializer = ActivityLogSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = ActivityLogSerializer(activity_qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        """
        List ticket-specific timeline events (TicketActivity).

        Unlike ``/activity`` (audit log for compliance), this returns the
        human-readable timeline displayed in the ticket detail UI.
        """
        ticket = self.get_object()
        from apps.tickets.models import TicketActivity

        timeline_qs = (
            TicketActivity.objects.filter(ticket=ticket)
            .select_related("actor")
            .order_by("-created_at")
        )
        page = self.paginate_queryset(timeline_qs)
        if page is not None:
            serializer = TicketActivitySerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = TicketActivitySerializer(timeline_qs, many=True)
        return Response(serializer.data)
