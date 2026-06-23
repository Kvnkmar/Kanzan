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

  /**
   * Human-readable file size (e.g. "812 B", "44 KB", "1.2 MB").
   * @param {number} bytes
   * @returns {string}
   */
  function formatFileSize(bytes) {
    var n = Number(bytes) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
  }

  /**
   * Render a customer-email attachment strip into `hostEl`. Images render
   * inline as a thumbnail gallery; other files become download chips. Shared by
   * the Inbox Hub cockpit and the Emails page so both surfaces match.
   *
   * All dynamic text (filenames) goes through textContent — never innerHTML —
   * so a crafted filename can't inject markup. The `url`s come from the API
   * (same-origin, index-addressed) and the download endpoint authorises each
   * fetch, so they're safe to use as src/href.
   *
   * @param {HTMLElement} hostEl - container to fill (toggled hidden when empty)
   * @param {Array} list - attachment rows: {filename, size, is_image, url}
   */
  function renderMailAttachments(hostEl, list) {
    if (!hostEl) return;
    hostEl.replaceChildren();
    var items = Array.isArray(list) ? list : [];
    if (!items.length) { hostEl.hidden = true; return; }

    var images = items.filter(function (a) { return a && a.is_image; });
    var files = items.filter(function (a) { return a && !a.is_image; });

    var heading = document.createElement('div');
    heading.className = 'mail-att-title';
    var clip = document.createElement('i');
    clip.className = 'ti ti-paperclip';
    clip.setAttribute('aria-hidden', 'true');
    heading.appendChild(clip);
    heading.appendChild(document.createTextNode(
      ' ' + items.length + (items.length === 1 ? ' attachment' : ' attachments')
    ));
    hostEl.appendChild(heading);

    if (images.length) {
      var gallery = document.createElement('div');
      gallery.className = 'mail-att-gallery';
      images.forEach(function (att) {
        var link = document.createElement('a');
        link.className = 'mail-att-image';
        link.href = att.url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.title = att.filename || 'image';

        var img = document.createElement('img');
        img.src = att.url;
        img.alt = att.filename || 'image attachment';
        img.loading = 'lazy';
        // A spoofed/broken image collapses its tile rather than showing a
        // browser-default broken-image glyph.
        img.addEventListener('error', function () {
          link.classList.add('mail-att-image--broken');
        });
        link.appendChild(img);
        gallery.appendChild(link);
      });
      hostEl.appendChild(gallery);
    }

    if (files.length) {
      var fileList = document.createElement('div');
      fileList.className = 'mail-att-files';
      files.forEach(function (att) {
        var chip = document.createElement('a');
        chip.className = 'mail-att-chip';
        chip.href = att.url + (att.url.indexOf('?') === -1 ? '?' : '&') + 'dl=1';
        chip.setAttribute('download', att.filename || '');

        var icon = document.createElement('i');
        icon.className = 'ti ti-file-download';
        icon.setAttribute('aria-hidden', 'true');
        chip.appendChild(icon);

        var name = document.createElement('span');
        name.className = 'mail-att-chip-name';
        name.textContent = att.filename || 'file';
        chip.appendChild(name);

        var size = document.createElement('span');
        size.className = 'mail-att-chip-size';
        size.textContent = formatFileSize(att.size);
        chip.appendChild(size);

        fileList.appendChild(chip);
      });
      hostEl.appendChild(fileList);
    }

    hostEl.hidden = false;
  }

  return {
    formatDate: formatDate,
    formatDateTime: formatDateTime,
    timeAgo: timeAgoSmart,
    formatFileSize: formatFileSize,
    renderMailAttachments: renderMailAttachments,
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
    // Fire reminder popups at the exact due time (instant), with the 30s
    // server task as a backstop. Dedup in ReminderAlerts prevents double-pop.
    ReminderScheduler.start();
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
  ticket_assigned:               { icon: 'ti ti-user-check',           color: 'var(--crm-primary)',        label: 'Assigned' },
  ticket_updated:                { icon: 'ti ti-edit',                 color: 'var(--status-danger-text)', label: 'Updated' },
  ticket_comment:                { icon: 'ti ti-message',              color: 'var(--crm-primary)',        label: 'Comment' },
  mention:                       { icon: 'ti ti-at',                   color: 'var(--status-danger-text)', label: 'Mention' },
  message:                       { icon: 'ti ti-message',              color: 'var(--status-info-text)',   label: 'Message' },
  sla_breach:                    { icon: 'ti ti-alert-triangle',       color: 'var(--status-danger-text)', label: 'SLA Alert' },
  payment_failed:                { icon: 'ti ti-credit-card',          color: 'var(--status-warning-text)', label: 'Payment' },
  subscription_change:           { icon: 'ti ti-crown',                color: 'var(--status-success-text)', label: 'Billing' },
  invitation:                    { icon: 'ti ti-mail-forward',         color: 'var(--status-info-text)',   label: 'Invite' },
  reminder_due:                  { icon: 'ti ti-bell-ringing',         color: 'var(--crm-primary)',        label: 'Reminder' },
  reminder_overdue:              { icon: 'ti ti-alarm',                color: 'var(--status-danger-text)', label: 'Overdue' },
  // Inbox Hub (Phase 1)
  hub_email_assigned:            { icon: 'ti ti-inbox',                color: 'var(--crm-primary)',        label: 'Hub Email' },
  hub_email_reassigned:          { icon: 'ti ti-arrow-right',          color: 'var(--crm-primary)',        label: 'Reassigned' },
  hub_email_escalated_to_me:     { icon: 'ti ti-alert-octagon',        color: 'var(--status-danger-text)', label: 'Escalated' },
  hub_email_sla_breach_warning:  { icon: 'ti ti-clock-exclamation',    color: 'var(--status-warning-text)', label: 'SLA Warning' },
  hub_email_sla_breached:        { icon: 'ti ti-alert-triangle',       color: 'var(--status-danger-text)', label: 'SLA Breach' },
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
    '<div class="notif-item-icon" style="background:color-mix(in srgb, ' + cfg.color + ' 14%, transparent);color:' + cfg.color + ';">' +
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
 * Reminder-due alerts.
 *
 * Fired when a ``reminder_due`` notification arrives over /ws/notifications/.
 * Surfaces a centered, can't-miss modal (#reminderDueModal in base.html) plus
 * an audio chime and a desktop/OS notification, so the agent is alerted even
 * with the Kanzen tab backgrounded. Works on every page because the
 * notifications WebSocket is connected app-wide from base.html.
 *
 * Chime uses the Web Audio API (no asset to ship). Audio playback and — on
 * Safari — Notification.requestPermission are gated behind a user gesture, so
 * we arm both on the first interaction after load.
 */
var ReminderAlerts = (function () {
  var audioCtx = null;
  var queue = [];
  var showing = false;
  var armed = false;

  function getCtx() {
    if (audioCtx) return audioCtx;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try { audioCtx = new AC(); } catch (e) { return null; }
    return audioCtx;
  }

  // Arm audio + desktop-notification permission on the first user gesture.
  function armGestures() {
    if (armed) return;
    armed = true;
    function arm() {
      var ctx = getCtx();
      if (ctx && ctx.state === 'suspended') { ctx.resume().catch(function () {}); }
      if ('Notification' in window && Notification.permission === 'default') {
        try { Notification.requestPermission(); } catch (e) { /* older API */ }
      }
      window.removeEventListener('pointerdown', arm);
      window.removeEventListener('keydown', arm);
    }
    window.addEventListener('pointerdown', arm, { passive: true });
    window.addEventListener('keydown', arm);
  }

  // Gentle two-tone chime via Web Audio (no audio file needed).
  function playChime() {
    var ctx = getCtx();
    if (!ctx) return;
    if (ctx.state === 'suspended') { ctx.resume().catch(function () {}); }
    try {
      var t0 = ctx.currentTime;
      [880.0, 1174.66].forEach(function (freq, i) {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = freq;
        var start = t0 + i * 0.16;
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.16, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.34);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(start + 0.38);
      });
    } catch (e) { /* audio not available — modal still shows */ }
  }

  function desktopNotify(data) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try {
      var n = new Notification(data.title || 'Reminder due', {
        body: data.body || '',
        icon: '/static/images/DP.png',
        tag: 'reminder-' + ((data.data && data.data.reminder_id) || data.id),
        requireInteraction: true,
      });
      n.onclick = function () {
        window.focus();
        window.location.href = (data.data && data.data.url) || '/reminders/';
        n.close();
      };
    } catch (e) { /* best-effort */ }
  }

  function fmtTime(iso) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    } catch (e) { return ''; }
  }

  function renderNext() {
    if (showing) return;
    var data = queue.shift();
    if (!data) return;

    var modalEl = document.getElementById('reminderDueModal');
    if (!modalEl || typeof bootstrap === 'undefined' || !bootstrap.Modal) {
      // No modal surface (e.g. a pre-auth page) — degrade to a sticky toast.
      if (window.Toast) Toast.show(data.title || 'Reminder due', 'warning', 8000);
      return;
    }

    var subjectEl = document.getElementById('reminderDueTitle');
    var metaEl = document.getElementById('reminderDueMeta');
    var openEl = document.getElementById('reminderDueOpen');

    if (subjectEl) {
      subjectEl.textContent = (data.title || 'Reminder').replace(/^Reminder due:\s*/i, '');
    }
    if (metaEl) {
      var meta = data.body || '';
      var t = fmtTime(data.data && data.data.scheduled_at);
      if (t) meta = meta ? (meta + ' · ' + t) : ('Due ' + t);
      metaEl.textContent = meta;
    }
    if (openEl) {
      openEl.setAttribute('href', (data.data && data.data.url) || '/reminders/');
    }

    showing = true;
    modalEl.addEventListener('hidden.bs.modal', function onHidden() {
      modalEl.removeEventListener('hidden.bs.modal', onHidden);
      showing = false;
      // Drain any reminders that came due while this modal was open.
      setTimeout(renderNext, 250);
    });
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  // Dedup so a reminder isn't popped twice — once by the instant client-side
  // timer (ReminderScheduler) and again when the server's reminder_due
  // notification lands 0–30s later. Keyed on reminder id + due-time (epoch ms,
  // so format differences between the API and the WS payload don't matter), so
  // a rescheduled reminder (new due-time) can still re-alert.
  var shownKeys = Object.create(null);
  function dedupKey(data) {
    var d = data.data || {};
    var rid = d.reminder_id || data.id || '';
    var t = '';
    if (d.scheduled_at) {
      var ms = new Date(d.scheduled_at).getTime();
      if (!isNaN(ms)) t = String(ms);
    }
    return rid + '@' + t;
  }

  function show(data) {
    var key = dedupKey(data);
    if (shownKeys[key]) return;
    shownKeys[key] = Date.now();
    // Bound memory: drop keys older than 6h.
    var cutoff = Date.now() - 6 * 3600 * 1000;
    for (var k in shownKeys) { if (shownKeys[k] < cutoff) delete shownKeys[k]; }

    armGestures();
    playChime();
    desktopNotify(data);
    queue.push(data);
    renderNext();
  }

  // Arm as early as possible so the first due alert can chime + pop OS notif.
  if (document.readyState !== 'loading') armGestures();
  else document.addEventListener('DOMContentLoaded', armGestures);

  return { show: show };
})();

/**
 * Reminder scheduler — fires the due-popup at the EXACT scheduled time.
 *
 * The server's fire_due_reminders task is a 30s poll: a reliable backstop (for
 * when no Kanzen tab is open, or for other devices) but up to ~30s late. To
 * make the popup feel instant while the user has the app open, we fetch their
 * upcoming reminders and set a precise setTimeout per reminder that calls
 * ReminderAlerts.show() right as scheduled_at passes. ReminderAlerts dedups by
 * reminder id + due-time, so the instant timer and the later WS notification
 * never double-pop. Re-syncs every few minutes and on reminder LiveBus events
 * so new / rescheduled reminders are picked up promptly.
 */
var ReminderScheduler = (function () {
  var REFRESH_MS = 5 * 60 * 1000;   // re-scan the upcoming window every 5 min
  var HORIZON_MS = 6 * 60 * 1000;   // only arm precise timers within 6 min (> refresh → no gaps)
  var myId = null;
  var timers = Object.create(null); // reminder_id -> { key, timeoutId }

  function getMyId() {
    if (myId) return myId;
    var el = document.querySelector('.sidebar-user[data-current-user-id]');
    myId = el ? el.getAttribute('data-current-user-id') : null;
    return myId;
  }

  // Match the server's recipient rule: the assignee if set, else the creator.
  function isMine(r) {
    var me = getMyId();
    if (!me) return false;
    if (r.assigned_to) return String(r.assigned_to) === String(me);
    return String(r.created_by) === String(me);
  }

  function clearTimer(id) {
    var prev = timers[id];
    if (prev) { clearTimeout(prev.timeoutId); delete timers[id]; }
  }

  function arm(r) {
    if (!r || !r.scheduled_at || !r.id) return;
    // Reassigned to someone else → no longer my alert; drop any pending timer.
    if (!isMine(r)) { clearTimer(r.id); return; }
    var when = new Date(r.scheduled_at).getTime();
    if (isNaN(when)) return;
    var key = r.id + '@' + when;
    var prev = timers[r.id];
    if (prev && prev.key === key) return;          // unchanged → keep the existing timer
    clearTimer(r.id);                              // new or rescheduled → drop the stale timer
    var delay = when - Date.now();
    if (delay <= 0 || delay > HORIZON_MS) return;  // past → server backstop; far → next sync
    var tid = setTimeout(function () {
      delete timers[r.id];
      ReminderAlerts.show({
        id: 'local-' + key,
        title: r.subject || 'Reminder',
        body: '',
        data: {
          reminder_id: r.id,
          scheduled_at: r.scheduled_at,
          priority: r.priority,
          url: '/reminders/',
        },
      });
    }, delay);
    timers[r.id] = { key: key, timeoutId: tid };
  }

  function sync() {
    // NB: Api is a top-level `const` in api.js, so it's a global *lexical*
    // binding (in scope here) but NOT a property of window — never gate on
    // `window.Api`, it's always undefined.
    if (!getMyId() || typeof Api === 'undefined') return;
    // Soonest-first (the model's default ordering), pending only, mine.
    Api.get('/api/v1/crm/reminders/?mine=true&status=pending&page_size=200')
      .then(function (res) {
        var items = (res && (res.results || res)) || [];
        if (!Array.isArray(items)) return;
        var live = Object.create(null);
        items.forEach(function (r) { if (r && r.id) live[r.id] = true; arm(r); });
        // Cancel timers for reminders that fell out of the pending set
        // (completed, cancelled, or rescheduled far out) so they don't pop.
        for (var id in timers) { if (!live[id]) clearTimer(id); }
      })
      .catch(function () { /* offline / transient — the interval retries */ });
  }

  function start() {
    if (!getMyId()) return;       // not an authenticated page
    sync();
    setInterval(sync, REFRESH_MS);
    if (window.LiveBus && typeof LiveBus.on === 'function') {
      ['reminder.created', 'reminder.updated', 'reminder.completed',
       'reminder.cancelled', 'reminder.due', 'live.reconnected'].forEach(function (ev) {
        LiveBus.on(ev, function () { setTimeout(sync, 300); });
      });
    }
  }

  return { start: start, sync: sync };
})();

/**
 * Initialize real-time notifications via WebSocket.
 */
function initNotifications() {
  const badge = document.getElementById('notifBadge');
  const list = document.getElementById('notifList');
  const countBadge = document.getElementById('notifCountBadge');
  const bellBtn = document.getElementById('notifDropdown');
  const flyout = document.getElementById('notifFlyout');
  const flyoutIcon = document.getElementById('notifFlyoutIcon');
  const flyoutLabel = document.getElementById('notifFlyoutLabel');
  const flyoutTime = document.getElementById('notifFlyoutTime');
  const flyoutTitle = document.getElementById('notifFlyoutTitle');
  const flyoutBody = document.getElementById('notifFlyoutBody');
  const flyoutClose = document.getElementById('notifFlyoutClose');
  if (!badge || !list) return;

  function updateBadge(count, opts) {
    if (count > 0) {
      badge.textContent = count > 99 ? '99+' : count;
      badge.classList.remove('d-none');
      if (countBadge) { countBadge.textContent = count; countBadge.classList.remove('d-none'); }
      if (opts && opts.bump) {
        badge.classList.remove('is-bumping');
        void badge.offsetWidth;
        badge.classList.add('is-bumping');
        setTimeout(function () { badge.classList.remove('is-bumping'); }, 500);
      }
    } else {
      badge.classList.add('d-none');
      if (countBadge) countBadge.classList.add('d-none');
    }
  }

  // -------- Bell-anchored flyout (transient peek-preview) --------
  var flyoutHideTimer = null;
  var bellRingTimer = null;

  function ringBell() {
    if (!bellBtn) return;
    bellBtn.classList.remove('is-ringing');
    void bellBtn.offsetWidth;
    bellBtn.classList.add('is-ringing');
    if (bellRingTimer) clearTimeout(bellRingTimer);
    bellRingTimer = setTimeout(function () {
      bellBtn.classList.remove('is-ringing');
    }, 950);
  }

  function hideFlyout() {
    if (!flyout) return;
    flyout.classList.remove('is-visible');
    flyout.setAttribute('aria-hidden', 'true');
    if (flyoutHideTimer) { clearTimeout(flyoutHideTimer); flyoutHideTimer = null; }
  }

  function showFlyout(data) {
    if (!flyout) return;
    var cfg = getNotifConfig(data.type);
    if (flyoutIcon) {
      flyoutIcon.style.background = 'color-mix(in srgb, ' + cfg.color + ' 14%, transparent)';
      flyoutIcon.style.color = cfg.color;
      // Build icon element via DOM (icon class comes from a closed allowlist).
      flyoutIcon.textContent = '';
      var iconEl = document.createElement('i');
      iconEl.className = cfg.icon;
      flyoutIcon.appendChild(iconEl);
    }
    if (flyoutLabel) { flyoutLabel.textContent = cfg.label; flyoutLabel.style.color = cfg.color; }
    if (flyoutTime)  { flyoutTime.textContent = 'just now'; }
    if (flyoutTitle) { flyoutTitle.textContent = data.title || 'Notification'; }
    if (flyoutBody)  { flyoutBody.textContent = data.body || ''; }

    flyout.classList.remove('is-visible');
    void flyout.offsetWidth;
    flyout.classList.add('is-visible');
    flyout.setAttribute('aria-hidden', 'false');

    if (flyoutHideTimer) clearTimeout(flyoutHideTimer);
    flyoutHideTimer = setTimeout(hideFlyout, 3000);
  }

  if (flyoutClose) flyoutClose.addEventListener('click', function (e) {
    e.preventDefault(); e.stopPropagation(); hideFlyout();
  });
  if (bellBtn) bellBtn.addEventListener('click', hideFlyout);

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
    updateBadge(current + 1, { bump: true });

    // Bell-anchored popup + ring animation. Replaces the generic Toast
    // so the notification is clearly tied to the bell icon.
    ringBell();
    // Due reminders get a can't-miss centered modal (+chime +desktop notif)
    // instead of the transient 3s flyout.
    if (data.type === 'reminder_due') {
      ReminderAlerts.show(data);
    } else {
      showFlyout(data);
    }

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
    if (avatarEl) {
      if (payload.avatar) {
        // Quote + escape any embedded quotes — guards against CSS-context
        // confusion from filenames containing quotes/parentheses.
        var safeUrl = String(payload.avatar).replace(/"/g, '\\"');
        avatarEl.style.backgroundImage = 'url("' + safeUrl + '")';
        avatarEl.classList.add('has-image');
        avatarEl.textContent = '';
      } else if (payload.initial) {
        avatarEl.style.backgroundImage = '';
        avatarEl.classList.remove('has-image');
        avatarEl.textContent = payload.initial;
      }
    }
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
    tickets:   'sidebarBadgeTickets',
    emails:    'sidebarBadgeEmails',
    messages:  'sidebarBadgeMessages',
    calendar:  'sidebarBadgeCalendar',
    reminders: 'sidebarBadgeReminders',
    knowledge: 'sidebarBadgeKnowledge',
    inbox_hub: 'sidebarBadgeInboxHub',
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
    hub_email_assigned: 'sidebarBadgeInboxHub',
    hub_email_reassigned: 'sidebarBadgeInboxHub',
    hub_email_escalated_to_me: 'sidebarBadgeInboxHub',
    hub_email_sla_breach_warning: 'sidebarBadgeInboxHub',
    hub_email_sla_breached: 'sidebarBadgeInboxHub',
  };
  document.addEventListener('kanzan:notification', function(e) {
    var type = e && e.detail && e.detail.type;
    if (type && NOTIF_TO_BADGE[type]) {
      fetchAndApply();
    }
  });

  // LiveBus: hub_email.created fans out to every connected agent — bump
  // the Inbox Hub badge for any tenant member, not just the recipient
  // of a Notification row (those land on assign/escalate, not on park).
  if (window.LiveBus) {
    LiveBus.onMany(
      ['hub_email.created', 'hub_email.assigned', 'hub_email.reassigned',
       'hub_email.transitioned', 'hub_email.escalated',
       'hub_email.converted_to_ticket', 'hub_email.dismissed'],
      LiveBus.debounce(fetchAndApply, 500)
    );
  }

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

// `const Toast` lives in the module's lexical scope, NOT on `window`, so other
// scripts that feature-detect `window.Toast` (inbox-hub.js, the reminder-due
// fallback below, the Emails page) silently fell back to console.log / alert.
// Publish it explicitly so toasts actually surface everywhere.
window.Toast = Toast;
