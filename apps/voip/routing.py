"""WebSocket URL routing for the VoIP app."""

from django.urls import re_path

from apps.voip.consumers import CallEventConsumer

websocket_urlpatterns = [
    re_path(r"ws/voip/events/$", CallEventConsumer.as_asgi()),
]
