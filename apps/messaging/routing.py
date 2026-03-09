"""
Django Channels WebSocket URL routing for the messaging app.

Include these patterns in the project-level ASGI routing configuration::

    from apps.messaging.routing import websocket_urlpatterns as messaging_ws
"""

from django.urls import re_path

from apps.messaging.consumers import ChatConsumer

websocket_urlpatterns = [
    re_path(
        r"ws/messaging/(?P<conversation_id>[a-f0-9-]+)/$",
        ChatConsumer.as_asgi(),
    ),
]
