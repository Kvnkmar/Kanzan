"""
DRF ViewSets for the tickets app.

All viewsets rely on the tenant-aware default manager so that querysets are
automatically scoped to the current tenant. The ``permission_resource``
attribute is set on each viewset for integration with the platform's RBAC
permission backend.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from rest_framework import status, viewsets
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
    BusinessHours,
    CannedResponse,
    EscalationRule,
    PublicHoliday,
    Queue,
    SavedView,
    SLAPolicy,
    Ticket,
    TicketAssignment,
    TicketCategory,
    TicketStatus,
    TicketTemplate,
    TicketWatcher,
    TimeEntry,
    Webhook,
)
from apps.tickets.services import (
    assign_ticket,
    change_ticket_priority,
    change_ticket_status,
    close_ticket,
    create_ticket_activity,
    escalate_ticket,
    log_ticket_comment,
    transition_pipeline_stage,
    transition_ticket_status,
)
from apps.tickets.serializers import (
    BusinessHoursSerializer,
    CannedResponseSerializer,
    EscalationRuleSerializer,
    PublicHolidaySerializer,
    QueueSerializer,
    SavedViewSerializer,
    SLAPolicySerializer,
    TicketActivitySerializer,
    TicketAssignmentSerializer,
    TicketCategorySerializer,
    TicketChangeStatusSerializer,
    TicketCreateSerializer,
    TicketDetailSerializer,
    TicketEmailListSerializer,
    TicketEscalateSerializer,
    TicketLinkEmailSerializer,
    TicketListSerializer,
    TicketSendEmailSerializer,
    TicketStatusSerializer,
    TicketTemplateSerializer,
    TicketWatcherAddSerializer,
    TicketWatcherSerializer,
    TimeEntrySerializer,
    TimeEntrySummarySerializer,
    WebhookSerializer,
    WebhookTestSerializer,
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

    def get_permissions(self):
        # Listing/retrieving statuses is allowed for any authenticated user
        # since they are lookup data needed by all roles.
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return super().get_permissions()

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

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return super().get_permissions()

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

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return super().get_permissions()

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
        # Use unscoped manager for restore/include_deleted (re-apply tenant filter),
        # otherwise default manager already excludes soft-deleted tickets.
        include_deleted = (
            self.action == "restore"
            or self.request.query_params.get("include_deleted") == "true"
        )
        if include_deleted:
            from main.context import get_current_tenant

            tenant = get_current_tenant()
            base_qs = Ticket.unscoped.filter(tenant=tenant) if tenant else Ticket.unscoped.none()
        else:
            base_qs = Ticket.objects.all()

        qs = base_qs.select_related(
            "status",
            "assignee",
            "assigned_by",
            "created_by",
            "queue",
            "contact",
            "contact__company",
            "company",
            "sla_policy",
            "status_changed_by",
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
        from apps.billing.services import PlanLimitChecker

        PlanLimitChecker(self.request.tenant).check_can_create_ticket()
        instance = serializer.save()
        create_ticket_activity(instance, self.request.user, request=self.request)

    def perform_update(self, serializer):
        # Use serializer.instance (not self.get_object()) to ensure the flag
        # is on the same Python object that the signal receives.
        instance = serializer.instance

        # Tell the signal not to log — the service layer handles dual-write.
        instance._skip_signal_logging = True

        # Snapshot old values before the serializer applies changes.
        old_status = instance.status
        old_status_id = instance.status_id
        old_priority = instance.priority
        old_assignee = instance.assignee
        old_assignee_id = old_assignee.pk if old_assignee else None

        # Extract service-layer fields BEFORE serializer.save() so we can
        # route them through the proper service functions (which handle
        # Phase 4 hooks, SLA breach checks, and dual-write logging).
        from apps.tickets.services import validate_status_transition

        pending_status = serializer.validated_data.pop("status", None)
        pending_priority = serializer.validated_data.pop("priority", None)
        pending_assignee = serializer.validated_data.pop("assignee", None)

        # Validate status transition before saving anything
        if pending_status and pending_status.pk != old_status_id:
            validate_status_transition(instance, pending_status)

        # Save remaining simple fields (subject, description, tags, etc.)
        updated = serializer.save()
        actor = self.request.user

        # Delegate tracked field changes to the service layer
        if pending_status and pending_status.pk != old_status_id:
            transition_ticket_status(updated, pending_status, actor, request=self.request)

        if pending_priority is not None and pending_priority != old_priority:
            change_ticket_priority(updated, pending_priority, actor, request=self.request)

        if pending_assignee is not None and (
            (pending_assignee.pk if pending_assignee else None) != old_assignee_id
        ):
            if pending_assignee:
                assign_ticket(updated, pending_assignee, actor, request=self.request)
            else:
                # Unassign: update directly and log
                updated.assignee = None
                updated.save(update_fields=["assignee", "updated_at"])
                from apps.comments.services import log_activity as _log_activity
                _log_activity(
                    tenant=getattr(self.request, "tenant", None),
                    actor=actor, content_object=updated,
                    action=ActivityLog.Action.ASSIGNED,
                    description="Unassigned",
                    changes={"assignee": [
                        old_assignee.get_full_name() if old_assignee else None, None,
                    ]},
                    request=self.request,
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

    @action(detail=True, methods=["post"], url_path="change-status")
    def change_status(self, request, pk=None):
        """
        Change ticket status with transition enforcement.

        Validates the transition against the allowed transition map before
        applying. Illegal transitions return 400 with a descriptive error.

        POST /api/v1/tickets/tickets/{id}/change-status/
        {"status": "<ticketstatus-uuid>"}
        """
        ticket = self.get_object()
        serializer = TicketChangeStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        status_id = serializer.validated_data["status"]
        try:
            new_status = TicketStatus.objects.get(pk=status_id)
        except TicketStatus.DoesNotExist:
            return Response(
                {"detail": "Status not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from django.core.exceptions import ValidationError

        try:
            transition_ticket_status(
                ticket=ticket,
                new_status=new_status,
                actor=request.user,
                request=request,
            )
        except ValidationError as exc:
            return Response(
                {"detail": exc.message if hasattr(exc, "message") else str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket.refresh_from_db()
        serializer = TicketDetailSerializer(ticket, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="change-stage")
    def change_stage(self, request, pk=None):
        """
        Change a ticket's pipeline stage.

        POST /api/v1/tickets/tickets/{id}/change-stage/
        {"stage": "<pipelinestage-uuid>", "reason": "optional string"}
        """
        from apps.tickets.models import PipelineStage
        from apps.tickets.serializers import TicketChangeStageSerializer

        ticket = self.get_object()
        serializer = TicketChangeStageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        stage_id = serializer.validated_data["stage"]
        reason = serializer.validated_data.get("reason", "")

        try:
            new_stage = PipelineStage.objects.get(pk=stage_id)
        except PipelineStage.DoesNotExist:
            return Response(
                {"detail": "Pipeline stage not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            transition_pipeline_stage(
                ticket=ticket,
                new_stage=new_stage,
                changed_by=request.user,
                reason=reason,
                request=request,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket.refresh_from_db()
        detail_serializer = TicketDetailSerializer(ticket, context={"request": request})
        return Response(detail_serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="escalate")
    def escalate(self, request, pk=None):
        """
        Escalate a ticket to a different agent or queue.

        Reassigns the ticket, increments escalation_count, posts an
        internal comment with the reason, and recalculates SLA deadlines
        if the new context has a different SLA policy.

        POST /api/v1/tickets/tickets/{id}/escalate/
        {"assignee": "<user-uuid>", "queue": "<queue-uuid>", "reason": "..."}
        """
        ticket = self.get_object()
        serializer = TicketEscalateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from django.contrib.auth import get_user_model

        User = get_user_model()
        assignee = None
        queue = None

        assignee_id = serializer.validated_data.get("assignee")
        if assignee_id:
            try:
                assignee = User.objects.get(pk=assignee_id)
            except User.DoesNotExist:
                return Response(
                    {"detail": "Assignee not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        queue_id = serializer.validated_data.get("queue")
        if queue_id:
            try:
                queue = Queue.objects.get(pk=queue_id)
            except Queue.DoesNotExist:
                return Response(
                    {"detail": "Queue not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            escalate_ticket(
                ticket=ticket,
                actor=request.user,
                reason=serializer.validated_data["reason"],
                assignee=assignee,
                queue=queue,
                request=request,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket.refresh_from_db()
        detail_serializer = TicketDetailSerializer(
            ticket, context={"request": request},
        )
        return Response(detail_serializer.data, status=status.HTTP_200_OK)

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

        # Strip leading '#' so searching "#5" matches ticket number 5
        q_clean = q.lstrip("#")

        # Use the base queryset (tenant-scoped) but do NOT exclude closed
        qs = (
            Ticket.objects.select_related(
                "status", "assignee", "created_by", "queue", "contact",
            ).all()
        )

        if number:
            qs = qs.filter(number=number)
        elif q:
            from django.db.models import Q

            filters = Q(subject__icontains=q)
            if q_clean.isdigit():
                filters |= Q(number=int(q_clean))
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
            from apps.attachments.models import Attachment

            comments_qs = (
                Comment.objects.filter(content_type=ct, object_id=ticket.pk)
                .select_related("author", "content_type", "parent")
                .prefetch_related("mentions__mentioned_user")
                .order_by("created_at")
            )

            # Hide internal notes from non-admin/manager users
            if not request.user.is_superuser:
                from apps.accounts.permissions import _get_membership

                tenant = getattr(request, "tenant", None)
                membership = _get_membership(request, tenant) if tenant else None
                if not membership or membership.role.hierarchy_level > 20:
                    comments_qs = comments_qs.exclude(is_internal=True)

            # Batch-fetch attachments for all comments to avoid N+1
            comments_list = list(comments_qs)
            if comments_list:
                comment_ct = ContentType.objects.get_for_model(Comment)
                comment_ids = [c.pk for c in comments_list]
                all_attachments = (
                    Attachment.objects.filter(
                        content_type=comment_ct, object_id__in=comment_ids,
                    ).select_related("uploaded_by")
                )
                attachments_by_comment = {}
                for att in all_attachments:
                    attachments_by_comment.setdefault(att.object_id, []).append(att)
                for comment in comments_list:
                    comment._prefetched_attachments = attachments_by_comment.get(comment.pk, [])

            page = self.paginate_queryset(comments_list)
            if page is not None:
                serializer = CommentSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = CommentSerializer(comments_list, many=True)
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

        # Track first agent response — only outbound (non-internal) comments
        # by a non-creator agent count as a customer-facing reply.
        if not comment.is_internal:
            from apps.tickets.services import record_first_response

            record_first_response(ticket, request.user)

        # Fire signal so the notification system can notify relevant users
        from apps.notifications.signal_handlers import ticket_comment_created

        ticket_comment_created.send(
            sender=comment.__class__,
            instance=comment,
            tenant=ticket.tenant,
            ticket=ticket,
            author=request.user,
        )

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

    # ------------------------------------------------------------------
    # Bulk actions
    # ------------------------------------------------------------------

    @action(detail=False, methods=["post"], url_path="bulk-action")
    def bulk_action(self, request):
        """
        Apply an action to multiple tickets at once.

        POST /api/v1/tickets/tickets/bulk-action/
        {
            "action": "assign|change_status|change_priority|add_tag|delete",
            "ticket_ids": ["uuid1", "uuid2", ...],
            "params": { ... }
        }
        """
        action_name = request.data.get("action")
        ticket_ids = request.data.get("ticket_ids", [])
        params = request.data.get("params", {})

        if not action_name or not ticket_ids:
            return Response(
                {"error": "action and ticket_ids are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Bulk delete requires Manager+ (hierarchy_level <= 20).
        # The bulk_action permission maps to "update" but delete is a
        # higher-privilege operation that should require "delete" access.
        if action_name == "delete":
            from apps.accounts.permissions import _get_membership

            tenant = getattr(request, "tenant", None)
            membership = _get_membership(request, tenant) if tenant else None
            if not request.user.is_superuser:
                if membership is None or membership.role.hierarchy_level > 20:
                    return Response(
                        {"error": "You do not have permission to delete tickets."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

        tickets = Ticket.objects.filter(id__in=ticket_ids)
        if tickets.count() != len(ticket_ids):
            return Response(
                {"error": "Some tickets not found or access denied."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from apps.tickets.services import bulk_update_tickets

        result = bulk_update_tickets(tickets, action_name, params, request.user, request)

        return Response(
            {
                "success": True,
                "tickets_updated": result["count"],
                "action": action_name,
                "details": result["details"],
            },
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # Email actions
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get"], url_path="emails")
    def emails(self, request, pk=None):
        """
        List inbound/outbound emails linked to this ticket.

        GET /api/v1/tickets/tickets/{id}/emails/
        """
        ticket = self.get_object()
        from apps.inbound_email.models import InboundEmail

        emails_qs = (
            InboundEmail.objects.filter(ticket=ticket)
            .order_by("-created_at")
        )
        serializer = TicketEmailListSerializer(emails_qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="send-email")
    def send_email(self, request, pk=None):
        """
        Send an email from this ticket to a recipient.

        The email is dispatched asynchronously via Celery so the agent
        doesn't block on SMTP delivery. Includes [#N] in the subject
        for threading and sets Reply-To to the tenant's inbound address.

        POST /api/v1/tickets/tickets/{id}/send-email/
        {"to": "customer@example.com", "subject": "...", "body": "..."}
        """
        ticket = self.get_object()
        serializer = TicketSendEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "Tenant context required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        to_email = serializer.validated_data["to"]
        subject = serializer.validated_data["subject"]
        body = serializer.validated_data["body"]

        # Ensure subject contains ticket reference for threading
        ticket_ref = f"[#{ticket.number}]"
        if ticket_ref not in subject:
            subject = f"{ticket_ref} {subject}"

        # Dispatch asynchronously via Celery
        from apps.inbound_email.models import InboundEmail
        from apps.tickets.tasks import send_ticket_email_task

        send_ticket_email_task.delay(
            str(ticket.pk),
            str(tenant.pk),
            to_email,
            subject,
            body,
            sender_type=InboundEmail.SenderType.AGENT,
        )

        # Log to ticket timeline + audit immediately (don't wait for send)
        from apps.tickets.models import TicketActivity as TA

        TA.objects.create(
            tenant=tenant, ticket=ticket, actor=request.user,
            event=TA.Event.COMMENTED,
            message=f"Email queued to {to_email}: {subject}",
        )
        log_activity(
            tenant=tenant, actor=request.user, content_object=ticket,
            action=ActivityLog.Action.FIELD_CHANGED,
            description=f"Sent email to {to_email}",
            request=request,
        )

        # Track first agent response — outbound email is customer-facing.
        from apps.tickets.services import record_first_response

        record_first_response(ticket, request.user)

        logger.info(
            "Agent %s queued email to %s for ticket #%d",
            request.user.email, to_email, ticket.number,
        )
        return Response(
            {"detail": "Email queued for delivery."},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="send-creation-email")
    def send_creation_email(self, request, pk=None):
        """
        Manually send the ticket-created confirmation email to the contact.

        Intended for tenants that have disabled auto-send
        (``TenantSettings.auto_send_ticket_created_email = False``); the
        agent reviews the ticket and then clicks "Send confirmation email"
        on the ticket page to mail the contact.

        Also works when auto-send is on — useful for re-sending if the
        contact says they didn't receive the original.

        POST /api/v1/tickets/tickets/{id}/send-creation-email/
        """
        ticket = self.get_object()
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response(
                {"detail": "Tenant context required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contact = getattr(ticket, "contact", None)
        if contact is None or not getattr(contact, "email", None):
            return Response(
                {"detail": "Ticket has no contact email to send to."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.tickets.tasks import send_ticket_created_email_task

        send_ticket_created_email_task.delay(str(ticket.pk), str(tenant.pk))

        from apps.tickets.models import TicketActivity as TA

        TA.objects.create(
            tenant=tenant, ticket=ticket, actor=request.user,
            event=TA.Event.COMMENTED,
            message=f"Confirmation email queued to {contact.email}",
        )
        log_activity(
            tenant=tenant, actor=request.user, content_object=ticket,
            action=ActivityLog.Action.FIELD_CHANGED,
            description=f"Manually sent ticket confirmation email to {contact.email}",
            request=request,
        )

        logger.info(
            "Agent %s triggered manual confirmation email for ticket #%d (to=%s)",
            request.user.email, ticket.number, contact.email,
        )
        return Response(
            {"detail": "Confirmation email queued for delivery.", "recipient": contact.email},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="link-email")
    def link_email(self, request, pk=None):
        """
        Link an existing inbound email to this ticket.

        Agents can attach unlinked or misrouted emails to the correct
        ticket for follow-up tracking. If the email has body text, a
        Comment is created on the ticket so the content appears in the
        conversation thread.

        POST /api/v1/tickets/tickets/{id}/link-email/
        {"email_id": "<uuid>"}
        """
        ticket = self.get_object()
        serializer = TicketLinkEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.inbound_email.models import InboundEmail
        from apps.inbound_email.utils import strip_quoted_reply

        email_id = serializer.validated_data["email_id"]
        tenant = getattr(request, "tenant", None)

        try:
            inbound = InboundEmail.objects.get(pk=email_id, tenant=tenant)
        except InboundEmail.DoesNotExist:
            return Response(
                {"detail": "Email not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        old_ticket = inbound.ticket
        inbound.ticket = ticket
        inbound.save(update_fields=["ticket", "updated_at"])

        # Create a Comment from the email body so it appears in the
        # ticket's Comments tab (not just the Emails tab).
        body = strip_quoted_reply(inbound.body_text)
        if body.strip():
            ct = ContentType.objects.get_for_model(Ticket)
            Comment.objects.create(
                content_type=ct,
                object_id=ticket.pk,
                author=request.user,
                body=body,
                is_internal=False,
                tenant=tenant,
            )

        from apps.tickets.models import TicketActivity as TA

        TA.objects.create(
            tenant=tenant, ticket=ticket, actor=request.user,
            event=TA.Event.COMMENTED,
            message=f"Linked email from {inbound.sender_email}: {inbound.subject}",
        )
        log_activity(
            tenant=tenant, actor=request.user, content_object=ticket,
            action=ActivityLog.Action.FIELD_CHANGED,
            description=f"Linked inbound email {email_id} to ticket",
            request=request,
        )

        logger.info(
            "Agent %s linked email %s to ticket #%d (was: %s)",
            request.user.email,
            email_id,
            ticket.number,
            f"#{old_ticket.number}" if old_ticket else "unlinked",
        )

        return Response(
            {"detail": "Email linked to ticket.", "email_id": str(email_id)},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="unlinked-emails")
    def unlinked_emails(self, request):
        """
        List inbound emails that are not yet linked to any ticket.

        Agents can browse these and link them to the appropriate ticket.

        GET /api/v1/tickets/tickets/unlinked-emails/
        """
        from apps.inbound_email.models import InboundEmail

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=status.HTTP_200_OK)

        emails_qs = (
            InboundEmail.objects.filter(
                tenant=tenant,
                ticket__isnull=True,
                direction=InboundEmail.Direction.INBOUND,
            )
            .exclude(status=InboundEmail.Status.REJECTED)
            .order_by("-created_at")[:50]
        )
        serializer = TicketEmailListSerializer(emails_qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Ticket linking
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get", "post"], url_path="links")
    def links(self, request, pk=None):
        """
        GET: List all links for this ticket (both directions).
        POST: Create a new link from this ticket to another.

        Agent+ can link tickets.
        """
        ticket = self.get_object()

        if request.method == "GET":
            from django.db.models import Q

            from apps.tickets.models import TicketLink
            from apps.tickets.serializers import TicketLinkSerializer

            links_qs = (
                TicketLink.objects.filter(
                    Q(source_ticket=ticket) | Q(target_ticket=ticket),
                )
                .select_related(
                    "source_ticket", "target_ticket", "created_by",
                )
                .order_by("-created_at")
            )
            serializer = TicketLinkSerializer(links_qs, many=True)
            return Response(serializer.data)

        # POST — create a link
        from apps.tickets.models import TicketLink
        from apps.tickets.serializers import (
            TicketLinkCreateSerializer,
            TicketLinkSerializer,
        )

        serializer = TicketLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        target_id = serializer.validated_data["target"]
        link_type = serializer.validated_data["link_type"]

        try:
            target_ticket = Ticket.objects.get(pk=target_id)
        except Ticket.DoesNotExist:
            return Response(
                {"detail": "Target ticket not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if target_ticket.pk == ticket.pk:
            return Response(
                {"detail": "Cannot link a ticket to itself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if target_ticket.tenant_id != ticket.tenant_id:
            return Response(
                {"detail": "Cannot link tickets from different tenants."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        link, created = TicketLink.objects.get_or_create(
            source_ticket=ticket,
            target_ticket=target_ticket,
            link_type=link_type,
            defaults={"created_by": request.user, "tenant": ticket.tenant},
        )

        if not created:
            return Response(
                {"detail": "This link already exists."},
                status=status.HTTP_409_CONFLICT,
            )

        out = TicketLinkSerializer(link)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"links/(?P<link_id>[0-9a-f-]+)",
    )
    def delete_link(self, request, pk=None, link_id=None):
        """Delete a ticket link. Agent+ can delete links."""
        from apps.tickets.models import TicketLink

        ticket = self.get_object()
        from django.db.models import Q

        try:
            link = TicketLink.objects.get(
                Q(source_ticket=ticket) | Q(target_ticket=ticket),
                pk=link_id,
            )
        except TicketLink.DoesNotExist:
            return Response(
                {"detail": "Link not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Ticket merge
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="merge")
    def merge(self, request, pk=None):
        """
        Merge this ticket into another (primary) ticket.

        Requires Manager+ role. Moves all comments, activities, and
        attachments from this ticket to the primary, creates a
        duplicate_of link, and closes this ticket.

        POST /api/v1/tickets/tickets/{id}/merge/
        {"merge_into": "<primary-ticket-uuid>"}
        """
        # Permission: Manager+ only
        from apps.accounts.permissions import IsTenantAdminOrManager

        perm = IsTenantAdminOrManager()
        if not perm.has_permission(request, self):
            return Response(
                {"detail": "Manager or Admin role required to merge tickets."},
                status=status.HTTP_403_FORBIDDEN,
            )

        from apps.tickets.serializers import TicketMergeSerializer

        secondary = self.get_object()  # the ticket being merged away
        serializer = TicketMergeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        primary_id = serializer.validated_data["merge_into"]
        try:
            primary = Ticket.objects.get(pk=primary_id)
        except Ticket.DoesNotExist:
            return Response(
                {"detail": "Primary ticket not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from apps.tickets.services import merge_tickets

        try:
            merge_tickets(primary, secondary, request.user, request=request)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        primary.refresh_from_db()
        detail_serializer = TicketDetailSerializer(
            primary, context={"request": request},
        )
        return Response(detail_serializer.data, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Ticket split
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="split")
    def split(self, request, pk=None):
        """
        Split selected comments from this ticket into a new child ticket.

        Requires Manager+ role. Creates a new ticket, moves the specified
        comments, links the two tickets as related_to, and initialises SLA
        on the child.

        POST /api/v1/tickets/tickets/{id}/split/
        {"comment_ids": [...], "subject": "...", "priority": "...", "queue": "..."}
        """
        from apps.accounts.permissions import IsTenantAdminOrManager

        perm = IsTenantAdminOrManager()
        if not perm.has_permission(request, self):
            return Response(
                {"detail": "Manager or Admin role required to split tickets."},
                status=status.HTTP_403_FORBIDDEN,
            )

        source = self.get_object()

        from apps.tickets.serializers import TicketSplitSerializer

        serializer = TicketSplitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.tickets.services import split_ticket

        try:
            child = split_ticket(
                source=source,
                comment_ids=serializer.validated_data["comment_ids"],
                actor=request.user,
                new_ticket_data={
                    "subject": serializer.validated_data["subject"],
                    "queue": serializer.validated_data.get("queue"),
                    "priority": serializer.validated_data.get("priority"),
                },
                request=request,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        child.refresh_from_db()
        detail_serializer = TicketDetailSerializer(
            child, context={"request": request},
        )
        return Response(detail_serializer.data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Apply macro
    # ------------------------------------------------------------------

    @action(
        detail=True,
        methods=["post"],
        url_path=r"apply_macro/(?P<macro_id>[0-9a-f-]+)",
    )
    def apply_macro(self, request, pk=None, macro_id=None):
        """
        Apply a macro to this ticket.

        Renders the macro body with variable substitution, creates a
        comment, and executes all macro actions atomically.

        POST /api/v1/tickets/tickets/{id}/apply_macro/{macro_id}/
        """
        from apps.tickets.models import Macro
        from apps.tickets.services import apply_macro as _apply_macro

        ticket = self.get_object()

        try:
            macro = Macro.objects.get(pk=macro_id)
        except Macro.DoesNotExist:
            return Response(
                {"detail": "Macro not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            comment = _apply_macro(ticket, macro, request.user, request=request)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket.refresh_from_db()
        return Response(
            {
                "detail": f"Macro '{macro.name}' applied.",
                "comment_id": str(comment.pk),
                "ticket": TicketDetailSerializer(
                    ticket, context={"request": request},
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        """
        Search for a ticket by number. Returns ticket detail with linked
        inbound emails regardless of ticket status (including closed).

        GET /api/v1/tickets/tickets/search/?ticket_number=42

        Permissions:
        - Admin/Manager: see all tickets
        - Agent: only if they were assignee or actioned_by on a linked email
        """
        ticket_number = request.query_params.get("ticket_number")
        if not ticket_number:
            return Response(
                {"detail": "ticket_number query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            ticket_number = int(ticket_number)
        except (ValueError, TypeError):
            return Response(
                {"detail": "ticket_number must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "No tenant context."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Use unscoped since we want to find across all statuses
        ticket = (
            Ticket.objects.select_related("status", "assignee", "contact", "queue")
            .filter(number=ticket_number)
            .first()
        )
        if ticket is None:
            return Response(
                {"detail": f"No ticket found with number #{ticket_number}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Permission check: Agent can only see if they are assignee or actioned_by
        user = request.user
        from apps.accounts.models import TenantMembership

        membership = (
            TenantMembership.objects.select_related("role")
            .filter(user=user, tenant=tenant, is_active=True)
            .first()
        )
        if membership and membership.role.hierarchy_level > 20:
            # Agent: check if they are assignee or actioned_by on linked emails
            from apps.inbound_email.models import InboundEmail

            is_assignee = ticket.assignee_id == user.pk
            is_actioned_by = InboundEmail.objects.filter(
                linked_ticket=ticket,
                actioned_by=user,
            ).exists()
            if not is_assignee and not is_actioned_by:
                return Response(
                    {"detail": f"No ticket found with number #{ticket_number}"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # Build response with linked emails
        from apps.inbound_email.models import InboundEmail
        from apps.inbound_email.serializers import LinkedEmailForTicketSerializer

        linked_emails = (
            InboundEmail.objects.filter(linked_ticket=ticket)
            .select_related("actioned_by")
            .order_by("-created_at")
        )

        ticket_data = TicketDetailSerializer(
            ticket, context={"request": request},
        ).data
        ticket_data["linked_emails"] = LinkedEmailForTicketSerializer(
            linked_emails, many=True,
        ).data

        return Response(ticket_data)

    @action(detail=True, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request, pk=None):
        """
        POST /api/v1/tickets/tickets/<id>/mark-all-read/

        Mark all comments on this ticket as read for the requesting user.
        Idempotent — uses bulk_create with ignore_conflicts.
        """
        from django.contrib.contenttypes.models import ContentType
        from apps.comments.models import Comment, CommentRead

        ticket = self.get_object()
        ticket_ct = ContentType.objects.get_for_model(Ticket)

        unread_comments = (
            Comment.unscoped.filter(
                content_type=ticket_ct,
                object_id=ticket.pk,
            )
            .exclude(author=request.user)
            .exclude(pk__in=CommentRead.objects.filter(
                user=request.user,
            ).values_list("comment_id", flat=True))
        )

        reads = [
            CommentRead(comment_id=cid, user=request.user)
            for cid in unread_comments.values_list("pk", flat=True)
        ]
        CommentRead.objects.bulk_create(reads, ignore_conflicts=True)

        return Response({"marked": len(reads)}, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Watchers (followers / CC list)
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get", "post"], url_path="watchers")
    def watchers(self, request, pk=None):
        """
        GET: List watchers for this ticket.
        POST: Add a watcher to this ticket.

        POST /api/v1/tickets/tickets/{id}/watchers/
        {"user": "<user-uuid>"}
        """
        ticket = self.get_object()

        if request.method == "GET":
            watchers_qs = (
                TicketWatcher.objects.filter(ticket=ticket)
                .select_related("user")
                .order_by("-created_at")
            )
            serializer = TicketWatcherSerializer(watchers_qs, many=True)
            return Response(serializer.data)

        # POST — add a watcher
        serializer = TicketWatcherAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from django.contrib.auth import get_user_model
        User = get_user_model()

        try:
            user = User.objects.get(pk=serializer.validated_data["user"])
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        watcher, created = TicketWatcher.objects.get_or_create(
            ticket=ticket,
            user=user,
            defaults={
                "tenant": ticket.tenant,
                "reason": TicketWatcher.WatchReason.MANUAL,
            },
        )

        if not created:
            return Response(
                {"detail": "User is already watching this ticket."},
                status=status.HTTP_409_CONFLICT,
            )

        out = TicketWatcherSerializer(watcher)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"watchers/(?P<watcher_user_id>[0-9a-f-]+)",
    )
    def remove_watcher(self, request, pk=None, watcher_user_id=None):
        """Remove a watcher from this ticket."""
        ticket = self.get_object()
        deleted_count, _ = TicketWatcher.objects.filter(
            ticket=ticket, user_id=watcher_user_id,
        ).delete()

        if deleted_count == 0:
            return Response(
                {"detail": "Watcher not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="watch")
    def watch(self, request, pk=None):
        """Toggle watch status for the current user on this ticket."""
        ticket = self.get_object()
        watcher, created = TicketWatcher.objects.get_or_create(
            ticket=ticket,
            user=request.user,
            defaults={
                "tenant": ticket.tenant,
                "reason": TicketWatcher.WatchReason.MANUAL,
            },
        )
        if not created:
            watcher.delete()
            return Response({"watching": False}, status=status.HTTP_200_OK)
        return Response({"watching": True}, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Time tracking
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get", "post"], url_path="time-entries")
    def time_entries(self, request, pk=None):
        """
        GET: List time entries for this ticket.
        POST: Log time against this ticket.

        POST /api/v1/tickets/tickets/{id}/time-entries/
        {"duration_minutes": 30, "description": "Investigated the issue", "is_billable": false}
        """
        ticket = self.get_object()

        if request.method == "GET":
            entries_qs = (
                TimeEntry.objects.filter(ticket=ticket)
                .select_related("user")
                .order_by("-created_at")
            )
            page = self.paginate_queryset(entries_qs)
            if page is not None:
                serializer = TimeEntrySerializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = TimeEntrySerializer(entries_qs, many=True)
            return Response(serializer.data)

        # POST — log time
        serializer = TimeEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = serializer.save(
            ticket=ticket,
            user=request.user,
            tenant=ticket.tenant,
        )
        return Response(
            TimeEntrySerializer(entry).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], url_path="time-summary")
    def time_summary(self, request, pk=None):
        """
        GET /api/v1/tickets/tickets/{id}/time-summary/

        Returns aggregated time tracking data for this ticket.
        """
        from django.db.models import Sum, Count, Q

        ticket = self.get_object()
        entries = TimeEntry.objects.filter(ticket=ticket)

        total = entries.aggregate(
            total_minutes=Sum("duration_minutes"),
            billable_minutes=Sum(
                "duration_minutes",
                filter=Q(is_billable=True),
            ),
            entry_count=Count("id"),
        )

        # Per-user breakdown
        by_user_qs = (
            entries.values("user__id", "user__first_name", "user__last_name", "user__email")
            .annotate(
                minutes=Sum("duration_minutes"),
                entries=Count("id"),
            )
            .order_by("-minutes")
        )
        by_user = [
            {
                "user_id": str(row["user__id"]),
                "user_name": f"{row['user__first_name']} {row['user__last_name']}".strip()
                             or row["user__email"],
                "minutes": row["minutes"] or 0,
                "entries": row["entries"],
            }
            for row in by_user_qs
        ]

        return Response({
            "total_minutes": total["total_minutes"] or 0,
            "billable_minutes": total["billable_minutes"] or 0,
            "entry_count": total["entry_count"] or 0,
            "by_user": by_user,
        })

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"time-entries/(?P<entry_id>[0-9a-f-]+)",
    )
    def time_entry_detail(self, request, pk=None, entry_id=None):
        """Update or delete a time entry. Only the creator can modify."""
        ticket = self.get_object()
        try:
            entry = TimeEntry.objects.get(pk=entry_id, ticket=ticket)
        except TimeEntry.DoesNotExist:
            return Response(
                {"detail": "Time entry not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Only creator or Manager+ can modify
        if entry.user_id != request.user.pk:
            from apps.accounts.permissions import _get_membership
            tenant = getattr(request, "tenant", None)
            membership = _get_membership(request, tenant) if tenant else None
            if not membership or membership.role.hierarchy_level > 20:
                return Response(
                    {"detail": "You can only modify your own time entries."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        if request.method == "DELETE":
            entry.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH
        serializer = TimeEntrySerializer(entry, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # ------------------------------------------------------------------
    # Soft delete
    # ------------------------------------------------------------------

    def perform_destroy(self, instance):
        """Soft-delete instead of hard-delete."""
        from django.utils import timezone as tz
        instance.is_deleted = True
        instance.deleted_at = tz.now()
        instance.deleted_by = self.request.user
        instance.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        """Restore a soft-deleted ticket."""
        ticket = self.get_object()
        if not ticket.is_deleted:
            return Response(
                {"detail": "Ticket is not deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ticket.is_deleted = False
        ticket.deleted_at = None
        ticket.deleted_by = None
        ticket.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])

        serializer = TicketDetailSerializer(ticket, context={"request": request})
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# CannedResponse
# ---------------------------------------------------------------------------


class CannedResponseViewSet(ModelViewSet):
    """
    CRUD for canned response templates.

    Agents see shared responses plus their own personal ones.
    """

    serializer_class = CannedResponseSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ["title", "content", "shortcut"]
    ordering_fields = ["title", "category", "usage_count", "created_at"]
    ordering = ["category", "title"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CannedResponse.objects.none()
        from django.db.models import Q

        return CannedResponse.objects.select_related("created_by").filter(
            Q(is_shared=True) | Q(created_by=self.request.user)
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        # Only the creator or a manager+ can edit shared canned responses.
        instance = serializer.instance
        if instance.is_shared and instance.created_by_id != self.request.user.pk:
            from apps.accounts.permissions import _get_membership

            tenant = getattr(self.request, "tenant", None)
            membership = _get_membership(self.request, tenant) if tenant else None
            if not membership or membership.role.hierarchy_level > 20:
                from rest_framework.exceptions import PermissionDenied

                raise PermissionDenied(
                    "Only the creator or a manager can edit shared canned responses."
                )
        serializer.save()

    def perform_destroy(self, instance):
        if instance.is_shared and instance.created_by_id != self.request.user.pk:
            from apps.accounts.permissions import _get_membership

            tenant = getattr(self.request, "tenant", None)
            membership = _get_membership(self.request, tenant) if tenant else None
            if not membership or membership.role.hierarchy_level > 20:
                from rest_framework.exceptions import PermissionDenied

                raise PermissionDenied(
                    "Only the creator or a manager can delete shared canned responses."
                )
        instance.delete()

    @action(detail=True, methods=["post"])
    def render(self, request, pk=None):
        """
        Render a canned response with template variables for the given ticket.

        POST /api/v1/tickets/canned-responses/{id}/render/
        {"ticket_id": "<uuid>"}
        """
        response_obj = self.get_object()
        ticket_id = request.data.get("ticket_id")
        if not ticket_id:
            return Response(
                {"error": "ticket_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            ticket = Ticket.objects.select_related("contact").get(id=ticket_id)
        except Ticket.DoesNotExist:
            return Response(
                {"error": "Ticket not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from apps.tickets.utils import render_canned_response

        content = render_canned_response(response_obj, ticket, request.user)
        return Response({"content": content})


# ---------------------------------------------------------------------------
# Macro
# ---------------------------------------------------------------------------


class MacroViewSet(ModelViewSet):
    """
    CRUD for ticket macros — reusable body templates with optional actions.

    Agents see shared macros plus their own personal ones.
    """

    permission_classes = [IsAuthenticated]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_serializer_class(self):
        from apps.tickets.serializers import MacroSerializer
        return MacroSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from apps.tickets.models import Macro
            return Macro.objects.none()
        from django.db.models import Q
        from apps.tickets.models import Macro
        return Macro.objects.select_related("created_by").filter(
            Q(is_shared=True) | Q(created_by=self.request.user)
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.created_by_id != self.request.user.pk:
            from apps.accounts.permissions import _get_membership
            tenant = getattr(self.request, "tenant", None)
            membership = _get_membership(self.request, tenant) if tenant else None
            if not membership or membership.role.hierarchy_level > 20:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    "Only the creator or a manager can edit this macro."
                )
        serializer.save()

    def perform_destroy(self, instance):
        if instance.created_by_id != self.request.user.pk:
            from apps.accounts.permissions import _get_membership
            tenant = getattr(self.request, "tenant", None)
            membership = _get_membership(self.request, tenant) if tenant else None
            if not membership or membership.role.hierarchy_level > 20:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    "Only the creator or a manager can delete this macro."
                )
        instance.delete()


# ---------------------------------------------------------------------------
# SavedView
# ---------------------------------------------------------------------------


class SavedViewViewSet(ModelViewSet):
    """
    CRUD for saved filter views (tickets / contacts).

    Users see shared views (user=NULL) plus their own personal views.
    """

    serializer_class = SavedViewSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ["name"]
    ordering = ["-is_pinned", "name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SavedView.objects.none()
        from django.db.models import Q

        qs = SavedView.objects.filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        )
        resource_type = self.request.query_params.get("resource_type")
        if resource_type:
            qs = qs.filter(resource_type=resource_type)
        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, pk=None):
        """Set this view as the default for its resource type."""
        from django.db import transaction

        view = self.get_object()
        with transaction.atomic():
            # Unset other defaults for same user + resource_type
            SavedView.objects.select_for_update().filter(
                user=request.user,
                resource_type=view.resource_type,
                is_default=True,
            ).update(is_default=False)
            view.is_default = True
            view.save(update_fields=["is_default", "updated_at"])
        return Response({"status": "default set"})


# ---------------------------------------------------------------------------
# BusinessHours (singleton per tenant)
# ---------------------------------------------------------------------------


class BusinessHoursViewSet(viewsets.GenericViewSet):
    """
    Business hours configuration for the current tenant.

    Singleton resource: only **retrieve** and **partial_update** are exposed.
    A ``BusinessHours`` row is auto-created on first access if missing.
    """

    serializer_class = BusinessHoursSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "settings"

    def get_permissions(self):
        if self.action == "retrieve":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_object(self):
        tenant = self.request.tenant
        obj, _created = BusinessHours.objects.get_or_create(tenant=tenant)
        return obj

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return BusinessHours.objects.none()
        return BusinessHours.objects.filter(tenant=self.request.tenant)

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


# ---------------------------------------------------------------------------
# PublicHoliday
# ---------------------------------------------------------------------------


class PublicHolidayViewSet(ModelViewSet):
    """CRUD for tenant public holidays."""

    serializer_class = PublicHolidaySerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "settings"
    search_fields = ["name"]
    ordering_fields = ["date", "name", "created_at"]
    ordering = ["date"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PublicHoliday.objects.none()
        return PublicHoliday.objects.all()


# ---------------------------------------------------------------------------
# CSAT public endpoint (no auth required)
# ---------------------------------------------------------------------------


class CSATSubmitView(viewsets.ViewSet):
    """
    Public endpoint for CSAT survey submission.

    Accepts a signed token + rating + optional comment. No authentication
    required — the signed token proves the requester received the email.

    POST /api/v1/tickets/csat/
    {"token": "...", "rating": 4, "comment": "Great support!"}
    """

    authentication_classes = []
    permission_classes = []

    def create(self, request):
        from django.core import signing
        from django.utils import timezone as tz

        from apps.tickets.serializers import CSATSubmitSerializer

        serializer = CSATSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data["token"]
        rating = serializer.validated_data["rating"]
        comment = serializer.validated_data.get("comment", "")

        # Unsign the token (max_age = 12 days covers auto_close_days + buffer)
        try:
            payload = signing.loads(token, salt="csat", max_age=12 * 86400)
        except signing.BadSignature:
            return Response(
                {"detail": "Invalid or expired survey token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket_id = payload.get("t")
        tenant_id = payload.get("n")

        if not ticket_id or not tenant_id:
            return Response(
                {"detail": "Malformed survey token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            ticket = Ticket.unscoped.get(pk=ticket_id, tenant_id=tenant_id)
        except Ticket.DoesNotExist:
            return Response(
                {"detail": "Ticket not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Idempotent: reject if already submitted
        if ticket.csat_rating is not None:
            return Response(
                {"detail": "Survey already submitted for this ticket."},
                status=status.HTTP_409_CONFLICT,
            )

        # Save CSAT response
        now = tz.now()
        Ticket.unscoped.filter(
            pk=ticket.pk,
            csat_rating__isnull=True,
        ).update(
            csat_rating=rating,
            csat_comment=comment,
            csat_submitted_at=now,
            updated_at=now,
        )

        # Log timeline event
        from apps.tickets.models import TicketActivity

        TicketActivity(
            tenant_id=tenant_id,
            ticket=ticket,
            actor=None,
            event=TicketActivity.Event.CSAT_RECEIVED,
            message=f"CSAT received: {rating}/5",
            metadata={"rating": rating, "comment": comment},
        ).save()

        return Response(
            {"detail": "Thank you for your feedback.", "rating": rating},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Ticket Template
# ---------------------------------------------------------------------------


class TicketTemplateViewSet(ModelViewSet):
    """
    CRUD for ticket templates (pre-filled ticket forms).

    All authenticated tenant members can list/retrieve templates.
    Only Manager+ can create, edit, or delete templates.
    """

    serializer_class = TicketTemplateSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "ticket"
    search_fields = ["name", "description"]
    ordering_fields = ["name", "usage_count", "created_at"]
    ordering = ["name"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TicketTemplate.objects.none()
        qs = TicketTemplate.objects.select_related("default_queue", "created_by").all()
        if self.request.query_params.get("active_only") != "false":
            qs = qs.filter(is_active=True)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="use")
    def use(self, request, pk=None):
        """
        Increment usage count and return template data for ticket creation.

        POST /api/v1/tickets/ticket-templates/{id}/use/
        """
        from django.db.models import F

        template = self.get_object()
        TicketTemplate.objects.filter(pk=template.pk).update(
            usage_count=F("usage_count") + 1,
        )
        template.refresh_from_db()
        return Response(TicketTemplateSerializer(template).data)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


class WebhookViewSet(ModelViewSet):
    """
    CRUD for webhook configurations.

    Only Admin/Manager can manage webhooks.
    """

    serializer_class = WebhookSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "settings"
    search_fields = ["name", "url"]
    ordering_fields = ["name", "created_at", "last_triggered_at"]
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Webhook.objects.none()
        return Webhook.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="test")
    def test(self, request, pk=None):
        """
        Send a test webhook delivery.

        POST /api/v1/tickets/webhooks/{id}/test/
        {"event": "ticket.created"}
        """
        webhook = self.get_object()
        serializer = WebhookTestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.tickets.webhook_service import deliver_webhook

        event = serializer.validated_data["event"]
        test_payload = {
            "event": event,
            "test": True,
            "tenant_id": str(request.tenant.pk),
            "data": {"message": "This is a test webhook delivery."},
        }

        success, status_code = deliver_webhook(webhook, test_payload)
        return Response({
            "success": success,
            "status_code": status_code,
        })

    @action(detail=True, methods=["post"], url_path="reset-failures")
    def reset_failures(self, request, pk=None):
        """Reset failure count and re-enable a disabled webhook."""
        webhook = self.get_object()
        webhook.failure_count = 0
        webhook.is_active = True
        webhook.save(update_fields=["failure_count", "is_active", "updated_at"])
        return Response(WebhookSerializer(webhook).data)
