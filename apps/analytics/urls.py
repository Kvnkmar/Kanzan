"""
URL configuration for the analytics app.

Registers all analytics-related ViewSets with the DRF router and includes
the dashboard aggregation endpoint.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.analytics.views import (
    CalendarEventViewSet,
    DashboardView,
    DashboardWidgetViewSet,
    ExportJobViewSet,
    ReportDefinitionViewSet,
)

app_name = "analytics"

router = DefaultRouter()
router.register(r"reports", ReportDefinitionViewSet, basename="reportdefinition")
router.register(r"widgets", DashboardWidgetViewSet, basename="dashboardwidget")
router.register(r"exports", ExportJobViewSet, basename="exportjob")
router.register(r"calendar-events", CalendarEventViewSet, basename="calendarevent")

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("", include(router.urls)),
]
