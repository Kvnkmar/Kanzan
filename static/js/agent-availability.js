/**
 * Agent Availability - Online/Offline Toggle
 *
 * Provides a toggle switch in the navbar for agents/managers/admins to
 * set their availability status (online/offline).
 *
 * On page load, fetches the persisted status from the server so the
 * toggle reflects the real state.  The user must manually toggle online
 * after login.  Logout sets the status to offline server-side.
 *
 * NOTE: We intentionally do NOT send offline on beforeunload / pagehide
 * because those events fire on every same-site navigation, which would
 * reset the status every time the user clicks a link.  Offline is
 * handled server-side on logout instead.
 */
(function () {
  'use strict';

  const AGENTS_API = '/api/v1/agents/agents';
  const STATUS_URL = AGENTS_API + '/my-status/';

  const toggle = document.getElementById('availabilityToggle');
  const dot = document.getElementById('availabilityDot');
  const container = document.getElementById('availabilityToggleContainer');

  if (!toggle) return;

  // Set a default visual state immediately (avoids invisible dot)
  applyStatusUI('offline');

  // Then fetch the real persisted status from the server
  loadMyStatus();

  toggle.addEventListener('change', function () {
    var newStatus = toggle.checked ? 'online' : 'offline';
    setMyStatus(newStatus);
  });

  async function loadMyStatus() {
    try {
      var data = await Api.get(STATUS_URL);
      applyStatusUI(data.status);
    } catch (_) {
      applyStatusUI('offline');
    }
  }

  async function setMyStatus(newStatus) {
    try {
      toggle.disabled = true;
      var data = await Api.post(STATUS_URL, { status: newStatus });
      applyStatusUI(data.status);
    } catch (err) {
      toggle.checked = !toggle.checked;
      applyStatusUI(toggle.checked ? 'online' : 'offline');
      if (typeof Toast !== 'undefined') {
        Toast.error('Failed to update availability status.');
      }
    } finally {
      toggle.disabled = false;
    }
  }

  function applyStatusUI(status) {
    if (!toggle || !dot) return;

    toggle.checked = status === 'online';

    dot.classList.remove('dot-online', 'dot-offline');
    dot.classList.add(status === 'online' ? 'dot-online' : 'dot-offline');

    if (container) {
      container.title = status === 'online' ? 'Online' : 'Offline';
    }
  }
})();
