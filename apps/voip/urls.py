"""
URL configuration for the VoIP app.

Registers ViewSets with the DRF router and standalone API views
for call control, SIP credentials, and recording downloads.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.voip.views import (
    CallHangupView,
    CallHoldView,
    CallLogViewSet,
    CallQueueViewSet,
    CallRecordingDownloadView,
    CallTransferView,
    ExtensionViewSet,
    InitiateCallView,
    SIPCredentialsView,
    VoIPSettingsViewSet,
)

app_name = "voip"

router = DefaultRouter()
router.register(r"settings", VoIPSettingsViewSet, basename="voip-settings")
router.register(r"extensions", ExtensionViewSet, basename="extension")
router.register(r"calls", CallLogViewSet, basename="call-log")
router.register(r"queues", CallQueueViewSet, basename="call-queue")

urlpatterns = [
    path("", include(router.urls)),
    path("sip-credentials/", SIPCredentialsView.as_view(), name="sip-credentials"),
    path("calls/initiate/", InitiateCallView.as_view(), name="call-initiate"),
    path("calls/<uuid:pk>/hold/", CallHoldView.as_view(), name="call-hold"),
    path("calls/<uuid:pk>/transfer/", CallTransferView.as_view(), name="call-transfer"),
    path("calls/<uuid:pk>/hangup/", CallHangupView.as_view(), name="call-hangup"),
    path(
        "recordings/<uuid:pk>/",
        CallRecordingDownloadView.as_view(),
        name="recording-download",
    ),
]
