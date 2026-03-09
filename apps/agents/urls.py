"""
URL configuration for the agents app.

Registers the AgentAvailabilityViewSet with the DRF router.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.agents.views import AgentAvailabilityViewSet

app_name = "agents"

router = DefaultRouter()
router.register(r"agents", AgentAvailabilityViewSet, basename="agentavailability")

urlpatterns = [
    path("", include(router.urls)),
]
