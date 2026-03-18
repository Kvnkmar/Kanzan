from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.inbound_email.api_views import InboundEmailViewSet

app_name = "inbound_email_api"

router = DefaultRouter()
router.register(r"", InboundEmailViewSet, basename="inbound-email")

urlpatterns = [
    path("", include(router.urls)),
]
