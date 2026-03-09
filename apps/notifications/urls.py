"""
URL configuration for the notifications app.

Include in the project root URL conf::

    path("api/v1/notifications/", include("apps.notifications.urls")),
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.notifications.views import (
    NotificationPreferenceViewSet,
    NotificationViewSet,
)

router = DefaultRouter()
router.register(r"notifications", NotificationViewSet, basename="notification")
router.register(
    r"notification-preferences",
    NotificationPreferenceViewSet,
    basename="notification-preference",
)

app_name = "notifications"

urlpatterns = [
    path("", include(router.urls)),
]
