/**
 * LiveConnection — the single shared WebSocket that powers Phase 2.
 *
 * One ``ws/live/`` connection per authenticated tab. The server fans
 * out tenant-wide events here (newsfeed, CRM activities, reminders,
 * profile/membership changes, etc.) so individual pages can subscribe
 * to LiveBus without each opening their own WebSocket.
 *
 * Behaviour:
 *   - Auto-connect on script load (only when there's a session cookie
 *     and we're not on the login/register pages).
 *   - Exponential-backoff reconnect: 1s -> 30s cap, infinite retries
 *     (a long laptop sleep should still recover).
 *   - Heartbeat every 25s: if the socket is dead, the next reconnect
 *     attempt fires immediately.
 *   - Publishes channel state into LiveBus so a status indicator can
 *     show "live" / "reconnecting" / "offline".
 *   - On every successful reconnect, publishes ``live.reconnected`` so
 *     subscribers (dashboard, lists) can refetch to fill any gap that
 *     opened while we were disconnected.
 *
 * The server payload looks like::
 *     { "type": "newsfeed.created",
 *       "payload": {...},
 *       "ts": "2026-05-13T12:34:56Z" }
 *
 * LiveConnection unpacks that and calls LiveBus.publish(type, payload).
 */
(function () {
    'use strict';

    if (!window.LiveBus) {
        // live-bus.js must load first.
        console.warn('[LiveConnection] LiveBus not present; aborting setup.');
        return;
    }
    if (!('WebSocket' in window)) {
        console.warn('[LiveConnection] WebSocket not supported; live updates disabled.');
        return;
    }

    var HEARTBEAT_INTERVAL_MS = 25000;
    var HEARTBEAT_TIMEOUT_MS = 8000;
    var INITIAL_BACKOFF_MS = 1000;
    var MAX_BACKOFF_MS = 30000;

    var ws = null;
    var reconnectTimer = null;
    var heartbeatTimer = null;
    var heartbeatPongTimer = null;
    var attempt = 0;
    var manualClose = false;
    var hadOpenedOnce = false;       // for distinguishing first connect vs reconnect

    function shouldConnect() {
        // Skip pre-auth pages where the WS would just bounce.
        var skipPaths = [
            '/login/', '/register/', '/verify-email/', '/verify-email-sent/',
            '/auth/handoff/', '/landing/', '/setup-company/', '/workspaces/',
        ];
        var path = window.location.pathname;
        for (var i = 0; i < skipPaths.length; i++) {
            if (path.indexOf(skipPaths[i]) === 0) return false;
        }
        // Require a Django session cookie. (Auth middleware will still
        // reject with 4001 if it's stale, which triggers our backoff.)
        if (!/sessionid=/.test(document.cookie)) return false;
        return true;
    }

    function wsUrl() {
        var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return protocol + '//' + window.location.host + '/ws/live/';
    }

    function clearTimers() {
        if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
        if (heartbeatPongTimer) { clearTimeout(heartbeatPongTimer); heartbeatPongTimer = null; }
    }

    function open() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        try {
            ws = new WebSocket(wsUrl());
        } catch (e) {
            scheduleReconnect();
            return;
        }

        ws.onopen = function () {
            attempt = 0;
            LiveBus.setChannelState('live', 'open');
            startHeartbeat();
            if (hadOpenedOnce) {
                // Tell subscribers we just came back so they can refetch
                // any data that might have changed during the gap.
                LiveBus.publish('live.reconnected', { ts: Date.now() });
            }
            hadOpenedOnce = true;
        };

        ws.onmessage = function (event) {
            // Heartbeat reply: server doesn't speak heartbeat today, but
            // any inbound message counts as proof the socket is alive.
            if (heartbeatPongTimer) {
                clearTimeout(heartbeatPongTimer);
                heartbeatPongTimer = null;
            }

            var data;
            try { data = JSON.parse(event.data); }
            catch (e) { return; }
            if (!data || !data.type) return;
            // The consumer wraps each event as {type, payload, ts}. We
            // dispatch into LiveBus using the type so subscribers can
            // listen by domain.verb without caring about transport.
            LiveBus.publish(data.type, data.payload || {}, { fromTab: false });
        };

        ws.onclose = function (event) {
            clearTimers();
            LiveBus.setChannelState('live', 'closed');
            if (manualClose || event.code === 1000) return;
            scheduleReconnect();
        };

        ws.onerror = function () { /* onclose handles cleanup */ };
    }

    function scheduleReconnect() {
        clearTimers();
        if (manualClose) return;
        attempt++;
        var delay = Math.min(INITIAL_BACKOFF_MS * Math.pow(2, attempt - 1), MAX_BACKOFF_MS);
        // Jitter +- 20% to spread reconnect storms across many tabs.
        delay = Math.round(delay * (0.8 + Math.random() * 0.4));
        LiveBus.setChannelState('live', 'reconnecting');
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(open, delay);
    }

    function startHeartbeat() {
        clearTimers();
        heartbeatTimer = setInterval(function () {
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            try {
                // The server consumer ignores receive_json today, but
                // sending bytes still surfaces a TCP/network failure
                // (the close event fires).  We also start a pong timer
                // and force-reconnect if nothing comes back.
                ws.send(JSON.stringify({ action: 'ping', ts: Date.now() }));
            } catch (e) {
                // Already broken; trigger reconnect.
                try { ws.close(); } catch (_) { /* noop */ }
                return;
            }
            if (heartbeatPongTimer) clearTimeout(heartbeatPongTimer);
            heartbeatPongTimer = setTimeout(function () {
                // No traffic at all in HEARTBEAT_TIMEOUT_MS — assume
                // the connection is wedged behind a proxy and recycle.
                if (ws && ws.readyState === WebSocket.OPEN) {
                    try { ws.close(); } catch (_) { /* noop */ }
                }
            }, HEARTBEAT_TIMEOUT_MS);
        }, HEARTBEAT_INTERVAL_MS);
    }

    function disconnect() {
        manualClose = true;
        clearTimers();
        clearTimeout(reconnectTimer);
        if (ws) {
            try { ws.close(1000); } catch (_) { /* noop */ }
            ws = null;
        }
        LiveBus.setChannelState('live', 'closed');
    }

    window.addEventListener('beforeunload', function () { manualClose = true; });

    // Reconnect aggressively when the tab regains focus after sleep —
    // any backoff in progress is irrelevant once the user is looking.
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible' && !manualClose) {
            if (!ws || ws.readyState === WebSocket.CLOSED) {
                clearTimeout(reconnectTimer);
                attempt = 0;
                open();
            }
        }
    });

    // Public surface (mostly for debugging from DevTools).
    window.LiveConnection = {
        open: open,
        close: disconnect,
        get isOpen() { return !!(ws && ws.readyState === WebSocket.OPEN); },
        get attempt() { return attempt; },
    };

    if (shouldConnect()) {
        open();
    }
})();
