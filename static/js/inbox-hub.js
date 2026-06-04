/**
 * Inbox Hub front-end controller.
 *
 * Phase 1 MVP. All dynamic HTML is built by escaping variable content
 * via the textContent-based `esc()` helper, then passed through
 * DOMPurify.sanitize as a defense-in-depth pass before assignment to
 * innerHTML. Email body HTML from the inbound payload is sanitised
 * with an explicit allowlist (no inline event handlers, no <script>,
 * no <iframe>, etc.) since it comes from external senders.
 */
(function () {
  'use strict';

  if (!document.getElementById('inboxHubShell')) return;

  // ---------- State ----------
  var PAGE_SIZE = 50;
  var SEARCH_DEBOUNCE_MS = 350;

  var state = {
    items: [],
    counts: { all: 0, mine: 0, triage: 0 },
    page: 1,
    next: null,
    prev: null,
    activeFilter: 'all',
    activeState: null,
    search: '',
    selectedId: null,
    selectedDetail: null,
    queues: [],
    statuses: [],
    currentUserId: document.getElementById('inboxHubShell').dataset.currentUserId || null,
  };

  // ---------- DOM cache ----------
  var els = {
    shell: document.getElementById('inboxHubShell'),
    nav: document.getElementById('ihNav'),
    listBody: document.getElementById('ihListBody'),
    skeleton: document.getElementById('ihSkeleton'),
    listFooter: document.getElementById('ihListFooter'),
    paginationLabel: document.getElementById('ihPaginationLabel'),
    prevBtn: document.getElementById('ihPrevPage'),
    nextBtn: document.getElementById('ihNextPage'),
    searchInput: document.getElementById('ihSearchInput'),
    refreshBtn: document.getElementById('ihRefreshBtn'),
    filterLabel: document.getElementById('ihActiveFilterLabel'),
    detailEmpty: document.getElementById('ihDetailEmpty'),
    detailView: document.getElementById('ihDetailView'),
    detailStatePill: document.getElementById('ihDetailStatePill'),
    detailPriorityPill: document.getElementById('ihDetailPriorityPill'),
    detailSubject: document.getElementById('ihDetailSubject'),
    detailSenderName: document.getElementById('ihDetailSenderName'),
    detailSenderEmail: document.getElementById('ihDetailSenderEmail'),
    detailReceivedAt: document.getElementById('ihDetailReceivedAt'),
    detailBody: document.getElementById('ihDetailBody'),
    actionConvert: document.getElementById('ihActionConvert'),
    actionDismiss: document.getElementById('ihActionDismiss'),
    actionViewTicket: document.getElementById('ihActionViewTicket'),
    countAll: document.getElementById('ihCountAll'),
    countMine: document.getElementById('ihCountMine'),
    countTriage: document.getElementById('ihCountTriage'),
    convertModalEl: document.getElementById('ihConvertModal'),
    convertForm: document.getElementById('ihConvertForm'),
    convertSubject: document.getElementById('ihConvertSubject'),
    convertQueue: document.getElementById('ihConvertQueue'),
    convertPriority: document.getElementById('ihConvertPriority'),
    convertStatus: document.getElementById('ihConvertStatus'),
    convertAssignee: document.getElementById('ihConvertAssignee'),
    convertSubmit: document.getElementById('ihConvertSubmit'),
    dismissModalEl: document.getElementById('ihDismissModal'),
    dismissForm: document.getElementById('ihDismissForm'),
    dismissReason: document.getElementById('ihDismissReason'),
    dismissSubmit: document.getElementById('ihDismissSubmit'),
  };

  var convertModal = els.convertModalEl ? new bootstrap.Modal(els.convertModalEl) : null;
  var dismissModal = els.dismissModalEl ? new bootstrap.Modal(els.dismissModalEl) : null;

  // ---------- Constants ----------
  var STATE_LABELS = {
    new: 'New', assigned: 'Assigned', in_progress: 'In Progress',
    pending_agent: 'Pending Agent', awaiting_customer: 'Awaiting Customer',
    escalated: 'Escalated', resolved: 'Resolved',
    converted_to_ticket: 'Converted', dismissed: 'Dismissed',
  };
  var PRIORITY_LABELS = { low: 'Low', normal: 'Normal', high: 'High', urgent: 'Urgent' };
  var FILTER_LABELS = {
    all: 'All conversations', mine: 'Assigned to me', triage: 'Triage queue (New)',
  };
  var BODY_SANITIZE_CONFIG = {
    ALLOWED_TAGS: ['p', 'br', 'b', 'i', 'em', 'strong', 'u', 'a', 'ul', 'ol', 'li',
                   'blockquote', 'pre', 'code', 'div', 'span', 'hr', 'h1', 'h2', 'h3',
                   'h4', 'h5', 'h6', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
                   'img'],
    ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class'],
  };

  // ---------- Helpers ----------
  function esc(s) {
    if (s === null || s === undefined) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function safeAssign(el, html) {
    // Defense-in-depth: even though variable content is escaped via esc(),
    // pipe the final string through DOMPurify before assignment.
    if (!el) return;
    el.innerHTML = window.DOMPurify
      ? DOMPurify.sanitize(html, { ADD_ATTR: ['data-row-id', 'role', 'aria-selected', 'aria-label'] })
      : html;
  }

  function sanitizeEmailBody(html) {
    if (window.DOMPurify) return DOMPurify.sanitize(html || '', BODY_SANITIZE_CONFIG);
    return esc(html);
  }

  function fmtDateTime(iso) {
    if (!iso) return '';
    if (window.Kanzan && Kanzan.formatDateTime) return Kanzan.formatDateTime(iso);
    try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
  }

  function timeAgo(iso) {
    if (!iso) return '';
    if (window.Kanzan && Kanzan.timeAgo) return Kanzan.timeAgo(iso);
    return fmtDateTime(iso);
  }

  function withVisibility() { return document.visibilityState !== 'hidden'; }

  function toast(level, msg) {
    if (window.Toast && Toast[level]) Toast[level](msg);
    else console.log('[Toast]', level, msg);
  }

  // ---------- Rendering: list ----------
  function renderEmpty(message) {
    safeAssign(els.listBody,
      '<div class="ih-list-empty">' +
      '  <i class="ti ti-inbox-off"></i>' +
      '  <p>' + esc(message) + '</p>' +
      '</div>');
  }

  function renderListSkeleton() {
    if (els.skeleton) {
      els.skeleton.removeAttribute('hidden');
      els.skeleton.style.display = '';
    }
  }

  function renderList() {
    if (els.skeleton) {
      els.skeleton.setAttribute('hidden', '');
      els.skeleton.style.display = 'none';
    }

    if (!state.items || state.items.length === 0) {
      renderEmpty('No emails match this view yet.');
      return;
    }

    var html = state.items.map(function (row) {
      var subject = esc(row.subject || '(no subject)');
      var sender = esc(row.sender_name || row.sender_email || 'Unknown sender');
      var when = esc(timeAgo(row.received_at || row.created_at));
      var isSelected = (state.selectedId === row.id);
      var classes = 'ih-row' +
        (isSelected ? ' is-selected' : '') +
        ' ih-row--state-' + esc(row.state) +
        ' ih-row--priority-' + esc(row.priority);
      var priorityChip = (row.priority && row.priority !== 'normal')
        ? '<span class="ih-row-priority ih-row-priority--' + esc(row.priority) + '">' +
          esc(PRIORITY_LABELS[row.priority] || row.priority) + '</span>'
        : '';
      var stateChip =
        '<span class="ih-row-state ih-row-state--' + esc(row.state) + '">' +
        esc(STATE_LABELS[row.state] || row.state) + '</span>';
      var ticketChip = row.converted_ticket_number
        ? '<span class="ih-row-ticket">#' + esc(row.converted_ticket_number) + '</span>'
        : '';

      return (
        '<button type="button" class="' + classes + '" ' +
        '        data-row-id="' + esc(row.id) + '" ' +
        '        role="option" ' +
        '        aria-selected="' + (isSelected ? 'true' : 'false') + '">' +
        '  <div class="ih-row-top">' +
        '    <span class="ih-row-subject">' + subject + '</span>' + priorityChip +
        '  </div>' +
        '  <div class="ih-row-meta">' +
        '    <span class="ih-row-sender">' + sender + '</span>' +
        '    <span class="ih-row-when">' + when + '</span>' +
        '  </div>' +
        '  <div class="ih-row-chips">' + stateChip + ticketChip + '</div>' +
        '</button>'
      );
    }).join('');

    safeAssign(els.listBody, html);
  }

  function renderCounts() {
    els.countAll.textContent = state.counts.all || 0;
    els.countMine.textContent = state.counts.mine || 0;
    els.countTriage.textContent = state.counts.triage || 0;
  }

  function renderPagination() {
    if (!state.next && !state.prev && state.page === 1) {
      els.listFooter.setAttribute('hidden', '');
      return;
    }
    els.listFooter.removeAttribute('hidden');
    els.paginationLabel.textContent = 'Page ' + state.page;
    els.prevBtn.disabled = !state.prev;
    els.nextBtn.disabled = !state.next;
  }

  function renderFilterLabel() {
    if (state.activeState) {
      els.filterLabel.textContent = 'State: ' + (STATE_LABELS[state.activeState] || state.activeState);
    } else {
      els.filterLabel.textContent = FILTER_LABELS[state.activeFilter] || 'All conversations';
    }
  }

  function highlightActiveNav() {
    var links = els.nav.querySelectorAll('.ih-nav-link');
    links.forEach(function (a) { a.classList.remove('active'); });
    var match = state.activeState
      ? els.nav.querySelector('.ih-nav-link[data-state="' + state.activeState + '"]')
      : els.nav.querySelector('.ih-nav-link[data-filter="' + state.activeFilter + '"]');
    if (match) match.classList.add('active');
  }

  // ---------- Rendering: detail ----------
  function showDetailEmpty() {
    els.detailView.setAttribute('hidden', '');
    els.detailEmpty.removeAttribute('hidden');
  }

  function showDetailView() {
    els.detailEmpty.setAttribute('hidden', '');
    els.detailView.removeAttribute('hidden');
  }

  function renderDetail(row) {
    if (!row) { showDetailEmpty(); return; }
    state.selectedDetail = row;

    els.detailSubject.textContent = row.subject || '(no subject)';
    els.detailSenderName.textContent = row.sender_name || '';
    els.detailSenderEmail.textContent = row.sender_email || '';
    els.detailSenderEmail.href = row.sender_email ? ('mailto:' + row.sender_email) : '#';
    els.detailReceivedAt.textContent = fmtDateTime(row.received_at || row.created_at);

    els.detailStatePill.className = 'ih-state-pill ih-state-pill--' + (row.state || 'unknown');
    els.detailStatePill.textContent = STATE_LABELS[row.state] || row.state;

    els.detailPriorityPill.className = 'ih-priority-pill ih-priority-pill--' + (row.priority || 'normal');
    els.detailPriorityPill.textContent = PRIORITY_LABELS[row.priority] || row.priority;

    // Body: prefer HTML, fall back to text. Always sanitise.
    var inbound = row.inbound || {};
    var bodyHtml = inbound.body_html || '';
    var bodyText = inbound.body_text || '';
    if (bodyHtml.trim()) {
      // Email body is from external sender — strict allowlist sanitize.
      els.detailBody.innerHTML = sanitizeEmailBody(bodyHtml);
    } else if (bodyText.trim()) {
      // Preserve newlines, escape everything.
      var preNode = document.createElement('pre');
      preNode.className = 'ih-body-plain';
      preNode.textContent = bodyText;
      els.detailBody.replaceChildren(preNode);
    } else {
      var emptyNode = document.createElement('p');
      emptyNode.className = 'ih-body-empty';
      var emItalic = document.createElement('em');
      emItalic.textContent = '(this email has no body)';
      emptyNode.appendChild(emItalic);
      els.detailBody.replaceChildren(emptyNode);
    }

    var isTerminal = (row.state === 'converted_to_ticket' || row.state === 'dismissed');
    els.actionConvert.disabled = isTerminal;
    els.actionDismiss.disabled = isTerminal;

    if (row.converted_ticket_number) {
      els.actionViewTicket.removeAttribute('hidden');
      els.actionViewTicket.href = '/tickets/' + row.converted_ticket_number + '/';
      // Rebuild via DOM nodes to avoid innerHTML on user data.
      els.actionViewTicket.replaceChildren();
      var iconEl = document.createElement('i');
      iconEl.className = 'ti ti-external-link me-1';
      els.actionViewTicket.appendChild(iconEl);
      els.actionViewTicket.appendChild(
        document.createTextNode('Open ticket #' + row.converted_ticket_number)
      );
    } else {
      els.actionViewTicket.setAttribute('hidden', '');
    }

    showDetailView();
  }

  // ---------- Data fetching ----------
  function buildListQuery() {
    var params = new URLSearchParams();
    params.set('page_size', PAGE_SIZE);
    params.set('page', state.page);

    if (state.activeState) {
      params.set('state', state.activeState);
    } else if (state.activeFilter === 'mine') {
      params.set('assignee', 'me');
    } else if (state.activeFilter === 'triage') {
      params.set('state', 'new');
    }
    if (state.search) params.set('search', state.search);
    return '/api/v1/inbox-hub/hub-emails/?' + params.toString();
  }

  function loadList(opts) {
    opts = opts || {};
    if (!opts.silent) renderListSkeleton();
    return Api.get(buildListQuery())
      .then(function (data) {
        state.items = data.results || [];
        state.next = data.next || null;
        state.prev = data.previous || null;
        renderList();
        renderPagination();
      })
      .catch(function (err) {
        safeAssign(els.listBody,
          '<div class="ih-list-error">' +
          '  <i class="ti ti-alert-triangle"></i>' +
          '  <p>Could not load Inbox Hub.</p>' +
          '  <button class="btn btn-sm btn-outline-primary" id="ihRetryLoad">Retry</button>' +
          '</div>');
        var retry = document.getElementById('ihRetryLoad');
        if (retry) retry.addEventListener('click', function () { loadList(); });
        console.warn('[InboxHub] list load failed:', err);
      });
  }

  function loadCounts() {
    return Promise.all([
      Api.get('/api/v1/inbox-hub/hub-emails/?page_size=1').catch(function () { return null; }),
      Api.get('/api/v1/inbox-hub/hub-emails/?page_size=1&assignee=me').catch(function () { return null; }),
      Api.get('/api/v1/inbox-hub/hub-emails/?page_size=1&state=new').catch(function () { return null; }),
    ]).then(function (results) {
      state.counts.all = results[0] ? (results[0].count || 0) : 0;
      state.counts.mine = results[1] ? (results[1].count || 0) : 0;
      state.counts.triage = results[2] ? (results[2].count || 0) : 0;
      renderCounts();
    });
  }

  function loadDetail(id) {
    return Api.get('/api/v1/inbox-hub/hub-emails/' + encodeURIComponent(id) + '/')
      .then(function (data) { renderDetail(data); return data; })
      .catch(function (err) {
        toast('error', 'Could not load that email.');
        console.warn('[InboxHub] detail load failed:', err);
      });
  }

  function loadConvertChoices() {
    if (state.queues.length && state.statuses.length) return Promise.resolve();
    return Promise.all([
      Api.get('/api/v1/tickets/queues/').catch(function () { return { results: [] }; }),
      Api.get('/api/v1/tickets/ticket-statuses/').catch(function () { return { results: [] }; }),
    ]).then(function (results) {
      state.queues = (results[0] && results[0].results) || [];
      state.statuses = (results[1] && results[1].results) || [];
      populateConvertDropdowns();
    });
  }

  function populateConvertDropdowns() {
    if (!els.convertQueue) return;
    // Build options via DOM so attribute values are auto-escaped.
    els.convertQueue.replaceChildren();
    var defaultQ = document.createElement('option');
    defaultQ.value = ''; defaultQ.textContent = 'Default';
    els.convertQueue.appendChild(defaultQ);
    state.queues.forEach(function (q) {
      var opt = document.createElement('option');
      opt.value = q.id; opt.textContent = q.name;
      els.convertQueue.appendChild(opt);
    });

    els.convertStatus.replaceChildren();
    var defaultS = document.createElement('option');
    defaultS.value = ''; defaultS.textContent = 'Default (Open)';
    els.convertStatus.appendChild(defaultS);
    state.statuses.forEach(function (s) {
      var opt = document.createElement('option');
      opt.value = s.id; opt.textContent = s.name;
      els.convertStatus.appendChild(opt);
    });
  }

  // ---------- Actions: Convert ----------
  function openConvertModal() {
    if (!state.selectedDetail || !convertModal) return;
    if (state.selectedDetail.state === 'converted_to_ticket') {
      toast('info', 'Already converted.');
      return;
    }
    els.convertSubject.value = state.selectedDetail.subject || '';
    els.convertPriority.value = state.selectedDetail.priority || '';
    loadConvertChoices().then(function () { convertModal.show(); });
  }

  function submitConvert(e) {
    e.preventDefault();
    if (!state.selectedDetail) return;
    var id = state.selectedDetail.id;
    var payload = {};
    if (els.convertQueue.value) payload.queue_id = els.convertQueue.value;
    if (els.convertStatus.value) payload.status_id = els.convertStatus.value;
    if (els.convertPriority.value) payload.priority = els.convertPriority.value;

    els.convertSubmit.disabled = true;
    els.convertSubmit.replaceChildren();
    var spin = document.createElement('span');
    spin.className = 'spinner-border spinner-border-sm me-1';
    spin.setAttribute('role', 'status');
    els.convertSubmit.appendChild(spin);
    els.convertSubmit.appendChild(document.createTextNode('Creating…'));

    Api.post('/api/v1/inbox-hub/hub-emails/' + encodeURIComponent(id) + '/convert-to-ticket/', payload)
      .then(function (data) {
        convertModal.hide();
        var ticketNumber = (data && data.ticket && data.ticket.number) || null;
        if (ticketNumber) showConvertSuccessToast(ticketNumber);
        else toast('success', 'Email converted to ticket.');
        Promise.all([loadList({ silent: true }), loadDetail(id), loadCounts()]);
      })
      .catch(function (err) {
        var msg = (err && (err.detail || err.error)) || 'Failed to convert email.';
        toast('error', msg);
        console.warn('[InboxHub] convert failed:', err);
      })
      .finally(function () {
        els.convertSubmit.disabled = false;
        els.convertSubmit.replaceChildren();
        var iconEl2 = document.createElement('i');
        iconEl2.className = 'ti ti-arrow-right me-1';
        els.convertSubmit.appendChild(iconEl2);
        els.convertSubmit.appendChild(document.createTextNode(' Create ticket'));
      });
  }

  function showConvertSuccessToast(ticketNumber) {
    // Phase 1 MVP: 8s window before auto-navigate. Phase 2 will replace
    // this with an Undo button + countdown UI.
    var url = '/tickets/' + ticketNumber + '/';
    toast('success', 'Created ticket #' + ticketNumber + '. Opening in 8s…');
    setTimeout(function () {
      if (document.visibilityState !== 'hidden') window.location.href = url;
    }, 8000);
  }

  // ---------- Actions: Dismiss ----------
  function openDismissModal() {
    if (!state.selectedDetail || !dismissModal) return;
    if (state.selectedDetail.state === 'dismissed') {
      toast('info', 'Already dismissed.');
      return;
    }
    els.dismissReason.value = '';
    dismissModal.show();
  }

  function submitDismiss(e) {
    e.preventDefault();
    if (!state.selectedDetail) return;
    var id = state.selectedDetail.id;
    var reason = (els.dismissReason.value || '').trim();

    els.dismissSubmit.disabled = true;
    els.dismissSubmit.replaceChildren();
    var spin = document.createElement('span');
    spin.className = 'spinner-border spinner-border-sm me-1';
    spin.setAttribute('role', 'status');
    els.dismissSubmit.appendChild(spin);
    els.dismissSubmit.appendChild(document.createTextNode('Dismissing…'));

    Api.post('/api/v1/inbox-hub/hub-emails/' + encodeURIComponent(id) + '/dismiss/', { reason: reason })
      .then(function () {
        dismissModal.hide();
        toast('success', 'Email dismissed.');
        Promise.all([loadList({ silent: true }), loadDetail(id), loadCounts()]);
      })
      .catch(function (err) {
        var msg = (err && (err.detail || err.error)) || 'Failed to dismiss.';
        toast('error', msg);
        console.warn('[InboxHub] dismiss failed:', err);
      })
      .finally(function () {
        els.dismissSubmit.disabled = false;
        els.dismissSubmit.replaceChildren();
        var iconEl3 = document.createElement('i');
        iconEl3.className = 'ti ti-archive me-1';
        els.dismissSubmit.appendChild(iconEl3);
        els.dismissSubmit.appendChild(document.createTextNode(' Dismiss'));
      });
  }

  // ---------- Event wiring ----------
  function wireEvents() {
    els.nav.addEventListener('click', function (e) {
      var btn = e.target.closest('.ih-nav-link');
      if (!btn) return;
      if (btn.dataset.filter) {
        state.activeFilter = btn.dataset.filter;
        state.activeState = null;
      } else if (btn.dataset.state) {
        state.activeState = btn.dataset.state;
        state.activeFilter = 'all';
      }
      state.page = 1;
      highlightActiveNav();
      renderFilterLabel();
      loadList();
    });

    els.listBody.addEventListener('click', function (e) {
      var row = e.target.closest('.ih-row');
      if (!row) return;
      selectRow(row.dataset.rowId);
    });

    var searchDebounced = (function () {
      var t = null;
      return function () {
        if (t) clearTimeout(t);
        t = setTimeout(function () {
          state.search = (els.searchInput.value || '').trim();
          state.page = 1;
          loadList();
        }, SEARCH_DEBOUNCE_MS);
      };
    })();
    els.searchInput.addEventListener('input', searchDebounced);

    els.refreshBtn.addEventListener('click', function () { loadList(); loadCounts(); });

    els.prevBtn.addEventListener('click', function () {
      if (!state.prev) return;
      state.page = Math.max(1, state.page - 1);
      loadList();
    });
    els.nextBtn.addEventListener('click', function () {
      if (!state.next) return;
      state.page += 1;
      loadList();
    });

    els.actionConvert.addEventListener('click', openConvertModal);
    els.actionDismiss.addEventListener('click', openDismissModal);

    if (els.convertForm) els.convertForm.addEventListener('submit', submitConvert);
    if (els.dismissForm) els.dismissForm.addEventListener('submit', submitDismiss);

    document.addEventListener('keydown', function (e) {
      var tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.target && e.target.isContentEditable) return;
      if (document.querySelector('.modal.show')) return;

      if (e.key === 'j') { e.preventDefault(); selectNextRow(1); }
      else if (e.key === 'k') { e.preventDefault(); selectNextRow(-1); }
      else if (e.key === 'c' && state.selectedDetail) { e.preventDefault(); openConvertModal(); }
      else if (e.key === 'x' && state.selectedDetail) { e.preventDefault(); openDismissModal(); }
      else if (e.key === 'Escape') {
        state.selectedId = null;
        renderList();
        showDetailEmpty();
      }
    });
  }

  function selectRow(rowId) {
    if (!rowId) return;
    state.selectedId = rowId;
    renderList();
    var el = els.listBody.querySelector('.ih-row[data-row-id="' + rowId + '"]');
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    loadDetail(rowId);
  }

  function selectNextRow(delta) {
    if (!state.items.length) return;
    var idx = state.items.findIndex(function (r) { return r.id === state.selectedId; });
    var nextIdx = (idx === -1)
      ? (delta > 0 ? 0 : state.items.length - 1)
      : idx + delta;
    if (nextIdx < 0 || nextIdx >= state.items.length) return;
    selectRow(state.items[nextIdx].id);
  }

  // ---------- LiveBus ----------
  function wireLiveBus() {
    if (!window.LiveBus) return;

    var refresh = LiveBus.debounce(function () {
      if (!withVisibility()) return;
      loadList({ silent: true });
      loadCounts();
      if (state.selectedId) loadDetail(state.selectedId);
    }, 400);

    LiveBus.onMany(
      [
        'hub_email.created',
        'hub_email.assigned',
        'hub_email.reassigned',
        'hub_email.transitioned',
        'hub_email.escalated',
        'hub_email.converted_to_ticket',
        'hub_email.dismissed',
      ],
      function () { refresh(); }
    );

    LiveBus.on('live.reconnected', function () {
      loadList({ silent: true });
      loadCounts();
      if (state.selectedId) loadDetail(state.selectedId);
    });

    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') {
        loadList({ silent: true });
        loadCounts();
      }
    });
  }

  // ---------- Boot ----------
  function init() {
    wireEvents();
    wireLiveBus();
    highlightActiveNav();
    renderFilterLabel();
    loadList();
    loadCounts();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
