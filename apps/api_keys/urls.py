"""URL routes for the API-keys management endpoints."""

from rest_framework.routers import DefaultRouter

from apps.api_keys.views import APIKeyViewSet

app_name = "api_keys"

router = DefaultRouter()
router.register(r"", APIKeyViewSet, basename="api-key")

urlpatterns = router.urls
