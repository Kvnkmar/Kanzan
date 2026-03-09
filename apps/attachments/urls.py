"""
URL configuration for the attachments app.

Registers AttachmentViewSet under the /api/ namespace.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.attachments.views import AttachmentViewSet

router = DefaultRouter()
router.register(r"attachments", AttachmentViewSet, basename="attachment")

app_name = "attachments"

urlpatterns = [
    path("", include(router.urls)),
]
