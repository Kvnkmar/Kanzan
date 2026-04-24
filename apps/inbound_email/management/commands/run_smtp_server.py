"""
Run the internal SMTP server that receives inbound email for all tenants.

Exposes an aiosmtpd server bound to SMTP_SERVER_HOST:SMTP_SERVER_PORT.
Messages accepted here are handed to the same processing pipeline the
provider webhooks use (InboundEmail + Celery).

Usage:
    python manage.py run_smtp_server
    python manage.py run_smtp_server --host 0.0.0.0 --port 2525

Run as a dedicated PM2 process (see ecosystem.config.js:kanzan-smtp).
"""

import logging
import signal
import ssl
import time

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the in-process SMTP server for inbound email delivery."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=None, help="Bind host (default: SMTP_SERVER_HOST)")
        parser.add_argument("--port", type=int, default=None, help="Bind port (default: SMTP_SERVER_PORT)")

    def handle(self, *args, **options):
        from aiosmtpd.controller import Controller

        from apps.inbound_email.smtp_server import (
            KanzanSMTPHandler,
            StaticAuthenticator,
        )

        host = options["host"] or getattr(settings, "SMTP_SERVER_HOST", "0.0.0.0")
        port = options["port"] or getattr(settings, "SMTP_SERVER_PORT", 2525)
        server_hostname = getattr(
            settings, "SMTP_SERVER_HOSTNAME", getattr(settings, "BASE_DOMAIN", "localhost"),
        )

        ssl_context = self._build_ssl_context()

        auth_users = getattr(settings, "SMTP_SERVER_AUTH_USERS", {}) or {}
        require_auth = bool(getattr(settings, "SMTP_SERVER_REQUIRE_AUTH", False))

        smtp_params = {}
        if auth_users:
            smtp_params["authenticator"] = StaticAuthenticator(auth_users)
            smtp_params["auth_required"] = require_auth
            smtp_params["auth_require_tls"] = ssl_context is not None

        controller = Controller(
            handler=KanzanSMTPHandler(),
            hostname=host,
            port=port,
            server_hostname=server_hostname,
            ssl_context=ssl_context,
            **smtp_params,
        )

        controller.start()
        self.stdout.write(self.style.SUCCESS(
            f"Kanzen SMTP server listening on {host}:{port} "
            f"(hostname={server_hostname}, tls={bool(ssl_context)}, "
            f"auth={'required' if require_auth else 'optional' if auth_users else 'disabled'})"
        ))
        logger.info("Kanzen SMTP server started on %s:%d", host, port)

        stop = {"flag": False}

        def _sig_handler(signum, frame):
            stop["flag"] = True

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        try:
            while not stop["flag"]:
                time.sleep(1)
        finally:
            controller.stop()
            self.stdout.write(self.style.WARNING("Kanzen SMTP server stopped"))
            logger.info("Kanzen SMTP server stopped")

    def _build_ssl_context(self):
        cert = getattr(settings, "SMTP_SERVER_TLS_CERT_FILE", "")
        key = getattr(settings, "SMTP_SERVER_TLS_KEY_FILE", "")
        if not (cert and key):
            return None
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile=cert, keyfile=key)
        return context
