"""
DRF ViewSets and views for the analytics app.

Provides CRUD endpoints for report definitions, dashboard widgets, and
export jobs, plus a dashboard view that returns aggregated tenant statistics.
"""

import logging
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics.models import CalendarEvent, DashboardWidget, ExportJob, ReportDefinition
from apps.analytics.serializers import (
    CalendarEventSerializer,
    DashboardWidgetSerializer,
    ExportJobCreateSerializer,
    ExportJobSerializer,
    ReportDefinitionSerializer,
)
from apps.accounts.permissions import HasTenantPermission, IsTenantAdminOrManager
from apps.analytics.services import (
    get_agent_performance,
    get_sla_compliance,
    get_ticket_stats,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ReportDefinition
# ---------------------------------------------------------------------------


class ReportDefinitionViewSet(viewsets.ModelViewSet):
    """Full CRUD for tenant-scoped report definitions."""

    serializer_class = ReportDefinitionSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "report"
    search_fields = ["name"]
    ordering_fields = ["name", "report_type", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return ReportDefinition.objects.select_related("created_by").all()

    def perform_create(self, serializer):
        serializer.save()


# ---------------------------------------------------------------------------
# DashboardWidget
# ---------------------------------------------------------------------------


class DashboardWidgetViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for dashboard widgets.

    Widgets with a ``user`` value are personal; widgets without are shared
    across the entire tenant.
    """

    serializer_class = DashboardWidgetSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "dashboard"
    search_fields = ["title"]
    ordering_fields = ["title", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return DashboardWidget.objects.select_related("user").all()

    def perform_create(self, serializer):
        serializer.save()


# ---------------------------------------------------------------------------
# ExportJob
# ---------------------------------------------------------------------------


class ExportJobViewSet(viewsets.ModelViewSet):
    """
    Create and list export jobs.

    Creating an export job triggers an asynchronous Celery task to generate
    the export file. List and detail endpoints show the current job status
    and provide the download URL once completed.
    """

    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "export"
    search_fields = ["resource_type"]
    ordering_fields = ["created_at", "status", "completed_at"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return ExportJob.objects.select_related("report", "requested_by").all()

    def get_serializer_class(self):
        if self.action == "create":
            return ExportJobCreateSerializer
        return ExportJobSerializer

    def perform_create(self, serializer):
        serializer.save()


# ---------------------------------------------------------------------------
# Dashboard (aggregated stats)
# ---------------------------------------------------------------------------


class CalendarEventViewSet(viewsets.ModelViewSet):
    """Full CRUD for tenant-scoped calendar events."""

    serializer_class = CalendarEventSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "calendar_event"
    search_fields = ["title"]
    ordering_fields = ["event_date", "event_time", "created_at"]
    ordering = ["event_date", "event_time"]
    filterset_fields = ["event_type", "event_date"]

    def get_queryset(self):
        qs = CalendarEvent.objects.select_related("created_by").all()
        # Filter by date range if provided
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(event_date__gte=date_from)
        if date_to:
            qs = qs.filter(event_date__lte=date_to)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class DashboardView(APIView):
    """
    Returns aggregated statistics for the tenant dashboard.

    Accepts optional query parameters:
        - date_from: ISO 8601 datetime string
        - date_to: ISO 8601 datetime string

    Returns:
        - ticket_stats: open/closed counts, averages, breakdowns
        - agent_performance: per-agent metrics
        - sla_compliance: per-policy compliance rates
    """

    permission_classes = [IsAuthenticated, IsTenantAdminOrManager]

    def get(self, request):
        tenant = request.tenant
        date_from = self._parse_date(request.query_params.get("date_from"))
        date_to = self._parse_date(request.query_params.get("date_to"))

        ticket_stats = get_ticket_stats(tenant, date_from, date_to, user=request.user)
        agent_performance = get_agent_performance(tenant, date_from, date_to)
        sla_compliance = get_sla_compliance(tenant, date_from, date_to)

        return Response(
            {
                "ticket_stats": ticket_stats,
                "agent_performance": agent_performance,
                "sla_compliance": sla_compliance,
            },
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _parse_date(value):
        """Parse an ISO 8601 datetime string, returning None on failure."""
        if not value:
            return None
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed
        # Attempt date-only format.
        try:
            return timezone.make_aware(datetime.fromisoformat(value))
        except (ValueError, TypeError):
            return None
