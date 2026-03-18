"""
URL configuration for the tickets app.

All endpoints are registered via a DRF DefaultRouter and are intended to be
included under a versioned API prefix (e.g. ``/api/v1/tickets/``).
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.tickets.views import (
    CannedResponseViewSet,
    EscalationRuleViewSet,
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

urlpatterns = [
    path("", include(router.urls)),
]
