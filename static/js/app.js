/**
 * Kanzan — Global formatting utilities.
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
// -----------------------------------------------------------------------
var NOTIF_TYPE_CONFIG = {
  ticket_assigned:      { icon: 'ti ti-user-check',           color: '#2563EB', label: 'Assigned' },
  ticket_updated:       { icon: 'ti ti-edit',                 color: '#3B82F6', label: 'Updated' },
  ticket_comment:       { icon: 'ti ti-message',              color: '#2563EB', label: 'Comment' },
  mention:              { icon: 'ti ti-at',                   color: '#8B5CF6', label: 'Mention' },
  message:              { icon: 'ti ti-message',              color: '#06B6D4', label: 'Message' },
  sla_breach:           { icon: 'ti ti-alert-triangle',       color: '#EF4444', label: 'SLA Alert' },
  payment_failed:       { icon: 'ti ti-credit-card',          color: '#F59E0B', label: 'Payment' },
  subscription_change:  { icon: 'ti ti-crown',                color: '#10B981', label: 'Billing' },
  invitation:           { icon: 'ti ti-mail-forward',         color: '#EC4899', label: 'Invite' },
};

function getNotifConfig(type) {
  return NOTIF_TYPE_CONFIG[type] || { icon: 'ti ti-bell', color: '#94A3B8', label: 'Notification' };
}

function timeAgo(dateStr) {
  return Kanzan.timeAgo(dateStr);
}

function escapeHtmlGlobal(s) { if (!s) return ''; var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function renderNotifItem(n) {
  var cfg = getNotifConfig(n.type);
  var nUrl = (n.data && n.data.url) ? n.data.url
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

  // Mark all read button
  const markAllBtn = document.getElementById('markAllReadBtn');
  if (markAllBtn) {
    markAllBtn.addEventListener('click', (e) => {
      e.preventDefault();
      Api.post('/api/v1/notifications/notifications/mark_all_read/').then(() => {
        updateBadge(0);
        list.querySelectorAll('.notif-item--unread').forEach(el => el.classList.remove('notif-item--unread'));
        Toast.success('All notifications marked as read');
      }).catch(() => {
        Toast.error('Failed to mark notifications as read');
      });
    });
  }

  // Load unread count
  Api.get('/api/v1/notifications/notifications/unread_count/').then(data => {
    if (data && data.unread_count > 0) updateBadge(data.unread_count);
  }).catch(() => {});

  // Load recent notifications
  Api.get('/api/v1/notifications/notifications/?page_size=10').then(data => {
    if (data && data.results && data.results.length > 0) {
      var html = '';
      data.results.forEach(n => { html += renderNotifItem(n); });
      list.innerHTML = html;
      bindNotifClicks(list);
    }
  }).catch(() => {});

  function bindNotifClicks(container) {
    container.querySelectorAll('.notif-item').forEach(item => {
      item.addEventListener('click', function(e) {
        var nid = this.dataset.notifId;
        if (nid && this.classList.contains('notif-item--unread')) {
          Api.post('/api/v1/notifications/notifications/' + nid + '/mark_read/').catch(() => {});
          this.classList.remove('notif-item--unread');
          var current = parseInt(badge.textContent || '0');
          if (current > 0) updateBadge(current - 1);
        }
        if (!this.getAttribute('href') || this.getAttribute('href') === '#') e.preventDefault();
      });
    });
  }

  // WebSocket for real-time notifications
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  try {
    const ws = new WebSocket(`${protocol}//${location.host}/ws/notifications/`);
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.id && data.title) {
        const current = parseInt(badge.textContent || '0');
        updateBadge(current + 1);

        Toast.info(data.title + (data.body ? ' — ' + data.body.substring(0, 60) : ''));

        var tempDiv = document.createElement('div');
        tempDiv.innerHTML = renderNotifItem({
          id: data.id, type: data.type || 'info', title: data.title, body: data.body,
          data: data.data, is_read: false, created_at: data.created_at || new Date().toISOString()
        });
        var newItem = tempDiv.firstElementChild;
        var emptyState = list.querySelector('.notif-empty');
        if (emptyState) emptyState.remove();
        list.prepend(newItem);
        bindNotifClicks(list);
      }
    };
  } catch (e) {}
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
  };

  Api.get('/api/v1/nav/badge-counts/').then(function(data) {
    if (!data) return;
    Object.keys(badgeMap).forEach(function(key) {
      setBadge(badgeMap[key], data[key] || 0);
    });
  }).catch(function() {});
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
    success: '#10B981',
    danger:  '#EF4444',
    warning: '#F59E0B',
    info:    '#2563EB',
  },

  show(message, type = 'success', duration = 4500) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const icon = this._icons[type] || this._icons.info;
    const color = this._colors[type] || this._colors.info;
    const title = this._titles[type] || 'Notification';

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
      el.addEventListener('transitionend', function handler() {
        el.removeEventListener('transitionend', handler);
        el.remove();
      });
      /* Fallback removal if transitionend doesn't fire */
      setTimeout(function() { if (el.parentNode) el.remove(); }, 500);
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
