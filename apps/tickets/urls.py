"""
URL configuration for the tickets app.

All endpoints are registered via a DRF DefaultRouter and are intended to be
included under a versioned API prefix (e.g. ``/api/v1/tickets/``).
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.tickets.views import (
    BusinessHoursViewSet,
    CannedResponseViewSet,
    CSATSubmitView,
    EscalationRuleViewSet,
    MacroViewSet,
    PublicHolidayViewSet,
    QueueViewSet,
    SavedViewViewSet,
    SLAPolicyViewSet,
    TicketCategoryViewSet,
    TicketStatusViewSet,
    TicketViewSet,
)

app_name = "tickets"

router = DefaultRouter()
router.register(r"tickets", TicketViewSet, basename="ticket")
router.register(r"ticket-statuses", TicketStatusViewSet, basename="ticketstatus")
router.register(r"queues", QueueViewSet, basename="queue")
router.register(r"sla-policies", SLAPolicyViewSet, basename="slapolicy")
router.register(r"escalation-rules", EscalationRuleViewSet, basename="escalationrule")
router.register(r"ticket-categories", TicketCategoryViewSet, basename="ticketcategory")
router.register(r"canned-responses", CannedResponseViewSet, basename="canned-response")
router.register(r"saved-views", SavedViewViewSet, basename="saved-view")
router.register(r"macros", MacroViewSet, basename="macro")
router.register(r"public-holidays", PublicHolidayViewSet, basename="publicholiday")

urlpatterns = [
    path("", include(router.urls)),
    # BusinessHours singleton (no ID routing)
    path(
        "business-hours/",
        BusinessHoursViewSet.as_view({"get": "retrieve", "patch": "partial_update"}),
        name="business-hours",
    ),
    # Public CSAT submission (no auth required)
    path(
        "csat/",
        CSATSubmitView.as_view({"post": "create"}),
        name="csat-submit",
    ),
]
