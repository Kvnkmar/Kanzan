/**
 * Agent Availability - Status Dropdown (Gmail-style)
 *
 * Provides a dropdown in the navbar for agents/managers/admins to
 * set their availability. Combines four built-in statuses (online/away/
 * busy/offline) with tenant-curated CustomAgentStatus rows managed by
 * admins/managers from the settings page.
 *
 * On page load fetches the persisted state and the active custom status
 * list so the dropdown reflects the real state.
 */
(function () {
  'use strict';

  const STATUS_URL = '/api/v1/agents/agents/my-status/';
  const CUSTOM_LIST_URL = '/api/v1/agents/custom-statuses/';

  const BUILTIN_LABELS = {
    online: 'Online',
    away: 'Away',
    busy: 'Busy',
    offline: 'Offline'
  };

  const dropdown = document.getElementById('statusDropdown');
  const trigger = document.getElementById('statusDropdownTrigger');
  const menu = document.getElementById('statusDropdownMenu');
  const dot = document.getElementById('statusDot');
  const label = document.getElementById('statusLabel');
  const customList = document.getElementById('statusDropdownCustomList');
  const customDivider = document.getElementById('statusDropdownCustomDivider');

  if (!dropdown || !trigger) return;

  var currentKey = 'offline';        // either built-in slug or custom slug
  var currentCustomId = null;        // UUID when a custom status is active
  var customStatuses = [];           // active CustomAgentStatus rows

  applyState({ key: 'offline', customId: null });

  // Bootstrap: load custom list first, then my-status (so renderer has the labels)
  loadCustomStatuses().then(loadMyStatus);

  // Toggle menu open/close
  trigger.addEventListener('click', function (e) {
    e.stopPropagation();
    dropdown.classList.toggle('open');
  });

  // Close on outside click
  document.addEventListener('click', function (e) {
    if (!dropdown.contains(e.target)) {
      dropdown.classList.remove('open');
    }
  });

  // Close on Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      dropdown.classList.remove('open');
    }
  });

  // Built-in status clicks
  menu.querySelectorAll('.status-dropdown-item[data-status]').forEach(function (item) {
    item.addEventListener('click', function () {
      var newStatus = item.dataset.status;
      if (newStatus === currentKey && !currentCustomId) {
        dropdown.classList.remove('open');
        return;
      }
      setMyStatus({ status: newStatus });
      dropdown.classList.remove('open');
    });
  });

  async function loadMyStatus() {
    try {
      var data = await Api.get(STATUS_URL);
      applyStateFromPayload(data);
    } catch (_) {
      applyState({ key: 'offline', customId: null });
    }
  }

  async function loadCustomStatuses() {
    if (!customList) return;
    try {
      var data = await Api.get(CUSTOM_LIST_URL);
      customStatuses = data.results || data || [];
      renderCustomItems();
    } catch (_) {
      customStatuses = [];
      renderCustomItems();
    }
  }

  function renderCustomItems() {
    if (!customList) return;
    customList.textContent = '';
    if (customDivider) {
      customDivider.style.display = customStatuses.length ? '' : 'none';
    }
    customStatuses.forEach(function (cs) {
      var btn = document.createElement('button');
      btn.className = 'status-dropdown-item';
      btn.type = 'button';
      btn.dataset.customId = cs.id;
      btn.dataset.customSlug = cs.slug;

      var dotEl = document.createElement('span');
      dotEl.className = 'status-dropdown-item-dot';
      dotEl.style.background = cs.color_hex || 'var(--status-info-dot)';
      btn.appendChild(dotEl);

      var textWrap = document.createElement('div');
      textWrap.className = 'status-dropdown-item-text';
      var labelEl = document.createElement('span');
      labelEl.className = 'status-dropdown-item-label';
      labelEl.textContent = cs.label;
      textWrap.appendChild(labelEl);
      if (cs.description) {
        var descEl = document.createElement('span');
        descEl.className = 'status-dropdown-item-desc';
        descEl.textContent = cs.description;
        textWrap.appendChild(descEl);
      }
      btn.appendChild(textWrap);

      var check = document.createElement('i');
      check.className = 'ti ti-check status-dropdown-check';
      btn.appendChild(check);

      btn.addEventListener('click', function () {
        if (currentCustomId === cs.id) {
          dropdown.classList.remove('open');
          return;
        }
        setMyStatus({ custom_status_id: cs.id });
        dropdown.classList.remove('open');
      });

      customList.appendChild(btn);
    });
    // Re-apply active state in case custom statuses re-rendered after load
    refreshActiveMarkers();
  }

  async function setMyStatus(payload) {
    var prev = { key: currentKey, customId: currentCustomId };
    // Optimistic update
    if (payload.status) {
      applyState({ key: payload.status, customId: null });
    } else if (payload.custom_status_id) {
      var cs = customStatuses.find(function (c) { return c.id === payload.custom_status_id; });
      if (cs) applyState({ key: cs.slug, customId: cs.id, customRow: cs });
    }
    trigger.style.pointerEvents = 'none';
    trigger.style.opacity = '0.7';

    try {
      var data = await Api.post(STATUS_URL, payload);
      applyStateFromPayload(data);
    } catch (err) {
      applyState(prev);
      if (typeof Toast !== 'undefined') {
        Toast.error('Failed to update status.');
      }
    } finally {
      trigger.style.pointerEvents = '';
      trigger.style.opacity = '';
    }
  }

  function applyStateFromPayload(data) {
    if (data && data.custom_status && data.custom_status.id) {
      applyState({
        key: data.custom_status.slug,
        customId: data.custom_status.id,
        customRow: data.custom_status,
      });
    } else {
      applyState({ key: (data && data.status) || 'offline', customId: null });
    }
  }

  function applyState(state) {
    currentKey = state.key || 'offline';
    currentCustomId = state.customId || null;

    if (dot) {
      if (currentCustomId) {
        var row = state.customRow || customStatuses.find(function (c) { return c.id === currentCustomId; });
        dot.className = 'status-dropdown-dot';
        dot.style.background = (row && row.color_hex) || 'var(--status-info-dot)';
        dot.style.boxShadow = 'none';
      } else {
        dot.className = 'status-dropdown-dot dot-' + currentKey;
        dot.style.background = '';
        dot.style.boxShadow = '';
      }
    }

    if (label) {
      if (currentCustomId) {
        var row2 = state.customRow || customStatuses.find(function (c) { return c.id === currentCustomId; });
        label.textContent = (row2 && row2.label) || 'Status';
      } else {
        label.textContent = BUILTIN_LABELS[currentKey] || 'Offline';
      }
    }

    refreshActiveMarkers();
  }

  function refreshActiveMarkers() {
    menu.querySelectorAll('.status-dropdown-item').forEach(function (item) {
      var isMatch = false;
      if (currentCustomId && item.dataset.customId) {
        isMatch = item.dataset.customId === currentCustomId;
      } else if (!currentCustomId && item.dataset.status) {
        isMatch = item.dataset.status === currentKey;
      }
      item.classList.toggle('active', isMatch);
    });
  }
})();
