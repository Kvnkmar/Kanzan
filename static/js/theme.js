/**
 * Theme switcher for Kanzen Suite.
 * Supports Light / Dark / System modes with localStorage persistence.
 * Loaded before body renders to prevent flash of wrong theme.
 */
(function() {
  var STORAGE_KEY = 'kanzan_theme';

  function getSystemTheme() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function getEffectiveTheme(preference) {
    if (preference === 'system') return getSystemTheme();
    if (!preference) return 'light'; // Default to light on first visit
    return preference;
  }

  function applyTheme(preference) {
    var effective = getEffectiveTheme(preference);
    document.documentElement.setAttribute('data-bs-theme', effective);
    updateToggleIcon(preference || 'light');
  }

  function updateToggleIcon(preference) {
    var btn = document.getElementById('themeToggleBtn');
    if (!btn) return;
    var icon = btn.querySelector('i');
    if (!icon) return;

    icon.className = '';
    if (preference === 'dark') {
      icon.className = 'ti ti-moon';
      btn.title = 'Dark mode (click to switch)';
    } else if (preference === 'light') {
      icon.className = 'ti ti-sun';
      btn.title = 'Light mode (click to switch)';
    } else {
      icon.className = 'ti ti-device-desktop';
      btn.title = 'System theme (click to switch)';
    }
  }

  function cycleTheme() {
    var current = localStorage.getItem(STORAGE_KEY) || 'light';
    var next;
    if (current === 'light') next = 'dark';
    else if (current === 'dark') next = 'system';
    else next = 'light';

    localStorage.setItem(STORAGE_KEY, next);
    applyTheme(next);
  }

  // Apply immediately (before DOM ready) to prevent flash
  var saved = localStorage.getItem(STORAGE_KEY);
  applyTheme(saved);

  // Listen for system theme changes
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function() {
    var pref = localStorage.getItem(STORAGE_KEY);
    if (!pref || pref === 'system') {
      applyTheme('system');
    }
  });

  // Bind toggle button after DOM ready
  document.addEventListener('DOMContentLoaded', function() {
    var pref = localStorage.getItem(STORAGE_KEY) || 'light';
    updateToggleIcon(pref);

    var btn = document.getElementById('themeToggleBtn');
    if (btn) {
      btn.addEventListener('click', cycleTheme);
    }
  });
})();
