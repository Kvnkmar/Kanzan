/**
 * Inbox Hub front-end controller — triage cockpit.
 *
 * The Hub lists emails for the active triage LENS (all-new / unassigned /
 * assigned-to-me / oldest-waiting / sla-at-risk — workload, not severity).
 * Selecting an email shows a read-only mail view PLUS a customer-context
 * card (who is writing + our ticket history with them) so the three triage
 * actions — convert / assign / dismiss — become a one-glance decision. Each
 * action routes the email onward and the pane resets; "working the email"
 * happens downstream in the ticket system.
 *
 * Customer context is fetched from the Hub-local
 * `GET /hub-emails/{id}/context/` action (NOT /contacts/.../context/, which
 * row-scopes agents out of contacts they don't own a ticket for).
 *
 * Security: all dynamic HTML is escaped via the textContent-based `esc()`
 * helper, then run through DOMPurify as a defense-in-depth pass before
 * innerHTML assignment; customer-supplied context (name, company, ticket
 * subjects) is rendered via createElement/textContent only. Inbound email
 * body HTML is sanitised with an explicit allowlist.
 */
(function () {
  'use strict';

  if (!document.getElementById('inboxHubShell')) return;

  // ---------- State ----------
  var PAGE_SIZE = 50;
  var SEARCH_DEBOUNCE_MS = 350;

  var state = {
    items: [],
    counts: { all: 0, unassigned: 0, mine: 0, sla: 0 },
    page: 1,
    next: null,
    prev: null,
    activeLens: 'all',           // all | unassigned | mine | oldest | sla
    search: '',
    selectedId: null,
    selectedDetail: null,
    queues: [],
    statuses: [],
    categories: [],
    users: [],
    usersLoaded: false,
    convertTags: [],             // pill array for the convert panel
    descEditor: null,            // TipTap rich-text editor for the description
    contextCache: {},            // contactId -> context payload (per session)
    contextReqId: 0,             // token to drop stale context paints (fast J/K)
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
    detailActions: document.getElementById('ihDetailActions'),
    detailAvatar: document.getElementById('ihDetailAvatar'),
    detailSubject: document.getElementById('ihDetailSubject'),
    detailSenderName: document.getElementById('ihDetailSenderName'),
    detailSenderEmail: document.getElementById('ihDetailSenderEmail'),
    detailReceivedAt: document.getElementById('ihDetailReceivedAt'),
    detailSlaBadge: document.getElementById('ihDetailSlaBadge'),
    detailBody: document.getElementById('ihDetailBody'),
    detailAttachments: document.getElementById('ihDetailAttachments'),
    contextCard: document.getElementById('ihContextCard'),
    openTicketNudge: document.getElementById('ihOpenTicketNudge'),

    actionConvert: document.getElementById('ihActionConvert'),
    actionAssign: document.getElementById('ihActionAssign'),
    actionDismiss: document.getElementById('ihActionDismiss'),

    countAll: document.getElementById('ihCountAll'),
    countUnassigned: document.getElementById('ihCountUnassigned'),
    countMine: document.getElementById('ihCountMine'),
    countSla: document.getElementById('ihCountSla'),
    lensSla: document.getElementById('ihLensSla'),

    convertPanelEl: document.getElementById('ihConvertPanel'),
    convertForm: document.getElementById('ihConvertForm'),
    convertAlert: document.getElementById('ihConvertAlert'),
    convertSubject: document.getElementById('ihConvertSubject'),
    convertDescEditor: document.getElementById('ihConvertDescEditor'),
    convertQueue: document.getElementById('ihConvertQueue'),
    convertPriority: document.getElementById('ihConvertPriority'),
    convertStatus: document.getElementById('ihConvertStatus'),
    convertAssignee: document.getElementById('ihConvertAssignee'),
    convertCategory: document.getElementById('ihConvertCategory'),
    convertDueDate: document.getElementById('ihConvertDueDate'),
    convertTags: document.getElementById('ihConvertTags'),
    convertTagInput: document.getElementById('ihConvertTagInput'),
    convertSubmit: document.getElementById('ihConvertSubmit'),

    dismissModalEl: document.getElementById('ihDismissModal'),
    dismissForm: document.getElementById('ihDismissForm'),
    dismissReason: document.getElementById('ihDismissReason'),
    dismissSubmit: document.getElementById('ihDismissSubmit'),
  };

  var convertPanel = els.convertPanelEl ? new bootstrap.Offcanvas(els.convertPanelEl) : null;
  var dismissModal = els.dismissModalEl ? new bootstrap.Modal(els.dismissModalEl) : null;

  // Rich-text editor for the convert panel's description. Built once at load
  // (TipTap may still be streaming in via the importmap module — wait for the
  // `tiptap-ready` event, with a timeout fallback to the plain-textarea mode
  // that createRichEditor degrades to). Mirrors templates/pages/tickets/create.html.
  function initConvertDescEditor() {
    if (state.descEditor || !els.convertDescEditor || typeof createRichEditor !== 'function') return;
    state.descEditor = createRichEditor('#ihConvertDescEditor', {
      placeholder: 'Describe the issue — prefilled from the email body…',
      content: '',
    });
  }
  if (window.tiptap) {
    initConvertDescEditor();
  } else {
    window.addEventListener('tiptap-ready', initConvertDescEditor);
    setTimeout(function () { if (!state.descEditor) initConvertDescEditor(); }, 5000);
  }

  // ---------- Constants ----------
  var LENS_LABELS = {
    all: 'Untriaged email',
    unassigned: 'Unassigned email',
    mine: 'Assigned to me',
    oldest: 'Oldest waiting first',
    sla: 'SLA at risk',
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

  function initialsFor(name, email) {
    var src = (name || '').trim() || (email || '').trim();
    if (!src) return '?';
    var parts = src.split(/[\s@.]+/).filter(Boolean);
    if (!parts.length) return src.charAt(0).toUpperCase();
    if (parts.length === 1) return parts[0].charAt(0).toUpperCase();
    return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
  }

  function userDisplayName(u) {
    var name = ((u.first_name || '') + ' ' + (u.last_name || '')).trim();
    return name || u.email || u.id;
  }

  function withVisibility() { return document.visibilityState !== 'hidden'; }

  function toast(level, msg) {
    if (window.Toast && Toast[level]) Toast[level](msg);
    else console.log('[Toast]', level, msg);
  }

  // Compact forward/elapsed duration: 45s, 8m, 1h 4m, 2d 3h. (Kanzan.timeAgo
  // is past-oriented and adds " ago"; we need bare magnitudes for both the
  // "waited" label and the SLA "due in" badge.)
  function humanizeDuration(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return s + 's';
    var m = Math.round(s / 60);
    if (m < 60) return m + 'm';
    var h = Math.floor(m / 60);
    var remM = m % 60;
    if (h < 24) return remM ? (h + 'h ' + remM + 'm') : (h + 'h');
    var d = Math.floor(h / 24);
    var remH = h % 24;
    return remH ? (d + 'd ' + remH + 'h') : (d + 'd');
  }

  function waitedLabel(iso) {
    if (!iso) return '';
    var ms = Date.now() - new Date(iso).getTime();
    if (isNaN(ms)) return '';
    return 'waited ' + humanizeDuration(ms);
  }

  // Small DOM builders for the (customer-supplied) context card — text is
  // always assigned via textContent, never innerHTML.
  function ce(tag, cls) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    return el;
  }

  function statCell(label, value) {
    var cell = ce('div', 'ih-context-stat');
    var v = ce('span', 'ih-context-stat-val');
    v.textContent = String(value);
    var l = ce('span', 'ih-context-stat-label');
    l.textContent = label;
    cell.appendChild(v);
    cell.appendChild(l);
    return cell;
  }

  function formatMrr(mrr) {
    var n = parseFloat(mrr);
    if (isNaN(n)) return String(mrr);
    if (n >= 1000) {
      var k = n / 1000;
      return '$' + (k % 1 === 0 ? k.toFixed(0) : k.toFixed(1)) + 'k';
    }
    return '$' + n.toFixed(0);
  }

  // ---------- Rendering: list ----------
  var EMPTY_LENS_MSG = {
    unassigned: 'No unassigned email — every new message has an owner.',
    mine: 'Nothing assigned to you right now.',
    sla: 'No email is at risk of breaching SLA.',
  };

  function renderEmpty(opts) {
    opts = opts || {};
    if (opts.zero) {
      safeAssign(els.listBody,
        '<div class="ih-list-empty ih-list-empty--zero">' +
        '  <i class="ti ti-circle-check"></i>' +
        '  <p class="ih-list-empty-title">All caught up</p>' +
        '  <p class="ih-list-empty-sub">Inbox zero — no email waiting to be triaged.</p>' +
        '</div>');
      return;
    }
    safeAssign(els.listBody,
      '<div class="ih-list-empty">' +
      '  <i class="ti ti-inbox-off"></i>' +
      '  <p>' + esc(opts.message || 'Nothing here.') + '</p>' +
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
      if (state.search) {
        renderEmpty({ message: 'No email matches your search.' });
      } else if (state.activeLens === 'all' || state.activeLens === 'oldest') {
        renderEmpty({ zero: true });
      } else {
        renderEmpty({ message: EMPTY_LENS_MSG[state.activeLens] || 'Nothing here.' });
      }
      return;
    }

    var html = state.items.map(function (row) {
      var subject = esc(row.subject || '(no subject)');
      var sender = esc(row.sender_name || row.sender_email || 'Unknown sender');
      var waited = esc(waitedLabel(row.received_at || row.created_at));
      var snippet = esc(row.snippet || '');
      var isSelected = (state.selectedId === row.id);
      var known = !!row.contact_id;
      var classes = 'ih-row' + (isSelected ? ' is-selected' : '');
      var avatarClass = 'ih-row-avatar ' +
        (known ? 'ih-row-avatar--known' : 'ih-row-avatar--new');
      var avatar = esc(initialsFor(row.sender_name, row.sender_email));
      var clip = row.has_attachments
        ? '<i class="ti ti-paperclip ih-row-clip" aria-hidden="true"></i>'
        : '';
      var snippetRow = snippet
        ? '<span class="ih-row-snippet">' + snippet + '</span>'
        : '';

      return (
        '<button type="button" class="' + classes + '" ' +
        '        data-row-id="' + esc(row.id) + '" ' +
        '        role="option" ' +
        '        aria-selected="' + (isSelected ? 'true' : 'false') + '">' +
        '  <span class="' + avatarClass + '" aria-hidden="true">' + avatar + '</span>' +
        '  <span class="ih-row-main">' +
        '    <span class="ih-row-top">' +
        '      <span class="ih-row-subject">' + subject + '</span>' + clip +
        '    </span>' +
        snippetRow +
        '    <span class="ih-row-meta">' +
        '      <span class="ih-row-sender">' + sender + '</span>' +
        '      <span class="ih-row-when">' + waited + '</span>' +
        '    </span>' +
        '  </span>' +
        '</button>'
      );
    }).join('');

    safeAssign(els.listBody, html);
  }

  function renderCounts() {
    if (els.countAll) els.countAll.textContent = state.counts.all || 0;
    if (els.countUnassigned) els.countUnassigned.textContent = state.counts.unassigned || 0;
    if (els.countMine) els.countMine.textContent = state.counts.mine || 0;
    if (els.countSla) els.countSla.textContent = state.counts.sla || 0;

    // Self-hiding "SLA at risk" lens — only meaningful when SLA policies
    // exist (default tenants seed none, so every deadline is null). Never
    // show an always-zero lens, and bail out of it if it's the active one.
    if (els.lensSla) {
      var hide = !state.counts.sla;
      els.lensSla.hidden = hide;
      if (hide && state.activeLens === 'sla') {
        state.activeLens = 'all';
        highlightActiveNav();
        renderFilterLabel();
        loadList();
      }
    }
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
    els.filterLabel.textContent = LENS_LABELS[state.activeLens] || 'Untriaged email';
  }

  function highlightActiveNav() {
    var links = els.nav.querySelectorAll('.ih-nav-link');
    links.forEach(function (a) { a.classList.remove('active'); });
    var match = els.nav.querySelector('.ih-nav-link[data-lens="' + state.activeLens + '"]');
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

    var senderName = row.sender_name || '';
    var senderEmail = row.sender_email || '';

    els.detailAvatar.textContent = initialsFor(senderName, senderEmail);
    els.detailSubject.textContent = row.subject || '(no subject)';
    els.detailSenderName.textContent = senderName || senderEmail || 'Unknown sender';
    els.detailSenderEmail.textContent = senderEmail;
    els.detailSenderEmail.href = senderEmail ? ('mailto:' + senderEmail) : '#';
    var received = row.received_at || row.created_at;
    els.detailReceivedAt.textContent = received
      ? (fmtDateTime(received) + ' · ' + waitedLabel(received))
      : '';
    renderSlaBadge(row);

    // Body: prefer HTML, fall back to text. Always sanitise.
    var inbound = row.inbound || {};
    var bodyHtml = inbound.body_html || '';
    var bodyText = inbound.body_text || '';
    if (bodyHtml.trim()) {
      els.detailBody.innerHTML = sanitizeEmailBody(bodyHtml);
    } else if (bodyText.trim()) {
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

    // Customer-sent attachments — rendered by the shared Kanzan helper so the
    // Inbox Hub and Emails page stay identical.
    if (window.Kanzan && Kanzan.renderMailAttachments) {
      Kanzan.renderMailAttachments(els.detailAttachments, row.attachments);
    } else if (els.detailAttachments) {
      els.detailAttachments.hidden = true;
    }

    // Reset scroll to the top of the new message.
    var scroll = els.detailView.querySelector('.ih-detail-scroll');
    if (scroll) scroll.scrollTop = 0;

    showDetailView();
  }

  // SLA badge in the detail header. Honest urgency: shown only when a
  // response deadline actually exists (default tenants have none), with a
  // tone that escalates as the deadline nears.
  function renderSlaBadge(row) {
    var badge = els.detailSlaBadge;
    if (!badge) return;
    badge.className = 'ih-sla-badge';
    var due = row.sla_response_due_at;
    if (!due) { badge.hidden = true; badge.textContent = ''; return; }

    if (row.response_breached) {
      badge.classList.add('ih-sla-badge--danger');
      badge.textContent = 'response overdue';
      badge.hidden = false;
      return;
    }
    var ms = new Date(due).getTime() - Date.now();
    if (isNaN(ms)) { badge.hidden = true; return; }
    if (ms <= 0) {
      badge.classList.add('ih-sla-badge--danger');
      badge.textContent = 'response overdue';
      badge.hidden = false;
      return;
    }
    var mins = ms / 60000;
    var tone = mins <= 15 ? 'danger' : (mins <= 60 ? 'warning' : 'info');
    badge.classList.add('ih-sla-badge--' + tone);
    badge.textContent = 'response due in ' + humanizeDuration(ms);
    badge.hidden = false;
  }

  // ---------- Rendering: customer context ----------
  function hideContextCard() {
    if (els.contextCard) {
      els.contextCard.hidden = true;
      els.contextCard.replaceChildren();
    }
  }

  // Fetch + render the customer-context card for the selected row. Fired in
  // parallel with the detail load (uses contact_id already on the list row).
  function loadContextFor(row) {
    hideContextCard();
    renderOpenTicketNudge(null);
    if (!row) return;
    if (!row.contact_id) { renderFirstContact(row); return; }

    var cid = row.contact_id;
    var token = ++state.contextReqId;
    if (state.contextCache[cid]) {
      applyContext(state.contextCache[cid], token, row);
      return;
    }
    Api.get('/api/v1/inbox-hub/hub-emails/' + encodeURIComponent(row.id) + '/context/')
      .then(function (data) {
        state.contextCache[cid] = data;
        applyContext(data, token, row);
      })
      .catch(function () {
        if (token === state.contextReqId) renderFirstContact(row);
      });
  }

  function applyContext(data, token, row) {
    // A newer selection won the race — drop this stale paint.
    if (token !== state.contextReqId) return;
    if (!data || !data.contact) { renderFirstContact(row); return; }
    renderContext(data);
    renderOpenTicketNudge(data.stats, data.recent_tickets);
  }

  function renderContext(data) {
    var card = els.contextCard;
    if (!card) return;
    var c = data.contact || {};
    var stats = data.stats || {};
    var recent = data.recent_tickets || [];
    var known = (stats.total_tickets || 0) > 0;

    card.replaceChildren();

    // Head: avatar + name/email + known/new badge
    var head = ce('div', 'ih-context-head');
    var av = ce('div', 'ih-context-avatar');
    av.textContent = initialsFor(c.name, c.email);
    head.appendChild(av);

    var idWrap = ce('div', 'ih-context-id');
    var nameEl = ce('span', 'ih-context-name');
    nameEl.textContent = c.name || c.email || 'Unknown';
    var emailEl = ce('span', 'ih-context-email');
    emailEl.textContent = c.email || '';
    idWrap.appendChild(nameEl);
    idWrap.appendChild(emailEl);
    head.appendChild(idWrap);

    var badge = ce('span', 'ih-context-badge ' +
      (known ? 'ih-context-badge--known' : 'ih-context-badge--new'));
    badge.textContent = known ? 'Known customer' : 'New customer';
    head.appendChild(badge);
    card.appendChild(head);

    // Meta line: company · MRR · health
    var metaBits = [];
    if (c.company) metaBits.push(c.company);
    if (c.account) {
      if (c.account.mrr) metaBits.push('MRR ' + formatMrr(c.account.mrr));
      if (typeof c.account.health_score === 'number') {
        metaBits.push('Health ' + c.account.health_score);
      }
    }
    if (metaBits.length) {
      var meta = ce('div', 'ih-context-meta');
      meta.textContent = metaBits.join(' · ');
      card.appendChild(meta);
    }

    // Bounce warning
    if (c.email_bouncing) {
      var warn = ce('div', 'ih-warn');
      warn.appendChild(ce('i', 'ti ti-alert-triangle'));
      warn.appendChild(document.createTextNode(' This email is bouncing'));
      card.appendChild(warn);
    }

    // Stats strip
    var statsWrap = ce('div', 'ih-context-stats');
    statsWrap.appendChild(statCell('Total', stats.total_tickets != null ? stats.total_tickets : '—'));
    statsWrap.appendChild(statCell('Open', stats.open_tickets != null ? stats.open_tickets : '—'));
    statsWrap.appendChild(statCell('CSAT', stats.avg_csat != null ? stats.avg_csat : '—'));
    statsWrap.appendChild(statCell('Last', stats.last_ticket_at ? timeAgo(stats.last_ticket_at) : '—'));
    card.appendChild(statsWrap);

    // Recent tickets (clickable)
    if (recent.length) {
      var tlist = ce('div', 'ih-context-tickets');
      recent.forEach(function (t) {
        var a = document.createElement('a');
        a.className = 'ih-tkt-row';
        a.href = '/tickets/' + encodeURIComponent(t.number) + '/';
        var num = ce('span', 'ih-tkt-num');
        num.textContent = '#' + t.number;
        var subj = ce('span', 'ih-tkt-subject');
        subj.textContent = t.subject || '(no subject)';
        var st = ce('span', 'ih-tkt-status');
        st.textContent = t.status || '—';
        // status_color is a DB hex — assign via a CSS var (data, not a
        // source literal) so the theme-check stays clean.
        if (t.status_color) st.style.setProperty('--badge-color', t.status_color);
        a.appendChild(num);
        a.appendChild(subj);
        a.appendChild(st);
        tlist.appendChild(a);
      });
      card.appendChild(tlist);
    }

    card.hidden = false;
  }

  // Minimal card for an unknown sender (no Contact row, or context 404/empty).
  function renderFirstContact(row) {
    var card = els.contextCard;
    if (!card) return;
    var name = (row && row.sender_name) || '';
    var email = (row && row.sender_email) || '';

    card.replaceChildren();
    var head = ce('div', 'ih-context-head');
    var av = ce('div', 'ih-context-avatar');
    av.textContent = initialsFor(name, email);
    head.appendChild(av);

    var idWrap = ce('div', 'ih-context-id');
    var nameEl = ce('span', 'ih-context-name');
    nameEl.textContent = name || email || 'Unknown sender';
    var emailEl = ce('span', 'ih-context-email');
    emailEl.textContent = email || '';
    idWrap.appendChild(nameEl);
    idWrap.appendChild(emailEl);
    head.appendChild(idWrap);

    var badge = ce('span', 'ih-context-badge ih-context-badge--new');
    badge.textContent = 'First contact';
    head.appendChild(badge);
    card.appendChild(head);

    card.hidden = false;
  }

  // "⚠ N open tickets" nudge in the action bar — assign/merge instead of
  // spawning a duplicate. Links to the most recent ticket for this contact.
  function renderOpenTicketNudge(stats, recent) {
    var nudge = els.openTicketNudge;
    if (!nudge) return;
    var open = stats && stats.open_tickets;
    if (!open) {
      nudge.hidden = true;
      nudge.removeAttribute('href');
      nudge.replaceChildren();
      return;
    }
    nudge.replaceChildren();
    nudge.appendChild(ce('i', 'ti ti-alert-triangle'));
    nudge.appendChild(document.createTextNode(
      ' ' + open + ' open ticket' + (open > 1 ? 's' : '')));
    var first = (recent || [])[0];
    if (first) nudge.href = '/tickets/' + encodeURIComponent(first.number) + '/';
    else nudge.removeAttribute('href');
    nudge.hidden = false;
  }

  // ---------- Data fetching ----------
  function buildListQuery() {
    var params = new URLSearchParams();
    params.set('page_size', PAGE_SIZE);
    params.set('page', state.page);
    switch (state.activeLens) {
      case 'unassigned':
        params.set('state', 'new');
        params.set('assignee', 'unassigned');
        break;
      case 'mine':
        // Assigned mail has left `new`, so don't constrain state here.
        params.set('assignee', 'me');
        break;
      case 'oldest':
        params.set('state', 'new');
        params.set('ordering', 'created_at');   // oldest waiting first
        break;
      case 'sla':
        params.set('sla_risk', 'true');
        params.set('ordering', 'sla_response_due_at');   // soonest deadline first
        break;
      case 'all':
      default:
        params.set('state', 'new');
        break;
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
        // If the selected email is no longer in the (untriaged) list, the
        // detail pane is stale — reset it.
        if (state.selectedId &&
            !state.items.some(function (r) { return r.id === state.selectedId; })) {
          state.selectedId = null;
          state.selectedDetail = null;
          showDetailEmpty();
        }
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
    var base = '/api/v1/inbox-hub/hub-emails/?page_size=1';
    return Promise.all([
      Api.get(base + '&state=new').catch(function () { return null; }),
      Api.get(base + '&state=new&assignee=unassigned').catch(function () { return null; }),
      Api.get(base + '&assignee=me').catch(function () { return null; }),
      Api.get(base + '&sla_risk=true').catch(function () { return null; }),
    ]).then(function (results) {
      state.counts.all = results[0] ? (results[0].count || 0) : 0;
      state.counts.unassigned = results[1] ? (results[1].count || 0) : 0;
      state.counts.mine = results[2] ? (results[2].count || 0) : 0;
      state.counts.sla = results[3] ? (results[3].count || 0) : 0;
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

  function loadUsers() {
    if (state.usersLoaded) return Promise.resolve(state.users);
    return Api.get('/api/v1/accounts/users/')
      .then(function (data) {
        state.users = (data && data.results) || [];
        state.usersLoaded = true;
        return state.users;
      })
      .catch(function () { return []; });
  }

  function loadConvertChoices() {
    if (state.queues.length && state.statuses.length) return Promise.resolve();
    return Promise.all([
      Api.get('/api/v1/tickets/queues/').catch(function () { return { results: [] }; }),
      Api.get('/api/v1/tickets/ticket-statuses/').catch(function () { return { results: [] }; }),
      Api.get('/api/v1/tickets/ticket-categories/').catch(function () { return { results: [] }; }),
      loadUsers(),
    ]).then(function (results) {
      state.queues = (results[0] && results[0].results) || [];
      state.statuses = (results[1] && results[1].results) || [];
      state.categories = (results[2] && results[2].results) || [];
      populateConvertDropdowns();
    });
  }

  function populateConvertDropdowns() {
    if (!els.convertQueue) return;
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
    // A ticket may not start life closed (server rejects it too) — offer open
    // statuses only.
    state.statuses.filter(function (s) { return !s.is_closed; }).forEach(function (s) {
      var opt = document.createElement('option');
      opt.value = s.id; opt.textContent = s.name;
      els.convertStatus.appendChild(opt);
    });

    if (els.convertCategory) {
      els.convertCategory.replaceChildren();
      var defaultC = document.createElement('option');
      defaultC.value = ''; defaultC.textContent = 'No category';
      els.convertCategory.appendChild(defaultC);
      // Ticket.category is a free-text CharField, so the option value IS the
      // category name (not a pk) — matches the Emails-page create form.
      state.categories.forEach(function (c) {
        var opt = document.createElement('option');
        opt.value = c.name; opt.textContent = c.name;
        els.convertCategory.appendChild(opt);
      });
    }

    if (els.convertAssignee) {
      els.convertAssignee.replaceChildren();
      var defaultA = document.createElement('option');
      defaultA.value = ''; defaultA.textContent = 'Unassigned (or auto)';
      els.convertAssignee.appendChild(defaultA);
      (state.users || []).forEach(function (u) {
        var opt = document.createElement('option');
        opt.value = u.id; opt.textContent = userDisplayName(u);
        els.convertAssignee.appendChild(opt);
      });
    }
  }

  // After any triage action the email leaves the `new` backlog: reset the
  // pane and refresh the list/counts so the routed email drops out.
  function afterTriage() {
    state.selectedId = null;
    state.selectedDetail = null;
    hideContextCard();
    renderOpenTicketNudge(null);
    showDetailEmpty();
    return Promise.all([loadList({ silent: true }), loadCounts()]);
  }

  // ---------- Actions: Convert (full ticket panel) ----------

  // HubEmail priority vocab (low/normal/high/urgent) → Ticket.Priority
  // (low/medium/high/urgent). "normal" has no Ticket equivalent → medium.
  function mapHubPriorityToTicket(p) {
    if (p === 'low' || p === 'high' || p === 'urgent') return p;
    return 'medium';
  }

  function textToHtml(text) {
    if (!text) return '';
    return '<p>' + esc(text).replace(/\n/g, '<br>') + '</p>';
  }

  function clearConvertError() {
    if (!els.convertAlert) return;
    els.convertAlert.classList.add('d-none');
    els.convertAlert.textContent = '';
  }

  function showConvertError(msg) {
    if (!els.convertAlert) { toast('error', msg); return; }
    els.convertAlert.textContent = msg;
    els.convertAlert.classList.remove('d-none');
  }

  // Surface a DRF error body: {detail}, {error}, or {field: [msg] | msg}.
  function firstErrorMessage(err) {
    if (!err) return 'Failed to convert email.';
    if (err.detail) return err.detail;
    if (err.error) return err.error;
    for (var k in err) {
      if (!Object.prototype.hasOwnProperty.call(err, k) || k === '_status') continue;
      var v = err[k];
      var msg = Array.isArray(v) ? v[0] : v;
      if (msg) return (k === 'non_field_errors' ? '' : (k + ': ')) + msg;
    }
    return 'Failed to convert email.';
  }

  // ----- Tags (pill input) -----
  function renderConvertTags() {
    if (!els.convertTags) return;
    els.convertTags.replaceChildren();
    state.convertTags.forEach(function (tag, idx) {
      var pill = ce('span', 'ih-tag');
      pill.appendChild(document.createTextNode(tag));
      var x = document.createElement('button');
      x.type = 'button';
      x.className = 'ih-tag-x';
      x.setAttribute('aria-label', 'Remove tag ' + tag);
      x.appendChild(ce('i', 'ti ti-x'));
      x.addEventListener('click', function () {
        state.convertTags.splice(idx, 1);
        renderConvertTags();
      });
      pill.appendChild(x);
      els.convertTags.appendChild(pill);
    });
  }

  function addConvertTag(raw) {
    var tag = (raw || '').trim().slice(0, 50);
    if (!tag) return;
    if (state.convertTags.indexOf(tag) === -1) {
      state.convertTags.push(tag);
      renderConvertTags();
    }
  }

  function openConvertPanel() {
    if (!state.selectedDetail || !convertPanel) return;
    var d = state.selectedDetail;
    clearConvertError();

    // Subject — editable, defaults to the email subject.
    els.convertSubject.value = d.subject || '';
    // Priority — map the HubEmail priority into the Ticket vocab.
    els.convertPriority.value = mapHubPriorityToTicket(d.priority || '');
    // Category + due date reset; tags cleared.
    if (els.convertCategory) els.convertCategory.value = '';
    if (els.convertDueDate) {
      if (els.convertDueDate._flatpickr) els.convertDueDate._flatpickr.clear();
      els.convertDueDate.value = '';
    }
    state.convertTags = [];
    renderConvertTags();
    if (els.convertTagInput) els.convertTagInput.value = '';

    // Description — prefill the rich editor from the email body.
    if (state.descEditor) {
      var inbound = d.inbound || {};
      var bodyHtml = (inbound.body_html || '').trim();
      var content = bodyHtml ? sanitizeEmailBody(bodyHtml) : textToHtml(inbound.body_text || '');
      state.descEditor.setContent(content);
    }

    loadConvertChoices().then(function () {
      // Status defaults to "Default (Open)"; assignee defaults to me if listed.
      if (els.convertStatus) els.convertStatus.value = '';
      if (els.convertAssignee && state.currentUserId) {
        var hasMe = Array.prototype.some.call(els.convertAssignee.options, function (o) {
          return String(o.value) === String(state.currentUserId);
        });
        els.convertAssignee.value = hasMe ? state.currentUserId : '';
      }
      convertPanel.show();
    });
  }

  function submitConvert(e) {
    e.preventDefault();
    if (!state.selectedDetail) return;
    clearConvertError();

    var subject = (els.convertSubject.value || '').trim();
    if (!subject) {
      showConvertError('Subject is required.');
      els.convertSubject.focus();
      return;
    }

    var id = state.selectedDetail.id;
    var payload = { subject: subject };

    if (state.descEditor) {
      var html = state.descEditor.getHTML();
      // TipTap serialises an empty doc as "<p></p>" — treat that as no override
      // so the email body remains the description.
      if (html && html.replace(/<p>\s*<\/p>/g, '').trim()) payload.description = html;
    }
    if (els.convertPriority.value) payload.priority = els.convertPriority.value;
    if (els.convertQueue.value) payload.queue = els.convertQueue.value;
    if (els.convertStatus.value) payload.status = els.convertStatus.value;
    if (els.convertAssignee && els.convertAssignee.value) payload.assignee = els.convertAssignee.value;
    if (els.convertCategory && els.convertCategory.value) payload.category = els.convertCategory.value;
    if (els.convertDueDate && els.convertDueDate.value) payload.due_date = els.convertDueDate.value;
    if (state.convertTags.length) payload.tags = state.convertTags.slice();

    setBtnLoading(els.convertSubmit, 'Creating…');
    Api.post('/api/v1/inbox-hub/hub-emails/' + encodeURIComponent(id) + '/convert-to-ticket/', payload)
      .then(function (data) {
        convertPanel.hide();
        var ticketNumber = (data && data.ticket && data.ticket.number) || null;
        if (ticketNumber) toast('success', 'Created ticket #' + ticketNumber + '.');
        else toast('success', 'Email converted to ticket.');
        afterTriage();
      })
      .catch(function (err) {
        showConvertError(firstErrorMessage(err));
        console.warn('[InboxHub] convert failed:', err);
      })
      .finally(function () {
        resetBtn(els.convertSubmit, 'ti-arrow-right', ' Create ticket');
      });
  }

  // ---------- Actions: Assign (dropdown menu of agents) ----------
  function openAssignMenu() {
    if (!state.selectedDetail || !els.actionAssign) return;
    loadUsers().then(function (users) {
      var me = (users || []).filter(function (u) {
        return String(u.id) === String(state.currentUserId);
      });
      var others = (users || []).filter(function (u) {
        return String(u.id) !== String(state.currentUserId);
      });

      var items = [];
      me.forEach(function (u) {
        items.push({
          icon: 'ti-user-check', label: 'Assign to me', sub: userDisplayName(u),
          onClick: function () { assignTo(u.id); },
        });
      });
      if (me.length && others.length) items.push({ divider: true });
      others.forEach(function (u) {
        items.push({
          icon: 'ti-user', label: userDisplayName(u),
          onClick: function () { assignTo(u.id); },
        });
      });
      if (!items.length) items.push({ disabled: true, label: 'No agents available' });

      openMenu(els.actionAssign, items);
    });
  }

  function assignTo(userId) {
    if (!state.selectedDetail || !userId) return;
    var id = state.selectedDetail.id;
    Api.post('/api/v1/inbox-hub/hub-emails/' + encodeURIComponent(id) + '/assign/', { assignee_id: userId })
      .then(function () { toast('success', 'Email assigned.'); afterTriage(); })
      .catch(function (err) {
        toast('error', (err && (err.detail || err.error ||
          (err.assignee_id && err.assignee_id[0]))) || 'Failed to assign.');
        console.warn('[InboxHub] assign failed:', err);
      });
  }

  // ---------- Floating menu ----------
  // A self-positioned menu appended to <body> so it escapes the detail
  // pane's overflow:hidden (a Bootstrap dropdown would be clipped here).
  var activeMenu = null;

  function closeMenu() {
    if (!activeMenu) return;
    activeMenu.remove();
    activeMenu = null;
    if (els.actionAssign) els.actionAssign.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', onMenuDocClick, true);
    document.removeEventListener('keydown', onMenuKey, true);
    window.removeEventListener('resize', closeMenu);
    window.removeEventListener('scroll', onMenuScroll, true);
  }

  function onMenuDocClick(e) {
    if (activeMenu && !activeMenu.contains(e.target)) closeMenu();
  }

  // Close when the page/detail pane behind the menu scrolls (the menu is
  // position:fixed and would detach from its anchor), but NOT when the user
  // scrolls inside the menu itself.
  function onMenuScroll(e) {
    if (activeMenu && (e.target === activeMenu || activeMenu.contains(e.target))) return;
    closeMenu();
  }

  function onMenuKey(e) {
    if (e.key === 'Escape') { e.stopPropagation(); closeMenu(); }
  }

  function openMenu(anchor, items) {
    closeMenu();
    var menu = document.createElement('div');
    menu.className = 'ih-menu';
    menu.setAttribute('role', 'menu');

    items.forEach(function (it) {
      if (it.divider) {
        var d = document.createElement('div');
        d.className = 'ih-menu-divider';
        menu.appendChild(d);
        return;
      }
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ih-menu-item';
      btn.setAttribute('role', 'menuitem');
      if (it.disabled) btn.disabled = true;
      if (it.icon) {
        var ic = document.createElement('i');
        ic.className = 'ti ' + it.icon;
        btn.appendChild(ic);
      }
      var lab = document.createElement('span');
      lab.className = 'ih-menu-label';
      lab.textContent = it.label;
      btn.appendChild(lab);
      if (it.sub) {
        var sub = document.createElement('span');
        sub.className = 'ih-menu-sub';
        sub.textContent = it.sub;
        btn.appendChild(sub);
      }
      if (!it.disabled && it.onClick) {
        btn.addEventListener('click', function () { closeMenu(); it.onClick(); });
      }
      menu.appendChild(btn);
    });

    document.body.appendChild(menu);
    positionMenu(menu, anchor);
    activeMenu = menu;
    anchor.setAttribute('aria-expanded', 'true');

    // Defer listener wiring so the click that opened the menu doesn't
    // immediately close it.
    setTimeout(function () {
      document.addEventListener('click', onMenuDocClick, true);
      document.addEventListener('keydown', onMenuKey, true);
      window.addEventListener('resize', closeMenu);
      window.addEventListener('scroll', onMenuScroll, true);
    }, 0);
  }

  function positionMenu(menu, anchor) {
    var r = anchor.getBoundingClientRect();
    var margin = 6;
    menu.style.position = 'fixed';
    menu.style.minWidth = Math.max(r.width, 200) + 'px';
    menu.style.visibility = 'hidden';
    // Measure after it is in the DOM.
    var mw = menu.offsetWidth;
    var mh = menu.offsetHeight;
    var top = r.bottom + margin;
    var left = r.left;
    if (top + mh > window.innerHeight - 8) {
      var above = r.top - margin - mh;
      if (above >= 8) top = above;            // flip up when there's room
      else top = Math.max(8, window.innerHeight - 8 - mh);
    }
    if (left + mw > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - 8 - mw);
    }
    menu.style.top = top + 'px';
    menu.style.left = left + 'px';
    menu.style.visibility = '';
  }

  // ---------- Actions: Dismiss ----------
  function openDismissModal() {
    if (!state.selectedDetail || !dismissModal) return;
    els.dismissReason.value = '';
    dismissModal.show();
  }

  function submitDismiss(e) {
    e.preventDefault();
    if (!state.selectedDetail) return;
    var id = state.selectedDetail.id;
    var reason = (els.dismissReason.value || '').trim();

    setBtnLoading(els.dismissSubmit, 'Dismissing…');
    Api.post('/api/v1/inbox-hub/hub-emails/' + encodeURIComponent(id) + '/dismiss/', { reason: reason })
      .then(function () {
        dismissModal.hide();
        toast('success', 'Email dismissed.');
        afterTriage();
      })
      .catch(function (err) {
        toast('error', (err && (err.detail || err.error)) || 'Failed to dismiss.');
        console.warn('[InboxHub] dismiss failed:', err);
      })
      .finally(function () {
        resetBtn(els.dismissSubmit, 'ti-archive', ' Dismiss');
      });
  }

  // ---------- Button spinner helpers ----------
  function setBtnLoading(btn, label) {
    if (!btn) return;
    btn.disabled = true;
    btn.replaceChildren();
    var spin = document.createElement('span');
    spin.className = 'spinner-border spinner-border-sm me-1';
    spin.setAttribute('role', 'status');
    btn.appendChild(spin);
    btn.appendChild(document.createTextNode(label));
  }

  function resetBtn(btn, iconClass, label) {
    if (!btn) return;
    btn.disabled = false;
    btn.replaceChildren();
    var icon = document.createElement('i');
    icon.className = 'ti ' + iconClass + ' me-1';
    btn.appendChild(icon);
    btn.appendChild(document.createTextNode(label));
  }

  // ---------- Event wiring ----------
  function wireEvents() {
    els.nav.addEventListener('click', function (e) {
      var btn = e.target.closest('.ih-nav-link');
      if (!btn) return;
      state.activeLens = btn.dataset.lens || 'all';
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

    els.actionConvert.addEventListener('click', openConvertPanel);
    els.actionAssign.addEventListener('click', function (e) {
      e.stopPropagation();
      if (activeMenu) { closeMenu(); return; }   // toggle
      openAssignMenu();
    });
    els.actionDismiss.addEventListener('click', openDismissModal);

    if (els.convertForm) els.convertForm.addEventListener('submit', submitConvert);
    if (els.dismissForm) els.dismissForm.addEventListener('submit', submitDismiss);

    // Convert panel: tag pill input (Enter / comma to add; Backspace on an
    // empty input removes the last; commit a half-typed tag on blur).
    if (els.convertTagInput) {
      els.convertTagInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ',') {
          e.preventDefault();
          addConvertTag(els.convertTagInput.value);
          els.convertTagInput.value = '';
        } else if (e.key === 'Backspace' && !els.convertTagInput.value && state.convertTags.length) {
          state.convertTags.pop();
          renderConvertTags();
        }
      });
      els.convertTagInput.addEventListener('blur', function () {
        addConvertTag(els.convertTagInput.value);
        els.convertTagInput.value = '';
      });
    }
    // The base flatpickr auto-init only fires on Bootstrap modals — attach the
    // themed date picker when the offcanvas is shown (native picker otherwise).
    if (els.convertPanelEl) {
      els.convertPanelEl.addEventListener('shown.bs.offcanvas', function () {
        if (window.initKanzenFlatpickr) window.initKanzenFlatpickr(els.convertPanelEl);
      });
    }

    document.addEventListener('keydown', function (e) {
      var tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.target && e.target.isContentEditable) return;
      if (document.querySelector('.modal.show, .offcanvas.show')) return;

      if (e.key === 'j') { e.preventDefault(); selectNextRow(1); }
      else if (e.key === 'k') { e.preventDefault(); selectNextRow(-1); }
      else if (e.key === 'c' && state.selectedDetail) { e.preventDefault(); openConvertPanel(); }
      else if (e.key === 'a' && state.selectedDetail) { e.preventDefault(); openAssignMenu(); }
      else if (e.key === 'x' && state.selectedDetail) { e.preventDefault(); openDismissModal(); }
      else if (e.key === 'Escape') {
        state.selectedId = null;
        renderList();
        hideContextCard();
        renderOpenTicketNudge(null);
        showDetailEmpty();
      }
    });
  }

  function selectRow(rowId) {
    if (!rowId) return;
    closeMenu();
    state.selectedId = rowId;
    renderList();
    var el = els.listBody.querySelector('.ih-row[data-row-id="' + rowId + '"]');
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    var row = state.items.find(function (r) { return r.id === rowId; });
    loadDetail(rowId);
    loadContextFor(row);   // fires in parallel using contact_id on the row
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
    // Full-height app surface: let the Hub own the viewport (no page scroll).
    // Scoped to this page via a body class; a full page load clears it.
    document.body.classList.add('ih-page');
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
