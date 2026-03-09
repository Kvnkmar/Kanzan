"""
WebSocket URL routing for the notifications app.

Include these patterns in the project-level ASGI routing configuration::

    from apps.notifications.routing import websocket_urlpatterns as notification_ws
    websocket_urlpatterns += notification_ws
"""

from django.urls import re_path

from apps.notifications.consumers import NotificationConsumer

websocket_urlpatterns = [
    re_path(r"ws/notifications/$", NotificationConsumer.as_asgi()),
]
