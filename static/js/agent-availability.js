/**
 * Agent Availability - Status Dropdown (Gmail-style)
 *
 * Provides a dropdown in the navbar for agents/managers/admins to
 * set their availability status (online/away/busy/offline).
 *
 * On page load, fetches the persisted status from the server so the
 * dropdown reflects the real state.
 */
(function () {
  'use strict';

  const STATUS_URL = '/api/v1/agents/agents/my-status/';

  const STATUS_LABELS = {
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

  if (!dropdown || !trigger) return;

  var currentStatus = 'offline';

  // Set default visual state, then load real status
  applyStatus('offline');
  loadMyStatus();

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

  // Status item clicks
  menu.querySelectorAll('.status-dropdown-item').forEach(function (item) {
    item.addEventListener('click', function () {
      var newStatus = item.dataset.status;
      if (newStatus === currentStatus) {
        dropdown.classList.remove('open');
        return;
      }
      setMyStatus(newStatus);
      dropdown.classList.remove('open');
    });
  });

  async function loadMyStatus() {
    try {
      var data = await Api.get(STATUS_URL);
      applyStatus(data.status || 'offline');
    } catch (_) {
      applyStatus('offline');
    }
  }

  async function setMyStatus(newStatus) {
    var previousStatus = currentStatus;
    // Optimistic update
    applyStatus(newStatus);
    trigger.style.pointerEvents = 'none';
    trigger.style.opacity = '0.7';

    try {
      var data = await Api.post(STATUS_URL, { status: newStatus });
      applyStatus(data.status);
    } catch (err) {
      applyStatus(previousStatus);
      if (typeof Toast !== 'undefined') {
        Toast.error('Failed to update status.');
      }
    } finally {
      trigger.style.pointerEvents = '';
      trigger.style.opacity = '';
    }
  }

  function applyStatus(status) {
    currentStatus = status;

    // Update trigger dot
    if (dot) {
      dot.className = 'status-dropdown-dot dot-' + status;
    }

    // Update trigger label
    if (label) {
      label.textContent = STATUS_LABELS[status] || 'Offline';
    }

    // Update active state on menu items
    menu.querySelectorAll('.status-dropdown-item').forEach(function (item) {
      if (item.dataset.status === status) {
        item.classList.add('active');
      } else {
        item.classList.remove('active');
      }
    });
  }
})();
