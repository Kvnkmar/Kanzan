from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.inbound_email.api_views import InboundEmailViewSet, InboxViewSet

app_name = "inbound_email_api"

router = DefaultRouter()
router.register(r"", InboundEmailViewSet, basename="inbound-email")

urlpatterns = [
    # Agent inbox endpoints (must be before router to avoid pk conflict)
    path("inbox/", InboxViewSet.as_view({"get": "list"}), name="inbox-list"),
    path(
        "inbox/<uuid:pk>/link/",
        InboxViewSet.as_view({"post": "link"}),
        name="inbox-link",
    ),
    path(
        "inbox/<uuid:pk>/action/",
        InboxViewSet.as_view({"post": "take_action"}),
        name="inbox-action",
    ),
    path(
        "inbox/<uuid:pk>/ignore/",
        InboxViewSet.as_view({"post": "ignore"}),
        name="inbox-ignore",
    ),
    path("", include(router.urls)),
]
