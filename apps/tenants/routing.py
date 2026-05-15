"""WebSocket routing for tenant-scoped live events."""

from django.urls import re_path

from apps.tenants.consumers import LiveEventConsumer

websocket_urlpatterns = [
    re_path(r"ws/live/$", LiveEventConsumer.as_asgi()),
]
