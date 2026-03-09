"""
URL configuration for the comments app.

Registers CommentViewSet and ActivityLogViewSet under the /api/ namespace.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.comments.views import ActivityLogViewSet, CommentViewSet

router = DefaultRouter()
router.register(r"comments", CommentViewSet, basename="comment")
router.register(r"activity-logs", ActivityLogViewSet, basename="activitylog")

app_name = "comments"

urlpatterns = [
    path("", include(router.urls)),
]
