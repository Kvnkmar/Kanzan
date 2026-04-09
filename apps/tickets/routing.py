"""
Django Channels WebSocket URL routing for the tickets app.

Include these patterns in the project-level ASGI routing configuration::

    from apps.tickets.routing import websocket_urlpatterns as ticket_ws
"""

from django.urls import re_path

from apps.tickets.consumers import TicketPresenceConsumer

websocket_urlpatterns = [
    re_path(
        r"ws/tickets/(?P<ticket_id>[a-f0-9-]+)/presence/$",
        TicketPresenceConsumer.as_asgi(),
    ),
]
