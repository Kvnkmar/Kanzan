/**
 * Real-time ticket list updates via WebSocket.
 *
 * Connects to ws/tickets/feed/ and updates the ticket list
 * in real-time when tickets are created, updated, assigned, or closed.
 *
 * Usage:
 *   TicketFeed.connect()  - Connect to the ticket feed WebSocket
 *   TicketFeed.disconnect() - Disconnect
 *   TicketFeed.onEvent(callback) - Register an event listener
 */

var TicketFeed = (function () {
    'use strict';

    var ws = null;
    var reconnectTimer = null;
    var reconnectAttempts = 0;
    var maxReconnectAttempts = 10;
    var listeners = [];
    var connected = false;

    function getWsUrl() {
        var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return protocol + '//' + window.location.host + '/ws/tickets/feed/';
    }

    function connect() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        try {
            ws = new WebSocket(getWsUrl());
        } catch (e) {
            console.warn('[TicketFeed] WebSocket creation failed:', e);
            scheduleReconnect();
            return;
        }

        ws.onopen = function () {
            connected = true;
            reconnectAttempts = 0;
            console.log('[TicketFeed] Connected');
        };

        ws.onmessage = function (event) {
            try {
                var data = JSON.parse(event.data);
                notifyListeners(data);
                updateTicketListUI(data);
            } catch (e) {
                console.warn('[TicketFeed] Failed to parse message:', e);
            }
        };

        ws.onclose = function (event) {
            connected = false;
            if (event.code !== 1000) {
                scheduleReconnect();
            }
        };

        ws.onerror = function () {
            connected = false;
        };
    }

    function disconnect() {
        clearTimeout(reconnectTimer);
        reconnectAttempts = maxReconnectAttempts;
        if (ws) {
            ws.close(1000);
            ws = null;
        }
        connected = false;
    }

    function scheduleReconnect() {
        if (reconnectAttempts >= maxReconnectAttempts) return;
        reconnectAttempts++;
        var delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
        reconnectTimer = setTimeout(connect, delay);
    }

    function onEvent(callback) {
        listeners.push(callback);
    }

    function notifyListeners(data) {
        listeners.forEach(function (cb) {
            try { cb(data); } catch (e) { console.warn('[TicketFeed] Listener error:', e); }
        });
    }

    /**
     * Update the ticket list UI based on the real-time event.
     * Shows a notification toast and optionally updates rows.
     */
    function updateTicketListUI(data) {
        var eventType = data.type;
        var ticketNumber = data.ticket_number;
        var subject = data.subject || '';

        // Show a subtle toast for ticket events
        if (typeof Toast !== 'undefined') {
            if (eventType === 'ticket_created') {
                Toast.info('New ticket #' + ticketNumber + ': ' + truncate(subject, 50));
            } else if (eventType === 'ticket_closed') {
                Toast.info('Ticket #' + ticketNumber + ' closed');
            } else if (eventType === 'ticket_assigned') {
                var assignee = data.new_assignee || 'someone';
                Toast.info('Ticket #' + ticketNumber + ' assigned to ' + assignee);
            }
        }

        // Pulse the ticket row if visible
        var row = document.querySelector('[data-ticket-number="' + ticketNumber + '"]');
        if (row) {
            row.classList.add('ticket-updated-pulse');
            setTimeout(function () {
                row.classList.remove('ticket-updated-pulse');
            }, 2000);

            // Update status badge if changed
            if (data.status_name) {
                var statusBadge = row.querySelector('.status-badge');
                if (statusBadge) {
                    statusBadge.textContent = data.status_name;
                    if (data.status_color) {
                        statusBadge.style.backgroundColor = data.status_color;
                    }
                }
            }

            // Update assignee name if changed
            if (data.assignee_name !== undefined) {
                var assigneeEl = row.querySelector('.assignee-name');
                if (assigneeEl) {
                    assigneeEl.textContent = data.assignee_name || 'Unassigned';
                }
            }
        }

        // For new tickets, show a "new tickets available" banner
        if (eventType === 'ticket_created') {
            showNewTicketBanner();
        }
    }

    function showNewTicketBanner() {
        var existing = document.getElementById('new-tickets-banner');
        if (existing) return;

        var banner = document.createElement('div');
        banner.id = 'new-tickets-banner';
        banner.className = 'new-tickets-banner';
        banner.setAttribute('role', 'status');
        banner.setAttribute('aria-live', 'polite');

        var text = document.createElement('span');
        text.textContent = 'New tickets available. ';
        banner.appendChild(text);

        var btn = document.createElement('button');
        btn.className = 'btn btn-sm btn-link p-0';
        btn.textContent = 'Refresh';
        btn.addEventListener('click', function () {
            window.location.reload();
        });
        banner.appendChild(btn);

        var container = document.querySelector('.ticket-list-container, .content-area, main');
        if (container) {
            container.insertBefore(banner, container.firstChild);
        }
    }

    function truncate(str, len) {
        if (str.length <= len) return str;
        return str.substring(0, len) + '...';
    }

    // Auto-connect if on a ticket-related page
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            if (document.querySelector('[data-ticket-feed]') ||
                window.location.pathname.indexOf('/tickets') === 0 ||
                window.location.pathname.indexOf('/dashboard') === 0) {
                connect();
            }
        });
    }

    return {
        connect: connect,
        disconnect: disconnect,
        onEvent: onEvent,
        isConnected: function () { return connected; },
    };
})();
