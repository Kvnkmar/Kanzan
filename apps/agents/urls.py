"""
URL configuration for the agents app.

Registers the AgentAvailabilityViewSet with the DRF router.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.agents.views import AgentAvailabilityViewSet, CustomAgentStatusViewSet

app_name = "agents"

router = DefaultRouter()
router.register(r"agents", AgentAvailabilityViewSet, basename="agentavailability")
router.register(
    r"custom-statuses", CustomAgentStatusViewSet, basename="customagentstatus",
)

urlpatterns = [
    path("", include(router.urls)),
]
