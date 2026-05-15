/**
 * LiveBus — single source of truth for real-time updates across the app.
 *
 * One pub/sub event bus that everything subscribes to.  Multiple WebSocket
 * consumers (notifications, ticket feed, chat, presence, voip) publish
 * normalised events into the bus; pages register handlers for the event
 * types they care about and update only the DOM nodes that need updating.
 *
 * Event naming convention:  "<domain>.<verb>" — e.g.
 *     ticket.created, ticket.updated, ticket.assigned, ticket.closed,
 *     ticket.deleted, notification.received, message.received,
 *     newsfeed.created, reminder.completed, ...
 *
 * The bus also fans events out to other open tabs via the BroadcastChannel
 * API so a mutation in tab A appears in tab B without a per-tab WebSocket.
 *
 * Public API:
 *     LiveBus.on(eventType, handler)         -> off() function
 *     LiveBus.onMany(["a", "b"], handler)    -> off() function
 *     LiveBus.publish(eventType, payload, {fromTab: bool})
 *     LiveBus.debounce(fn, ms)               -> debounced fn (handy helper)
 *     LiveBus.isConnected(channel)           -> bool
 *
 * Notes for handlers:
 *   - Handlers must be cheap.  Heavy DOM work should be debounced /
 *     RequestAnimationFrame-batched by the caller (see LiveBus.debounce).
 *   - Handlers receive (payload, meta) where meta = {type, fromTab, ts}.
 *   - Throwing inside a handler is caught and logged; other handlers
 *     still run.
 *   - Subscribing to "*" gets every event (useful for debugging).
 */
window.LiveBus = (function () {
  'use strict';

  var DEBUG = false;          // set window.LiveBus._debug = true at runtime
  var WILDCARD = '*';
  var BROADCAST_CHANNEL_NAME = 'kanzan-live';

  // type -> Set<handler>
  var handlers = Object.create(null);
  // channel name -> 'open' | 'closed' | 'reconnecting'
  var channelState = Object.create(null);
  // BroadcastChannel for cross-tab fan-out (best-effort; not all browsers
  // expose it for old Safari/Edge versions, fall back to a no-op).
  var bc = null;
  try {
    if (typeof BroadcastChannel !== 'undefined') {
      bc = new BroadcastChannel(BROADCAST_CHANNEL_NAME);
      bc.onmessage = function (msg) {
        var data = msg && msg.data;
        if (!data || !data.type) return;
        // fromTab=true tells handlers this came from another tab so
        // they can skip "make a toast" type side effects.
        _dispatch(data.type, data.payload, { fromTab: true, ts: data.ts });
      };
    }
  } catch (e) {
    // ignore — cross-tab sync is a bonus, not a requirement
  }

  function on(eventType, handler) {
    return onMany([eventType], handler);
  }

  function onMany(eventTypes, handler) {
    if (typeof handler !== 'function') return function () {};
    var types = (eventTypes || []).slice();
    types.forEach(function (t) {
      if (!handlers[t]) handlers[t] = new Set();
      handlers[t].add(handler);
    });
    return function off() {
      types.forEach(function (t) {
        if (handlers[t]) handlers[t].delete(handler);
      });
    };
  }

  function publish(eventType, payload, opts) {
    opts = opts || {};
    if (DEBUG) console.debug('[LiveBus] publish', eventType, payload, opts);
    _dispatch(eventType, payload, { fromTab: false, ts: Date.now() });
    // Fan-out to other tabs only if this didn't originate from a tab event.
    if (!opts.fromTab && bc) {
      try {
        bc.postMessage({ type: eventType, payload: payload, ts: Date.now() });
      } catch (e) {
        // Some payloads aren't structured-cloneable (e.g. DOM nodes).
        // Silently drop cross-tab delivery in that case.
      }
    }
  }

  function _dispatch(eventType, payload, meta) {
    meta = meta || { fromTab: false, ts: Date.now() };
    meta.type = eventType;
    var fired = 0;
    // Exact-type handlers
    if (handlers[eventType]) {
      handlers[eventType].forEach(function (h) {
        try { h(payload, meta); fired++; }
        catch (e) { console.warn('[LiveBus] handler error for', eventType, e); }
      });
    }
    // Wildcard handlers
    if (handlers[WILDCARD]) {
      handlers[WILDCARD].forEach(function (h) {
        try { h(payload, meta); fired++; }
        catch (e) { console.warn('[LiveBus] wildcard handler error', e); }
      });
    }
    if (DEBUG) console.debug('[LiveBus] fired', fired, 'handlers for', eventType);
  }

  function setChannelState(channel, state) {
    channelState[channel] = state;
    publish('livebus.channel_state', { channel: channel, state: state });
  }

  function isConnected(channel) {
    return channelState[channel] === 'open';
  }

  /**
   * debounce(fn, ms) — coalesce many calls into one trailing-edge call.
   * Useful for "refetch dashboard stats" type handlers that don't want
   * to fire once per ticket event in a burst.
   */
  function debounce(fn, ms) {
    var timer = null;
    return function () {
      var args = arguments;
      var ctx = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  }

  /**
   * rafBatch(fn) — coalesce many calls into one rAF tick. Use for DOM
   * mutation handlers that get called in a burst (e.g. 50 ticket updates
   * arriving at once on reconnect).
   */
  function rafBatch(fn) {
    var pending = false;
    var lastArgs = null;
    var ctx = null;
    return function () {
      lastArgs = arguments;
      ctx = this;
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () {
        pending = false;
        fn.apply(ctx, lastArgs);
      });
    };
  }

  // Expose a couple of knobs for runtime debugging.
  return {
    on: on,
    onMany: onMany,
    publish: publish,
    debounce: debounce,
    rafBatch: rafBatch,
    isConnected: isConnected,
    setChannelState: setChannelState,
    // For debugging from DevTools: LiveBus._debug = true; LiveBus._handlers
    get _debug() { return DEBUG; },
    set _debug(v) { DEBUG = !!v; },
    _handlers: handlers,
    _channelState: channelState,
  };
})();
