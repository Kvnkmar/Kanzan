"""
Management command to run the Asterisk ARI event listener.

Connects to each active tenant's Asterisk instance and listens for
Stasis events. Events are dispatched to the VoIP services layer for
call state updates and WebSocket broadcasting.

Usage:
    python manage.py run_ari_listener

Run as a dedicated PM2 process (see ecosystem.config.js).
"""

import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the Asterisk ARI event listener for real-time call events"

    def handle(self, *args, **options):
        self.stdout.write("Starting ARI event listener...")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Handle graceful shutdown
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: loop.stop())

        try:
            loop.run_until_complete(self._run())
        except KeyboardInterrupt:
            self.stdout.write("ARI listener shutting down...")
        finally:
            loop.close()

    async def _run(self):
        """Connect to all active tenant ARI instances."""
        from apps.voip.ari_client import ARIEventListener
        from apps.voip.models import VoIPSettings
        from apps.voip.tasks import sync_call_state

        settings_qs = VoIPSettings.unscoped.filter(
            is_active=True,
        ).exclude(ari_username="").exclude(ari_password="")

        listeners = []
        for voip_settings in settings_qs:
            listener = ARIEventListener(
                host=voip_settings.asterisk_host,
                port=voip_settings.asterisk_ari_port,
                username=voip_settings.ari_username,
                password=voip_settings.ari_password,
            )

            async def make_handler(tenant_id):
                async def handler(event):
                    channel = event.get("channel", {})
                    channel_id = channel.get("id", "")
                    if channel_id:
                        sync_call_state.delay(channel_id, event)
                return handler

            handler = await make_handler(str(voip_settings.tenant_id))
            listener.on_event(handler)
            listeners.append(listener)

            logger.info(
                "Connecting to Asterisk at %s:%d for tenant %s",
                voip_settings.asterisk_host,
                voip_settings.asterisk_ari_port,
                voip_settings.tenant_id,
            )

        if not listeners:
            logger.warning("No active VoIP settings found. Waiting for configuration...")
            # Keep running and check periodically
            while True:
                await asyncio.sleep(60)
                count = VoIPSettings.unscoped.filter(
                    is_active=True,
                ).exclude(ari_username="").exclude(ari_password="").count()
                if count > 0:
                    logger.info("VoIP settings found, restarting...")
                    return await self._run()

        # Start all listeners concurrently
        await asyncio.gather(*(l.start() for l in listeners))
