"""
Asterisk ARI (Asterisk REST Interface) client.

Provides both HTTP operations for channel control and a WebSocket listener
for real-time Stasis events. The ARI listener runs as a dedicated process
via the ``run_ari_listener`` management command.
"""

import asyncio
import json
import logging
from base64 import b64encode

import httpx
import websockets

logger = logging.getLogger(__name__)


class ARIClient:
    """
    HTTP client for Asterisk ARI channel operations.

    Uses httpx for async HTTP requests to the ARI REST API.
    """

    def __init__(self, host, port, username, password, use_ssl=False):
        scheme = "https" if use_ssl else "http"
        self.base_url = f"{scheme}://{host}:{port}/ari"
        self._auth = (username, password)
        self._client = None

    async def _get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=self._auth,
                timeout=10.0,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Channel operations
    # ------------------------------------------------------------------

    async def originate(
        self,
        endpoint,
        extension=None,
        context=None,
        caller_id=None,
        app="kanzan-voip",
        app_args=None,
        variables=None,
    ):
        """
        Originate a new channel.

        Args:
            endpoint: SIP endpoint (e.g., "PJSIP/1001")
            extension: Dialplan extension to connect to
            context: Dialplan context
            caller_id: Caller ID string
            app: Stasis application name
            app_args: Comma-separated Stasis app arguments
            variables: Dict of channel variables
        """
        client = await self._get_client()
        data = {
            "endpoint": endpoint,
            "app": app,
        }
        if extension:
            data["extension"] = extension
        if context:
            data["context"] = context
        if caller_id:
            data["callerId"] = caller_id
        if app_args:
            data["appArgs"] = app_args
        if variables:
            data["variables"] = {"variables": variables}

        try:
            response = await client.post("/channels", json=data)
            response.raise_for_status()
            return response.json(), None
        except httpx.HTTPStatusError as e:
            logger.error("ARI originate failed: %s %s", e.response.status_code, e.response.text)
            return None, f"ARI error: {e.response.status_code}"
        except httpx.RequestError as e:
            logger.error("ARI originate connection error: %s", e)
            return None, f"Connection error: {e}"

    async def hangup(self, channel_id, reason="normal"):
        """Hang up a channel."""
        client = await self._get_client()
        try:
            response = await client.delete(
                f"/channels/{channel_id}",
                params={"reason_code": reason},
            )
            response.raise_for_status()
            return True, None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI hangup failed for %s: %s", channel_id, e)
            return False, str(e)

    async def hold(self, channel_id):
        """Place a channel on hold."""
        client = await self._get_client()
        try:
            response = await client.post(f"/channels/{channel_id}/hold")
            response.raise_for_status()
            return True, None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI hold failed for %s: %s", channel_id, e)
            return False, str(e)

    async def unhold(self, channel_id):
        """Resume a held channel."""
        client = await self._get_client()
        try:
            response = await client.delete(f"/channels/{channel_id}/hold")
            response.raise_for_status()
            return True, None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI unhold failed for %s: %s", channel_id, e)
            return False, str(e)

    async def mute(self, channel_id, direction="both"):
        """Mute a channel."""
        client = await self._get_client()
        try:
            response = await client.post(
                f"/channels/{channel_id}/mute",
                params={"direction": direction},
            )
            response.raise_for_status()
            return True, None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI mute failed for %s: %s", channel_id, e)
            return False, str(e)

    async def unmute(self, channel_id, direction="both"):
        """Unmute a channel."""
        client = await self._get_client()
        try:
            response = await client.delete(
                f"/channels/{channel_id}/mute",
                params={"direction": direction},
            )
            response.raise_for_status()
            return True, None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI unmute failed for %s: %s", channel_id, e)
            return False, str(e)

    async def redirect(self, channel_id, endpoint):
        """Redirect (blind transfer) a channel to another endpoint."""
        client = await self._get_client()
        try:
            response = await client.post(
                f"/channels/{channel_id}/redirect",
                params={"endpoint": endpoint},
            )
            response.raise_for_status()
            return True, None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI redirect failed for %s: %s", channel_id, e)
            return False, str(e)

    # ------------------------------------------------------------------
    # Bridge operations (for attended transfers)
    # ------------------------------------------------------------------

    async def create_bridge(self, bridge_type="mixing"):
        """Create a new bridge for connecting channels."""
        client = await self._get_client()
        try:
            response = await client.post(
                "/bridges",
                json={"type": bridge_type},
            )
            response.raise_for_status()
            return response.json(), None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI create bridge failed: %s", e)
            return None, str(e)

    async def add_channel_to_bridge(self, bridge_id, channel_id):
        """Add a channel to an existing bridge."""
        client = await self._get_client()
        try:
            response = await client.post(
                f"/bridges/{bridge_id}/addChannel",
                params={"channel": channel_id},
            )
            response.raise_for_status()
            return True, None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI add to bridge failed: %s", e)
            return False, str(e)

    # ------------------------------------------------------------------
    # Recording operations
    # ------------------------------------------------------------------

    async def start_recording(self, channel_id, name, format="wav"):
        """Start recording a channel."""
        client = await self._get_client()
        try:
            response = await client.post(
                f"/channels/{channel_id}/record",
                json={
                    "name": name,
                    "format": format,
                    "ifExists": "overwrite",
                },
            )
            response.raise_for_status()
            return response.json(), None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI start recording failed for %s: %s", channel_id, e)
            return None, str(e)

    async def stop_recording(self, recording_name):
        """Stop an active recording."""
        client = await self._get_client()
        try:
            response = await client.post(
                f"/recordings/live/{recording_name}/stop"
            )
            response.raise_for_status()
            return True, None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI stop recording failed for %s: %s", recording_name, e)
            return False, str(e)

    async def get_recording_file(self, recording_name):
        """Download a stored recording file."""
        client = await self._get_client()
        try:
            response = await client.get(
                f"/recordings/stored/{recording_name}/file"
            )
            response.raise_for_status()
            return response.content, None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("ARI get recording failed for %s: %s", recording_name, e)
            return None, str(e)

    # ------------------------------------------------------------------
    # Channel info
    # ------------------------------------------------------------------

    async def get_channel(self, channel_id):
        """Get channel details."""
        client = await self._get_client()
        try:
            response = await client.get(f"/channels/{channel_id}")
            response.raise_for_status()
            return response.json(), None
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            return None, str(e)


class ARIEventListener:
    """
    WebSocket listener for Asterisk ARI Stasis events.

    Connects to the ARI events WebSocket and dispatches events
    to registered handlers. Implements reconnection with exponential backoff.
    """

    def __init__(self, host, port, username, password, app="kanzan-voip", use_ssl=False):
        auth_str = b64encode(f"{username}:{password}".encode()).decode()
        scheme = "wss" if use_ssl else "ws"
        self.ws_url = (
            f"{scheme}://{host}:{port}/ari/events"
            f"?app={app}&subscribeAll=true"
        )
        self.extra_headers = {"Authorization": f"Basic {auth_str}"}
        self._handlers = []
        self._running = False
        self._reconnect_delay = 1
        self._max_reconnect_delay = 30

    def on_event(self, handler):
        """Register an event handler callable."""
        self._handlers.append(handler)

    async def start(self):
        """Start listening for ARI events with automatic reconnection."""
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                if not self._running:
                    break
                logger.error(
                    "ARI WebSocket connection lost: %s. Reconnecting in %ds...",
                    e,
                    self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    self._max_reconnect_delay,
                )

    async def _connect(self):
        """Connect to ARI WebSocket and process events."""
        async with websockets.connect(
            self.ws_url,
            additional_headers=self.extra_headers,
            ping_interval=30,
            ping_timeout=10,
        ) as ws:
            logger.info("Connected to ARI WebSocket at %s", self.ws_url)
            self._reconnect_delay = 1  # Reset backoff on successful connect

            async for message in ws:
                try:
                    event = json.loads(message)
                    await self._dispatch(event)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from ARI: %s", message[:200])

    async def _dispatch(self, event):
        """Dispatch an ARI event to all registered handlers."""
        event_type = event.get("type", "unknown")
        logger.debug("ARI event: %s", event_type)

        for handler in self._handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "Error in ARI event handler for %s", event_type
                )

    def stop(self):
        """Stop the event listener."""
        self._running = False
