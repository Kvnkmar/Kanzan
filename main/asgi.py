"""
ASGI config for Kanzen Suite.

Handles both HTTP and WebSocket protocols via Django Channels.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
django.setup()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402

from apps.messaging.routing import websocket_urlpatterns as messaging_ws  # noqa: E402
from apps.notifications.routing import websocket_urlpatterns as notification_ws  # noqa: E402
from apps.tenants.middleware import WebSocketTenantMiddleware  # noqa: E402

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                WebSocketTenantMiddleware(
                    URLRouter(messaging_ws + notification_ws)
                )
            )
        ),
    }
)
