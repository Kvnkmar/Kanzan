"""URL routing for the Inbox Hub API.

Mounted from ``main/urls.py`` at ``/api/v1/inbox-hub/``. Phase 1A only
registers the HubEmail viewset; Department / RoutingRule / SLA viewsets
land in later slices when the admin pages need them.
"""

from rest_framework.routers import DefaultRouter

from apps.inbox_hub.views import HubEmailViewSet

app_name = "inbox_hub"

router = DefaultRouter()
router.register(r"hub-emails", HubEmailViewSet, basename="hub-email")

urlpatterns = router.urls
