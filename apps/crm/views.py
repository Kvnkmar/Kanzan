"""
CRM API views for activity management, agent task queues, reminder management,
and pipeline forecast.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsTenantAdminOrManager, IsTenantMember
from apps.crm.models import Activity, Reminder
from apps.crm.serializers import (
    ActivitySerializer,
    ReminderBulkActionSerializer,
    ReminderCreateSerializer,
    ReminderRescheduleSerializer,
    ReminderSerializer,
)

User = get_user_model()


class ActivityViewSet(viewsets.ModelViewSet):
    """
    CRUD for CRM activities (calls, emails, meetings, tasks).

    Filters:
        - ticket: UUID
        - contact: UUID
        - activity_type: call/email/meeting/task
        - assigned_to: UUID
        - due_at_before: ISO datetime
        - due_at_after: ISO datetime
    """

    serializer_class = ActivitySerializer
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Activity.objects.none()

        qs = Activity.objects.select_related(
            "created_by", "assigned_to", "ticket", "contact",
        )

        # Apply filters
        params = self.request.query_params
        if ticket := params.get("ticket"):
            qs = qs.filter(ticket_id=ticket)
        if contact := params.get("contact"):
            qs = qs.filter(contact_id=contact)
        if activity_type := params.get("activity_type"):
            qs = qs.filter(activity_type=activity_type)
        if assigned_to := params.get("assigned_to"):
            qs = qs.filter(assigned_to_id=assigned_to)
        if due_before := params.get("due_at_before"):
            qs = qs.filter(due_at__lt=due_before)
        if due_after := params.get("due_at_after"):
            qs = qs.filter(due_at__gte=due_after)

        return qs

    def perform_create(self, serializer):
        activity = serializer.save(created_by=self.request.user)
        self._update_ticket_last_activity(activity)

    def perform_update(self, serializer):
        activity = serializer.save()
        self._update_ticket_last_activity(activity)

    def _update_ticket_last_activity(self, activity):
        """Atomically update ticket.last_activity_at if ticket is linked."""
        if activity.ticket_id:
            from apps.tickets.models import Ticket

            Ticket.unscoped.filter(pk=activity.ticket_id).update(
                last_activity_at=timezone.now()
            )

    @action(detail=False, methods=["get"], url_path="my-tasks")
    def my_tasks(self, request):
        """
        Agent task queue: incomplete activities assigned to the requesting
        user, plus overdue follow-up tickets.
        """
        now = timezone.now()
        user = request.user

        # Incomplete activities assigned to this user
        activities = (
            Activity.objects.filter(
                assigned_to=user,
                completed_at__isnull=True,
            )
            .select_related("ticket", "contact", "created_by")
            .order_by("due_at")
        )

        activity_data = ActivitySerializer(activities, many=True).data

        # Overdue follow-up tickets assigned to this user
        from apps.tickets.models import Ticket, TicketStatus

        closed_status_ids = list(
            TicketStatus.objects.filter(is_closed=True).values_list("id", flat=True)
        )

        overdue_tickets = (
            Ticket.objects.filter(
                assignee=user,
                follow_up_due_at__lt=now,
                follow_up_due_at__isnull=False,
            )
            .exclude(status_id__in=closed_status_ids)
            .values(
                "id", "number", "subject", "priority",
                "follow_up_due_at", "last_activity_at",
            )
            .order_by("follow_up_due_at")
        )

        # Overdue reminders assigned to this user
        overdue_reminders = Reminder.objects.overdue().filter(
            assigned_to=user,
        ).select_related("contact", "ticket")
        reminder_data = ReminderSerializer(overdue_reminders, many=True).data

        return Response({
            "activities": activity_data,
            "overdue_followups": list(overdue_tickets),
            "overdue_reminders": reminder_data,
        })


class ReminderViewSet(viewsets.ModelViewSet):
    """
    CRUD for reminders with overdue tracking.

    Filters:
        - assigned_to: UUID
        - contact: UUID
        - ticket: UUID
        - priority: low/medium/high/urgent
        - status: pending/overdue/completed/cancelled
        - mine: true/false (filter to current user's reminders)
        - scheduled_after: ISO datetime
        - scheduled_before: ISO datetime
        - queue: UUID (via linked ticket's queue)
    """

    permission_classes = [IsAuthenticated, IsTenantMember]
    search_fields = ["subject", "notes", "contact__first_name", "contact__last_name"]
    ordering_fields = ["scheduled_at", "priority", "created_at"]
    ordering = ["scheduled_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Reminder.objects.none()

        qs = Reminder.objects.select_related(
            "created_by", "assigned_to", "ticket", "contact",
        )

        # Agent-level row restriction: agents see only their own reminders
        user = self.request.user
        if not user.is_superuser:
            tenant = getattr(self.request, "tenant", None)
            if tenant:
                from apps.accounts.models import TenantMembership

                membership = (
                    TenantMembership.objects.select_related("role")
                    .filter(user=user, tenant=tenant, is_active=True)
                    .first()
                )
                if membership and membership.role.hierarchy_level > 20:
                    qs = qs.filter(
                        Q(assigned_to=user) | Q(created_by=user)
                    )

        params = self.request.query_params

        if assigned_to := params.get("assigned_to"):
            qs = qs.filter(assigned_to_id=assigned_to)
        if contact := params.get("contact"):
            qs = qs.filter(contact_id=contact)
        if ticket := params.get("ticket"):
            qs = qs.filter(ticket_id=ticket)
        if priority := params.get("priority"):
            qs = qs.filter(priority=priority)
        if params.get("mine") == "true":
            qs = qs.filter(assigned_to=self.request.user)
        if scheduled_after := params.get("scheduled_after"):
            qs = qs.filter(scheduled_at__gte=scheduled_after)
        if scheduled_before := params.get("scheduled_before"):
            qs = qs.filter(scheduled_at__lt=scheduled_before)
        if queue := params.get("queue"):
            qs = qs.filter(ticket__queue_id=queue)

        # Status filter (computed, so we apply it in Python-compatible way)
        now = timezone.now()
        status_filter = params.get("status")
        if status_filter == "overdue":
            qs = qs.filter(
                scheduled_at__lt=now,
                completed_at__isnull=True,
                cancelled_at__isnull=True,
            )
        elif status_filter == "pending":
            qs = qs.filter(
                scheduled_at__gte=now,
                completed_at__isnull=True,
                cancelled_at__isnull=True,
            )
        elif status_filter == "completed":
            qs = qs.filter(completed_at__isnull=False)
        elif status_filter == "cancelled":
            qs = qs.filter(cancelled_at__isnull=False)

        return qs

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return ReminderCreateSerializer
        return ReminderSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=["get"], url_path="overdue")
    def overdue(self, request):
        """
        List overdue reminders, ordered by oldest first.

        GET /api/v1/crm/reminders/overdue/
        Supports: mine=true, assigned_to, contact, ticket, priority, queue
        """
        qs = self.get_queryset().overdue().order_by("scheduled_at")

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = ReminderSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = ReminderSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        """
        Return reminder statistics for the dashboard.

        GET /api/v1/crm/reminders/stats/
        """
        now = timezone.now()
        base_qs = self.get_queryset()

        overdue_qs = base_qs.overdue(now)
        total_overdue = overdue_qs.count()

        # Overdue by assignee
        by_assignee = list(
            overdue_qs.values(
                "assigned_to", "assigned_to__first_name", "assigned_to__last_name"
            )
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # Overdue by priority
        by_priority = list(
            overdue_qs.values("priority")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # Completed today
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        completed_today = base_qs.filter(
            completed_at__gte=today_start,
        ).count()

        # Average overdue duration (computed in Python for SQLite compat)
        avg_overdue_seconds = None
        if total_overdue > 0:
            scheduled_times = list(
                overdue_qs.values_list("scheduled_at", flat=True)
            )
            total_secs = sum(
                (now - s).total_seconds() for s in scheduled_times
            )
            avg_overdue_seconds = int(total_secs / len(scheduled_times))

        return Response({
            "total_overdue": total_overdue,
            "completed_today": completed_today,
            "by_assignee": [
                {
                    "assigned_to": str(item["assigned_to"]) if item["assigned_to"] else None,
                    "name": f"{item['assigned_to__first_name'] or ''} {item['assigned_to__last_name'] or ''}".strip() or "Unassigned",
                    "count": item["count"],
                }
                for item in by_assignee
            ],
            "by_priority": [
                {"priority": item["priority"], "count": item["count"]}
                for item in by_priority
            ],
            "avg_overdue_seconds": avg_overdue_seconds,
        })

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        """
        Mark a reminder as completed.

        POST /api/v1/crm/reminders/{id}/complete/
        """
        reminder = self.get_object()
        if reminder.completed_at is not None:
            return Response(
                {"detail": "Reminder is already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if reminder.cancelled_at is not None:
            return Response(
                {"detail": "Cannot complete a cancelled reminder."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reminder.mark_completed()
        return Response(ReminderSerializer(reminder).data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """
        Cancel a reminder.

        POST /api/v1/crm/reminders/{id}/cancel/
        """
        reminder = self.get_object()
        if reminder.completed_at is not None:
            return Response(
                {"detail": "Cannot cancel a completed reminder."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if reminder.cancelled_at is not None:
            return Response(
                {"detail": "Reminder is already cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reminder.mark_cancelled()
        return Response(ReminderSerializer(reminder).data)

    @action(detail=True, methods=["post"], url_path="reschedule")
    def reschedule(self, request, pk=None):
        """
        Reschedule a reminder to a new datetime.

        POST /api/v1/crm/reminders/{id}/reschedule/
        {"scheduled_at": "2025-01-15T10:00:00Z", "note": "Client unavailable"}
        """
        reminder = self.get_object()
        if reminder.completed_at is not None:
            return Response(
                {"detail": "Cannot reschedule a completed reminder."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReminderRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reminder.reschedule(
            new_scheduled_at=serializer.validated_data["scheduled_at"],
            note=serializer.validated_data.get("note", ""),
        )
        return Response(ReminderSerializer(reminder).data)

    @action(detail=False, methods=["post"], url_path="bulk-action")
    def bulk_action(self, request):
        """
        Perform bulk actions on reminders.

        POST /api/v1/crm/reminders/bulk-action/
        {
            "action": "complete|reschedule|reassign|cancel",
            "reminder_ids": ["uuid1", ...],
            "scheduled_at": "...",   // required for reschedule
            "assigned_to": "uuid",   // required for reassign
            "note": "..."            // optional
        }
        """
        serializer = ReminderBulkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        action_name = data["action"]
        reminder_ids = data["reminder_ids"]
        now = timezone.now()

        reminders = Reminder.objects.filter(id__in=reminder_ids)
        found_count = reminders.count()
        if found_count != len(reminder_ids):
            return Response(
                {"detail": "Some reminders not found or access denied."},
                status=status.HTTP_404_NOT_FOUND,
            )

        updated = 0

        if action_name == "complete":
            updated = reminders.filter(
                completed_at__isnull=True,
                cancelled_at__isnull=True,
            ).update(completed_at=now, updated_at=now)

        elif action_name == "cancel":
            updated = reminders.filter(
                completed_at__isnull=True,
                cancelled_at__isnull=True,
            ).update(cancelled_at=now, updated_at=now)

        elif action_name == "reschedule":
            new_time = data["scheduled_at"]
            updated = reminders.filter(
                completed_at__isnull=True,
            ).update(scheduled_at=new_time, updated_at=now)

        elif action_name == "reassign":
            new_user = User.objects.filter(pk=data["assigned_to"]).first()
            if not new_user:
                return Response(
                    {"detail": "Target user not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            updated = reminders.update(assigned_to=new_user, updated_at=now)

        return Response({
            "success": True,
            "action": action_name,
            "updated": updated,
        })


class PipelineForecastView(RetrieveAPIView):
    """
    GET /api/v1/crm/pipeline/{pipeline_id}/forecast/

    Returns weighted revenue forecast per pipeline stage.
    Admin and Manager only.
    """

    permission_classes = [IsAuthenticated, IsTenantAdminOrManager]

    def retrieve(self, request, *args, **kwargs):
        from apps.tickets.models import Pipeline, PipelineStage, Ticket, TicketStatus

        pipeline_id = kwargs["pipeline_id"]

        pipeline = Pipeline.objects.filter(pk=pipeline_id).first()
        if not pipeline:
            return Response({"detail": "Pipeline not found."}, status=404)

        closed_status_ids = set(
            TicketStatus.objects.filter(is_closed=True).values_list("id", flat=True)
        )

        stages = PipelineStage.unscoped.filter(
            pipeline=pipeline,
        ).order_by("order")

        stage_data = []
        total_weighted = Decimal("0.00")

        for stage in stages:
            deals = Ticket.objects.filter(
                ticket_type="deal",
                pipeline_stage=stage,
            ).exclude(status_id__in=closed_status_ids)

            agg = deals.aggregate(
                ticket_count=Count("id"),
                total_value=Sum("deal_value"),
            )

            ticket_count = agg["ticket_count"] or 0
            total_value = agg["total_value"] or Decimal("0.00")

            # weighted_value = sum(deal_value * probability / 100) per deal
            weighted_value = Decimal("0.00")
            if ticket_count > 0:
                for deal in deals.values_list("deal_value", "probability"):
                    dv = deal[0] or Decimal("0.00")
                    prob = deal[1] if deal[1] is not None else stage.probability
                    weighted_value += dv * Decimal(prob) / Decimal("100")

            avg_probability = stage.probability
            if ticket_count > 0:
                probs = [
                    d[0] if d[0] is not None else stage.probability
                    for d in deals.values_list("probability")
                ]
                avg_probability = int(sum(probs) / len(probs))

            total_weighted += weighted_value

            stage_data.append({
                "stage": stage.name,
                "ticket_count": ticket_count,
                "total_value": float(total_value),
                "weighted_value": float(weighted_value),
                "avg_probability": avg_probability,
            })

        return Response({
            "pipeline": pipeline.name,
            "stages": stage_data,
            "total_weighted_forecast": float(total_weighted),
        })
