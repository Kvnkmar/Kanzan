/**
 * Kanzen — Global formatting utilities.
 * Reads user preferences from localStorage (set by Settings page).
 * Used across all pages for consistent date/time display.
 */
var Kanzan = (function() {
  function _pref(key, fallback) { return localStorage.getItem('kanzan_' + key) || fallback; }

  function _locale() {
    var lang = _pref('language', 'en');
    var map = { en: 'en-US', ms: 'ms-MY', zh: 'zh-CN', es: 'es-ES', fr: 'fr-FR', de: 'de-DE', ja: 'ja-JP' };
    return map[lang] || 'en-US';
  }

  function _dateOpts() {
    var fmt = _pref('date_format', 'YYYY-MM-DD');
    if (fmt === 'MM/DD/YYYY') return { year: 'numeric', month: '2-digit', day: '2-digit' };
    if (fmt === 'DD/MM/YYYY') return { year: 'numeric', month: '2-digit', day: '2-digit' };
    // Default YYYY-MM-DD and 'short' style
    return { year: 'numeric', month: 'short', day: 'numeric' };
  }

  function _timeOpts() {
    var tf = _pref('time_format', '24h');
    return { hour: 'numeric', minute: '2-digit', hour12: tf === '12h' };
  }

  function _tz() {
    var tz = _pref('timezone', '');
    return tz || undefined; // undefined = browser default
  }

  /**
   * Format a date string to the user's preferred format.
   * @param {string} dateStr - ISO date string
   * @param {object} [opts] - Extra Intl.DateTimeFormat options to merge
   * @returns {string}
   */
  function formatDate(dateStr, opts) {
    if (!dateStr) return '--';
    var d = new Date(dateStr);
    if (isNaN(d)) return '--';
    var baseOpts = _dateOpts();
    var tz = _tz();
    if (tz) baseOpts.timeZone = tz;
    if (opts) { for (var k in opts) baseOpts[k] = opts[k]; }
    try { return d.toLocaleDateString(_locale(), baseOpts); }
    catch(e) { return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }); }
  }

  /**
   * Format a date+time string to the user's preferred format.
   * @param {string} dateStr - ISO date string
   * @returns {string}
   */
  function formatDateTime(dateStr) {
    if (!dateStr) return '--';
    var d = new Date(dateStr);
    if (isNaN(d)) return '--';
    var opts = _dateOpts();
    var tOpts = _timeOpts();
    for (var k in tOpts) opts[k] = tOpts[k];
    var tz = _tz();
    if (tz) opts.timeZone = tz;
    try { return d.toLocaleString(_locale(), opts); }
    catch(e) { return d.toLocaleString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); }
  }

  /**
   * Relative time ago string (e.g., "5m ago", "2h ago") with fallback to formatted date.
   * @param {string} dateStr - ISO date string
   * @returns {string}
   */
  function timeAgoSmart(dateStr) {
    if (!dateStr) return '--';
    var d = new Date(dateStr);
    if (isNaN(d)) return '--';
    var diff = Math.floor((Date.now() - d) / 1000);
    if (diff < 60) return 'Just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return formatDateTime(dateStr);
  }

  return {
    formatDate: formatDate,
    formatDateTime: formatDateTime,
    timeAgo: timeAgoSmart,
    getLocale: _locale,
    getTimezone: _tz
  };
})();

/**
 * Common application initialization for Kanzen Suite.
 */
document.addEventListener('DOMContentLoaded', () => {
  // Auto-dismiss alerts after 5 seconds
  document.querySelectorAll('.alert-dismissible').forEach(alert => {
    setTimeout(() => {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
      bsAlert.close();
    }, 5000);
  });

  // Mobile sidebar toggle
  const sidebarToggle = document.getElementById('sidebarToggle');
  const sidebar = document.getElementById('crmSidebar');
  const backdrop = document.getElementById('sidebarBackdrop');

  if (sidebarToggle && sidebar && backdrop) {
    sidebarToggle.addEventListener('click', () => {
      sidebar.classList.toggle('show');
      backdrop.classList.toggle('show');
      document.body.style.overflow = sidebar.classList.contains('show') ? 'hidden' : '';
    });

    backdrop.addEventListener('click', () => {
      sidebar.classList.remove('show');
      backdrop.classList.remove('show');
      document.body.style.overflow = '';
    });
  }

  // Desktop sidebar collapse toggle
  initSidebarCollapse();

  // Apply density preference from localStorage
  initDensity();

  // Navbar scroll effect (backdrop blur border)
  initNavbarScroll();

  // Initialize notification WebSocket if user is authenticated
  if (document.getElementById('notifDropdown')) {
    initNotifications();
  }

  // Load sidebar notification badges
  initSidebarBadges();

  // Live-connection status pill in the navbar — only shown while the
  // WebSocket is reconnecting or offline, so happy-path users don't see
  // any chrome at all.
  initLiveStatusPill();

  // Keep the sidebar user-footer (avatar/name/email) in sync when the
  // current user edits their profile in another tab.
  initSidebarUserLive();

  // Show toast from sessionStorage (for cross-page redirects)
  const pendingToast = sessionStorage.getItem('toast');
  if (pendingToast) {
    sessionStorage.removeItem('toast');
    Toast.success(pendingToast);
  }

});

/**
 * Initialize desktop sidebar collapse/expand toggle.
 */
function initSidebarCollapse() {
  var collapseBtn = document.getElementById('sidebarCollapseBtn');
  if (!collapseBtn) return;

  var STORAGE_KEY = 'kanzan_sidebar_collapsed';

  if (localStorage.getItem(STORAGE_KEY) === '1') {
    document.body.classList.add('sidebar-collapsed');
    collapseBtn.setAttribute('aria-label', 'Expand sidebar');
    collapseBtn.setAttribute('title', 'Expand sidebar');
  }

  collapseBtn.addEventListener('click', function() {
    var isCollapsed = document.body.classList.toggle('sidebar-collapsed');
    localStorage.setItem(STORAGE_KEY, isCollapsed ? '1' : '0');
    collapseBtn.setAttribute('aria-label', isCollapsed ? 'Expand sidebar' : 'Collapse sidebar');
    collapseBtn.setAttribute('title', isCollapsed ? 'Expand sidebar' : 'Collapse sidebar');
  });
}

/**
 * Apply list density preference (comfortable/compact).
 * Sets data-density attribute on <html> so CSS can respond.
 */
function initDensity() {
  var density = localStorage.getItem('kanzan_density') || 'comfortable';
  document.documentElement.setAttribute('data-density', density);
}

/**
 * Navbar scroll effect — adds 'scrolled' class for border/shadow on scroll.
 */
function initNavbarScroll() {
  var header = document.getElementById('contentHeader');
  if (!header) return;

  window.addEventListener('scroll', function() {
    if (window.scrollY > 8) {
      header.classList.add('scrolled');
    } else {
      header.classList.remove('scrolled');
    }
  }, { passive: true });
}

// -----------------------------------------------------------------------
// Notification type config: icons, colors, friendly labels
// Colour values use CSS var() strings so they retheme automatically when
// element.style.color = cfg.color is assigned (browser resolves var()
// at CSS-resolution time).
// -----------------------------------------------------------------------
var NOTIF_TYPE_CONFIG = {
  ticket_assigned:      { icon: 'ti ti-user-check',           color: 'var(--crm-primary)',        label: 'Assigned' },
  ticket_updated:       { icon: 'ti ti-edit',                 color: 'var(--status-danger-text)', label: 'Updated' },
  ticket_comment:       { icon: 'ti ti-message',              color: 'var(--crm-primary)',        label: 'Comment' },
  mention:              { icon: 'ti ti-at',                   color: 'var(--status-danger-text)', label: 'Mention' },
  message:              { icon: 'ti ti-message',              color: 'var(--status-info-text)',   label: 'Message' },
  sla_breach:           { icon: 'ti ti-alert-triangle',       color: 'var(--status-danger-text)', label: 'SLA Alert' },
  payment_failed:       { icon: 'ti ti-credit-card',          color: 'var(--status-warning-text)', label: 'Payment' },
  subscription_change:  { icon: 'ti ti-crown',                color: 'var(--status-success-text)', label: 'Billing' },
  invitation:           { icon: 'ti ti-mail-forward',         color: 'var(--status-info-text)',   label: 'Invite' },
};

function getNotifConfig(type) {
  return NOTIF_TYPE_CONFIG[type] || { icon: 'ti ti-bell', color: 'var(--status-neutral-dot)', label: 'Notification' };
}

function timeAgo(dateStr) {
  return Kanzan.timeAgo(dateStr);
}

function escapeHtmlGlobal(s) { if (!s) return ''; var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function renderNotifItem(n) {
  var cfg = getNotifConfig(n.type);
  var nUrl = (n.data && n.data.url) ? n.data.url
           : (n.data && n.data.ticket_number) ? '/tickets/' + n.data.ticket_number
           : (n.data && n.data.article_id) ? '/knowledge/'
           : (n.data && n.data.conversation_id) ? '/messaging/'
           : null;
  var unreadClass = n.is_read ? '' : ' notif-item--unread';

  return '<a class="notif-item' + unreadClass + '" href="' + (nUrl || '#') + '" data-notif-id="' + n.id + '">' +
    '<div class="notif-item-icon" style="background:' + cfg.color + '15;color:' + cfg.color + ';">' +
      '<i class="' + cfg.icon + '"></i>' +
    '</div>' +
    '<div class="notif-item-content">' +
      '<div class="notif-item-top">' +
        '<span class="notif-item-label" style="color:' + cfg.color + ';">' + cfg.label + '</span>' +
        '<span class="notif-item-time">' + timeAgo(n.created_at) + '</span>' +
      '</div>' +
      '<p class="notif-item-title">' + escapeHtmlGlobal(n.title || 'Notification') + '</p>' +
      (n.body ? '<p class="notif-item-body">' + escapeHtmlGlobal(n.body).substring(0, 100) + '</p>' : '') +
    '</div>' +
  '</a>';
}

/**
 * Initialize real-time notifications via WebSocket.
 */
function initNotifications() {
  const badge = document.getElementById('notifBadge');
  const list = document.getElementById('notifList');
  const countBadge = document.getElementById('notifCountBadge');
  if (!badge || !list) return;

  function updateBadge(count) {
    if (count > 0) {
      badge.textContent = count > 99 ? '99+' : count;
      badge.classList.remove('d-none');
      if (countBadge) { countBadge.textContent = count; countBadge.classList.remove('d-none'); }
    } else {
      badge.classList.add('d-none');
      if (countBadge) countBadge.classList.add('d-none');
    }
  }

  // Mark all read button — fully optimistic with rollback on failure.
  const markAllBtn = document.getElementById('markAllReadBtn');
  if (markAllBtn) {
    markAllBtn.addEventListener('click', (e) => {
      e.preventDefault();

      // Snapshot previous state so we can rollback on server failure.
      var prevBadgeCount = parseInt(badge.textContent || '0');
      var prevListNodes = Array.prototype.slice.call(list.children);
      var prevFooterNodes = notifFooter ? Array.prototype.slice.call(notifFooter.children) : [];

      // Optimistic: clear list, show empty state, zero badge.
      updateBadge(0);
      list.textContent = '';
      var empty = document.createElement('div');
      empty.className = 'notif-empty';
      var ico = document.createElement('div');
      ico.className = 'notif-empty-icon';
      ico.appendChild(Object.assign(document.createElement('i'), { className: 'ti ti-bell-off' }));
      empty.appendChild(ico);
      empty.appendChild(Object.assign(document.createElement('p'),
        { className: 'notif-empty-title', textContent: 'All caught up!' }));
      empty.appendChild(Object.assign(document.createElement('p'),
        { className: 'notif-empty-text', textContent: 'No new notifications right now.' }));
      list.appendChild(empty);
      showViewOlderBtn(true);
      olderPage = 0;

      Api.post('/api/v1/notifications/notifications/mark_all_read/').then(() => {
        // Server confirmed — nothing left to do, the optimistic state
        // is now the canonical state.
        Toast.success('All notifications marked as read');
      }).catch(() => {
        // Server rejected — restore the original list / badge / footer.
        list.textContent = '';
        prevListNodes.forEach(function (n) { list.appendChild(n); });
        updateBadge(prevBadgeCount);
        if (notifFooter) {
          notifFooter.textContent = '';
          prevFooterNodes.forEach(function (n) { notifFooter.appendChild(n); });
        }
        bindNotifClicks(list);
        Toast.error('Failed to mark notifications as read');
      });
    });
  }

  // Load unread count
  Api.get('/api/v1/notifications/notifications/unread_count/').then(data => {
    if (data && data.unread_count > 0) updateBadge(data.unread_count);
  }).catch(() => {});

  var notifFooter = document.getElementById('notifFooter');
  var olderPage = 0;

  function showViewOlderBtn(visible) {
    if (!notifFooter) return;
    notifFooter.textContent = '';
    if (!visible) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'notif-view-older';
    btn.textContent = 'View older notifications';
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      e.preventDefault();
      loadOlderNotifications();
    });
    notifFooter.appendChild(btn);
  }

  function appendNotifications(items) {
    items.forEach(function(n) {
      var tempDiv = document.createElement('div');
      tempDiv.insertAdjacentHTML('beforeend', renderNotifItem(n));
      list.appendChild(tempDiv.firstElementChild);
    });
    bindNotifClicks(list);
  }

  function loadOlderNotifications() {
    olderPage++;
    var btn = notifFooter ? notifFooter.querySelector('.notif-view-older') : null;
    if (btn) btn.textContent = 'Loading...';
    Api.get('/api/v1/notifications/notifications/?page=' + olderPage).then(function(data) {
      if (data && data.results && data.results.length > 0) {
        var emptyEl = list.querySelector('.notif-empty');
        if (emptyEl) emptyEl.remove();
        appendNotifications(data.results);
        showViewOlderBtn(!!data.next);
      } else {
        showViewOlderBtn(false);
      }
    }).catch(function() {
      if (btn) btn.textContent = 'View older notifications';
      olderPage--;
    });
  }

  // Load unread notifications initially
  Api.get('/api/v1/notifications/notifications/?is_read=false').then(function(data) {
    if (data && data.results && data.results.length > 0) {
      list.textContent = '';
      appendNotifications(data.results);
    }
    // Always show "View older" to access read notifications
    showViewOlderBtn(true);
  }).catch(function() {});

  function bindNotifClicks(container) {
    container.querySelectorAll('.notif-item').forEach(item => {
      // Idempotent re-bind: skip if we already attached a handler.
      if (item.dataset.markreadBound === '1') return;
      item.dataset.markreadBound = '1';

      item.addEventListener('click', function (e) {
        var nid = this.dataset.notifId;
        var wasUnread = this.classList.contains('notif-item--unread');
        if (nid && wasUnread) {
          // Optimistic: flip to read + decrement badge before the network
          // call. If the server rejects, revert so server truth wins.
          var self = this;
          var prevBadge = parseInt(badge.textContent || '0');
          self.classList.remove('notif-item--unread');
          if (prevBadge > 0) updateBadge(prevBadge - 1);

          Api.post('/api/v1/notifications/notifications/' + nid + '/mark_read/')
            .catch(function () {
              self.classList.add('notif-item--unread');
              updateBadge(prevBadge);
              Toast.error('Failed to mark notification as read');
            });
        }
        if (!this.getAttribute('href') || this.getAttribute('href') === '#') e.preventDefault();
      });
    });
  }

  // WebSocket for real-time notifications.
  //
  // Reconnect with exponential backoff (1s -> 30s cap, 10 attempts) so a
  // brief server restart or VPN hiccup doesn't kill the live feed. Each
  // received notification is published into LiveBus so pages can react
  // to it (dashboard counters, kanban WIP, sidebar badges, etc.) without
  // every page wiring up its own WebSocket.

  function handleIncomingNotification(data) {
    if (!data || !data.id || !data.title) return;

    var current = parseInt(badge.textContent || '0');
    updateBadge(current + 1);

    Toast.info(data.title + (data.body ? ' — ' + data.body.substring(0, 60) : ''));

    var html = renderNotifItem({
      id: data.id, type: data.type || 'info', title: data.title, body: data.body,
      data: data.data, is_read: false, created_at: data.created_at || new Date().toISOString()
    });
    var safe = (typeof DOMPurify !== 'undefined') ? DOMPurify.sanitize(html) : html;
    var emptyState = list.querySelector('.notif-empty');
    if (emptyState) emptyState.remove();
    list.insertAdjacentHTML('afterbegin', safe);
    bindNotifClicks(list);

    // Publish into LiveBus so any other surface that cares can subscribe
    // without opening its own WebSocket. Keep the legacy CustomEvent for
    // older pages still listening to ``document``.
    if (window.LiveBus) {
      LiveBus.publish('notification.received', data);
    }
    if (data.type) {
      document.dispatchEvent(new CustomEvent('kanzan:notification', { detail: data }));
    }
  }

  (function connectNotificationsWS() {
    if (!('WebSocket' in window)) return;
    var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = protocol + '//' + location.host + '/ws/notifications/';
    var attempt = 0;
    var maxAttempts = 10;
    var manualClose = false;

    function open() {
      var ws;
      try { ws = new WebSocket(url); }
      catch (e) { scheduleReconnect(); return; }

      ws.onopen = function () {
        attempt = 0;
        if (window.LiveBus) LiveBus.setChannelState('notifications', 'open');
      };

      ws.onmessage = function (event) {
        var data;
        try { data = JSON.parse(event.data); } catch (e) { return; }
        handleIncomingNotification(data);
      };

      ws.onclose = function (event) {
        if (window.LiveBus) LiveBus.setChannelState('notifications', 'closed');
        if (!manualClose && event.code !== 1000) scheduleReconnect();
      };

      ws.onerror = function () { /* onclose will follow */ };
    }

    function scheduleReconnect() {
      if (attempt >= maxAttempts) return;
      attempt++;
      var delay = Math.min(1000 * Math.pow(2, attempt - 1), 30000);
      if (window.LiveBus) LiveBus.setChannelState('notifications', 'reconnecting');
      setTimeout(open, delay);
    }

    window.addEventListener('beforeunload', function () { manualClose = true; });
    open();
  })();
}

/**
 * Live-connection status pill.
 *
 * Surfaces a problem with the real-time channels.  Stays hidden during
 * the normal page-load handshake; only appears once a channel that was
 * previously open drops, or stays unable to connect for a sustained
 * period.  Hides again the moment all once-open channels are open
 * again.
 *
 * State machine per channel:
 *     'unknown'      -- no event observed yet (initial)
 *     'open'         -- connected
 *     'reconnecting' -- transient gap (backoff scheduled)
 *     'closed'       -- explicit close after a previous open
 *
 * We only show the pill if at least one channel has *ever* been open
 * and is currently not open. That avoids the false-positive flash
 * during the initial handshake window before all channels finish
 * connecting.
 */
function initLiveStatusPill() {
  if (!window.LiveBus) return;
  var pill = document.getElementById('liveStatusPill');
  if (!pill) return;
  var label = document.getElementById('liveStatusLabel');

  var state = { live: 'unknown', notifications: 'unknown', ticket_feed: 'unknown' };
  // Track which channels have ever come up. A channel that's never
  // succeeded yet is still in handshake and shouldn't trigger the pill.
  var everOpen = { live: false, notifications: false, ticket_feed: false };
  // Grace window: even if a channel never opens, after this delay we
  // surface the pill so a permanently misconfigured server is visible.
  var FALLBACK_MS = 12000;
  var fallbackTimer = setTimeout(function () { applyVisualState(true); }, FALLBACK_MS);

  function applyVisualState(forceShow) {
    pill.classList.remove(
      'live-status-pill--hidden',
      'live-status-pill--reconnecting',
      'live-status-pill--offline'
    );

    var channels = Object.keys(state);
    var problemChannels = channels.filter(function (c) {
      // A channel is "in a problem state" if it's currently not open
      // AND we've either seen it open before OR the fallback fired.
      if (state[c] === 'open') return false;
      return everOpen[c] || forceShow;
    });

    if (problemChannels.length === 0) {
      pill.classList.add('live-status-pill--hidden');
      return;
    }

    var anyReconn = problemChannels.some(function (c) { return state[c] === 'reconnecting'; });
    if (anyReconn) {
      pill.classList.add('live-status-pill--reconnecting');
      if (label) label.textContent = 'Reconnecting';
      pill.setAttribute('title', 'Reconnecting to live updates…');
      return;
    }
    pill.classList.add('live-status-pill--offline');
    if (label) label.textContent = 'Offline';
    pill.setAttribute('title',
      'Live updates are unavailable. Data will refresh once the connection returns.');
  }

  LiveBus.on('livebus.channel_state', function (data) {
    if (!data || !data.channel || !(data.channel in state)) return;
    state[data.channel] = data.state;
    if (data.state === 'open') {
      everOpen[data.channel] = true;
      // First open clears the fallback timer; the pill is now strictly
      // driven by actual state changes.
      if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null; }
    }
    applyVisualState(false);
  });

  // Hide it until we hear something.
  pill.classList.add('live-status-pill--hidden');
}

/**
 * Live updates for the sidebar user footer (avatar/name/email).
 *
 * Driven by ``user.updated`` events emitted from
 * apps/accounts/signals.broadcast_user_save when the current user's
 * profile changes (name, email).  Filters by the data-current-user-id
 * attribute on the sidebar so changes to *other* users in the tenant
 * don't replace your name with theirs.
 */
function initSidebarUserLive() {
  if (!window.LiveBus) return;
  var root = document.querySelector('.sidebar-user[data-current-user-id]');
  if (!root) return;
  var currentUserId = root.getAttribute('data-current-user-id');
  if (!currentUserId) return;

  var nameEl = document.getElementById('sidebarUserName');
  var emailEl = document.getElementById('sidebarUserEmail');
  var avatarEl = document.getElementById('sidebarUserAvatar');

  LiveBus.on('user.updated', function (payload) {
    if (!payload || String(payload.id) !== String(currentUserId)) return;
    if (nameEl && payload.full_name) nameEl.textContent = payload.full_name;
    if (emailEl && payload.email)    emailEl.textContent = payload.email;
    if (avatarEl && payload.initial) avatarEl.textContent = payload.initial;
  });
}

/**
 * Sidebar notification badges — loads counts from unified badge endpoint.
 */
function initSidebarBadges() {
  function setBadge(id, count) {
    var el = document.getElementById(id);
    if (!el) return;
    if (count > 0) {
      el.textContent = count > 99 ? '99+' : count;
      el.style.display = '';
    } else {
      el.style.display = 'none';
    }
  }

  var badgeMap = {
    tickets:  'sidebarBadgeTickets',
    emails:   'sidebarBadgeEmails',
    messages: 'sidebarBadgeMessages',
    calendar: 'sidebarBadgeCalendar',
    reminders: 'sidebarBadgeReminders',
    knowledge: 'sidebarBadgeKnowledge',
  };

  // Public pages (login, register, landing, etc.) don't render the
  // sidebar, so the badge elements aren't in the DOM. Skip the request
  // entirely — otherwise we'd 401 on every unauthenticated page load.
  var hasAnyBadge = Object.values(badgeMap).some(function(id) {
    return document.getElementById(id);
  });
  if (!hasAnyBadge) return;

  function fetchAndApply() {
    return Api.get('/api/v1/nav/badge-counts/').then(function(data) {
      if (!data) return;
      Object.keys(badgeMap).forEach(function(key) {
        setBadge(badgeMap[key], data[key] || 0);
      });
    }).catch(function() {});
  }

  fetchAndApply();

  // Live updates: when a new in-app notification arrives over the
  // notifications WebSocket, the type tells us which badge to bump. We
  // re-fetch the unified endpoint rather than incrementing locally so
  // the count stays in sync with the server's tenant-scoped truth
  // (handles mark-as-read elsewhere, multi-tab sessions, etc.)
  var NOTIF_TO_BADGE = {
    message: 'sidebarBadgeMessages',
    ticket_assigned: 'sidebarBadgeTickets',
    ticket_updated: 'sidebarBadgeTickets',
    ticket_overdue: 'sidebarBadgeTickets',
    reminder_overdue: 'sidebarBadgeReminders',
    kb_review_requested: 'sidebarBadgeKnowledge',
    kb_article_reviewed: 'sidebarBadgeKnowledge',
  };
  document.addEventListener('kanzan:notification', function(e) {
    var type = e && e.detail && e.detail.type;
    if (type && NOTIF_TO_BADGE[type]) {
      fetchAndApply();
    }
  });

  // Also refresh when the user opens the Messages page or sends one
  // (chat code dispatches this event after read/send actions so the
  // sidebar updates without waiting for the next notification.)
  document.addEventListener('kanzan:messages-changed', fetchAndApply);
}

/**
 * Global toast notification system.
 */
const Toast = {
  _icons: {
    success: 'ti ti-check',
    danger:  'ti ti-circle-x',
    warning: 'ti ti-alert-triangle',
    info:    'ti ti-info-circle',
  },

  _titles: {
    success: 'Success',
    danger:  'Error',
    warning: 'Warning',
    info:    'Info',
  },

  _colors: {
    success: 'var(--status-success-text)',
    danger:  'var(--status-danger-text)',
    warning: 'var(--status-warning-text)',
    info:    'var(--crm-primary)',
  },

  _max: 3,

  show(message, type = 'success', duration = 4500) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const icon = this._icons[type] || this._icons.info;
    const color = this._colors[type] || this._colors.info;
    const title = this._titles[type] || 'Notification';

    /* Cap visible toasts: dismiss oldest non-exiting toasts beyond the limit.
       The exiting toast slides out sideways first, then collapses its height
       so the remaining toasts push upward — keep the node alive for both phases. */
    const active = container.querySelectorAll('.crm-toast:not(.crm-toast-exit)');
    const overflow = active.length - (this._max - 1);
    for (let i = 0; i < overflow; i++) {
      const old = active[i];
      old.classList.remove('show');
      old.classList.add('crm-toast-exit');
      setTimeout(function() { if (old.parentNode) old.remove(); }, 600);
    }

    const el = document.createElement('div');
    el.className = 'toast crm-toast border-0';
    el.setAttribute('role', 'alert');
    el.setAttribute('aria-live', 'assertive');
    el.setAttribute('aria-atomic', 'true');
    el.innerHTML =
      '<div class="d-flex">' +
        '<div class="crm-toast-accent" style="background:' + color + ';"></div>' +
        '<div class="crm-toast-body">' +
          '<div class="d-flex align-items-start justify-content-between">' +
            '<div class="d-flex align-items-center gap-2 mb-1">' +
              '<i class="' + icon + '" style="color:' + color + ';font-size:1rem;"></i>' +
              '<span class="crm-toast-title">' + this._escape(title) + '</span>' +
            '</div>' +
            '<button type="button" class="btn-close btn-close-sm ms-2" data-bs-dismiss="toast" aria-label="Close"></button>' +
          '</div>' +
          '<div class="crm-toast-msg">' + this._escape(message) + '</div>' +
        '</div>' +
      '</div>';

    container.appendChild(el);
    /* Trigger transition by adding .show in next frame */
    requestAnimationFrame(function() {
      el.classList.add('show');
    });
    setTimeout(function() {
      el.classList.remove('show');
      el.classList.add('crm-toast-exit');
      /* Keep the node alive for the full slide + collapse sequence (~430ms). */
      setTimeout(function() { if (el.parentNode) el.remove(); }, 600);
    }, duration);
  },

  success(msg) { this.show(msg, 'success'); },
  error(msg)   { this.show(msg, 'danger'); },
  warning(msg) { this.show(msg, 'warning'); },
  info(msg)    { this.show(msg, 'info'); },

  _escape(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },
};
