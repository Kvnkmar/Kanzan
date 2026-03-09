/**
 * Command Palette (Cmd+K / Ctrl+K) for Kanzen Suite.
 * Provides quick navigation, search, and actions.
 */
(function() {
  var isOpen = false;
  var activeIndex = -1;
  var searchTimeout = null;

  // Static pages for navigation
  var PAGES = [
    { title: 'Dashboard',  icon: 'ti ti-layout-dashboard',  url: '/dashboard/',  desc: 'Overview' },
    { title: 'Tickets',    icon: 'ti ti-ticket',            url: '/tickets/',    desc: 'Support tickets' },
    { title: 'Contacts',   icon: 'ti ti-address-book',      url: '/contacts/',   desc: 'CRM contacts' },
    { title: 'Boards',     icon: 'ti ti-layout-kanban',     url: '/kanban/',     desc: 'Kanban boards' },
    { title: 'Calendar',   icon: 'ti ti-calendar',          url: '/calendar/',   desc: 'Events & schedule' },
    { title: 'Messages',   icon: 'ti ti-message',           url: '/messaging/',  desc: 'Conversations' },
    { title: 'Analytics',  icon: 'ti ti-chart-bar',         url: '/analytics/',  desc: 'Reports & metrics' },
    { title: 'Settings',   icon: 'ti ti-settings',          url: '/settings/',   desc: 'Tenant settings' },
    { title: 'Users',      icon: 'ti ti-user-cog',          url: '/users/',      desc: 'Team members' },
    { title: 'Agents',     icon: 'ti ti-users',             url: '/agents/',     desc: 'Agent management' },
    { title: 'Billing',    icon: 'ti ti-credit-card',       url: '/billing/',    desc: 'Plans & invoices' },
    { title: 'Profile',    icon: 'ti ti-user',              url: '/profile/',    desc: 'Your profile' },
  ];

  var ACTIONS = [
    { title: 'New Ticket',  icon: 'ti ti-plus', url: '/tickets/new/', desc: 'Create a support ticket' },
    { title: 'New Contact', icon: 'ti ti-plus', url: '/contacts/new/', desc: 'Add a new contact' },
  ];

  function escapeHtml(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function createPaletteHTML() {
    var backdrop = document.createElement('div');
    backdrop.className = 'cmd-palette-backdrop';
    backdrop.id = 'cmdPaletteBackdrop';

    var palette = document.createElement('div');
    palette.className = 'cmd-palette';
    palette.id = 'cmdPalette';
    palette.innerHTML =
      '<div class="cmd-palette-input-wrap">' +
        '<i class="ti ti-search"></i>' +
        '<input type="text" class="cmd-palette-input" id="cmdPaletteInput" placeholder="Search pages, tickets, contacts..." autocomplete="off">' +
      '</div>' +
      '<div class="cmd-palette-body" id="cmdPaletteBody"></div>' +
      '<div class="cmd-palette-footer">' +
        '<span><kbd>&uarr;&darr;</kbd> Navigate</span>' +
        '<span><kbd>Enter</kbd> Open</span>' +
        '<span><kbd>Esc</kbd> Close</span>' +
      '</div>';

    document.body.appendChild(backdrop);
    document.body.appendChild(palette);

    backdrop.addEventListener('click', closePalette);
  }

  function openPalette() {
    if (isOpen) return;
    isOpen = true;
    activeIndex = -1;

    var backdrop = document.getElementById('cmdPaletteBackdrop');
    var palette = document.getElementById('cmdPalette');
    var input = document.getElementById('cmdPaletteInput');

    backdrop.classList.add('show');
    palette.classList.add('show');
    input.value = '';
    renderDefaultView();

    requestAnimationFrame(function() { input.focus(); });
  }

  function closePalette() {
    if (!isOpen) return;
    isOpen = false;

    var backdrop = document.getElementById('cmdPaletteBackdrop');
    var palette = document.getElementById('cmdPalette');
    backdrop.classList.remove('show');
    palette.classList.remove('show');
  }

  function renderDefaultView() {
    var body = document.getElementById('cmdPaletteBody');
    var html = '';

    // Recent pages from localStorage
    var recent = getRecentPages();
    if (recent.length > 0) {
      html += '<div class="cmd-palette-section">Recent</div>';
      recent.forEach(function(r) {
        html += renderItem(r.title, r.icon || 'ti ti-clock', r.url, r.desc);
      });
    }

    // Quick actions
    html += '<div class="cmd-palette-section">Quick Actions</div>';
    ACTIONS.forEach(function(a) {
      html += renderItem(a.title, a.icon, a.url, a.desc);
    });

    // Pages
    html += '<div class="cmd-palette-section">Pages</div>';
    PAGES.forEach(function(p) {
      html += renderItem(p.title, p.icon, p.url, p.desc);
    });

    body.innerHTML = html;
    bindItemClicks(body);
  }

  function renderItem(title, icon, url, desc) {
    return '<a class="cmd-palette-item" href="' + escapeHtml(url) + '" data-title="' + escapeHtml(title) + '">' +
      '<div class="cmd-palette-item-icon"><i class="' + escapeHtml(icon) + '"></i></div>' +
      '<div class="cmd-palette-item-text">' +
        '<span class="cmd-palette-item-title">' + escapeHtml(title) + '</span>' +
        (desc ? '<span class="cmd-palette-item-desc">' + escapeHtml(desc) + '</span>' : '') +
      '</div>' +
    '</a>';
  }

  function bindItemClicks(container) {
    container.querySelectorAll('.cmd-palette-item').forEach(function(item) {
      item.addEventListener('click', function(e) {
        var url = this.getAttribute('href');
        var title = this.dataset.title;
        if (url && url !== '#') {
          saveRecentPage(title, url);
          closePalette();
        }
      });
    });
  }

  function runSearch(query) {
    var body = document.getElementById('cmdPaletteBody');
    var q = query.toLowerCase();

    // Filter pages
    var matchedPages = PAGES.filter(function(p) {
      return p.title.toLowerCase().includes(q) || (p.desc && p.desc.toLowerCase().includes(q));
    });

    var matchedActions = ACTIONS.filter(function(a) {
      return a.title.toLowerCase().includes(q) || (a.desc && a.desc.toLowerCase().includes(q));
    });

    var html = '';

    if (matchedActions.length > 0) {
      html += '<div class="cmd-palette-section">Actions</div>';
      matchedActions.forEach(function(a) { html += renderItem(a.title, a.icon, a.url, a.desc); });
    }

    if (matchedPages.length > 0) {
      html += '<div class="cmd-palette-section">Pages</div>';
      matchedPages.forEach(function(p) { html += renderItem(p.title, p.icon, p.url, p.desc); });
    }

    // Search tickets & contacts via API
    if (q.length >= 2) {
      html += '<div class="cmd-palette-section" id="cmdPaletteApiResults">Searching...</div>';
      body.innerHTML = html;
      bindItemClicks(body);

      Promise.all([
        typeof Api !== 'undefined' ? Api.get('/api/v1/tickets/tickets/?search=' + encodeURIComponent(query) + '&page_size=5').catch(function() { return { results: [] }; }) : Promise.resolve({ results: [] }),
        typeof Api !== 'undefined' ? Api.get('/api/v1/contacts/contacts/?search=' + encodeURIComponent(query) + '&page_size=5').catch(function() { return { results: [] }; }) : Promise.resolve({ results: [] })
      ]).then(function(results) {
        var tickets = results[0].results || [];
        var contacts = results[1].results || [];
        var apiHtml = '';

        if (tickets.length > 0) {
          apiHtml += '<div class="cmd-palette-section">Tickets</div>';
          tickets.forEach(function(t) {
            apiHtml += renderItem('#' + t.number + ' ' + (t.subject || ''), 'ti ti-ticket', '/tickets/' + t.number + '/', (t.status_name || '') + ' \u00b7 ' + (t.priority || ''));
          });
        }

        if (contacts.length > 0) {
          apiHtml += '<div class="cmd-palette-section">Contacts</div>';
          contacts.forEach(function(c) {
            var name = ((c.first_name || '') + ' ' + (c.last_name || '')).trim() || c.email;
            apiHtml += renderItem(name, 'ti ti-address-book', '/contacts/' + c.id + '/', c.email || '');
          });
        }

        var apiSection = document.getElementById('cmdPaletteApiResults');
        if (apiSection) {
          if (apiHtml) {
            apiSection.outerHTML = apiHtml;
          } else {
            apiSection.remove();
          }
        }
        bindItemClicks(body);
        activeIndex = -1;
        updateActiveItem();
      });
    } else {
      if (!html) {
        html = '<div class="cmd-palette-empty">No results for "' + escapeHtml(query) + '"</div>';
      }
      body.innerHTML = html;
      bindItemClicks(body);
    }

    activeIndex = -1;
    updateActiveItem();
  }

  function getItems() {
    var body = document.getElementById('cmdPaletteBody');
    return body ? body.querySelectorAll('.cmd-palette-item') : [];
  }

  function updateActiveItem() {
    var items = getItems();
    items.forEach(function(item, i) {
      if (i === activeIndex) {
        item.classList.add('active');
        item.scrollIntoView({ block: 'nearest' });
      } else {
        item.classList.remove('active');
      }
    });
  }

  // Recent pages in localStorage
  function getRecentPages() {
    try {
      return JSON.parse(localStorage.getItem('kanzan_recent_pages') || '[]').slice(0, 5);
    } catch (e) { return []; }
  }

  function saveRecentPage(title, url) {
    var recent = getRecentPages();
    // Find matching page for icon
    var page = PAGES.find(function(p) { return p.url === url; });
    var icon = page ? page.icon : 'ti ti-clock';
    var desc = page ? page.desc : '';

    // Remove if already exists
    recent = recent.filter(function(r) { return r.url !== url; });
    recent.unshift({ title: title, url: url, icon: icon, desc: desc });
    recent = recent.slice(0, 5);

    try { localStorage.setItem('kanzan_recent_pages', JSON.stringify(recent)); } catch (e) {}
  }

  // Init
  document.addEventListener('DOMContentLoaded', function() {
    createPaletteHTML();

    var input = document.getElementById('cmdPaletteInput');

    input.addEventListener('input', function() {
      var q = this.value.trim();
      clearTimeout(searchTimeout);
      if (!q) {
        renderDefaultView();
        return;
      }
      searchTimeout = setTimeout(function() { runSearch(q); }, 200);
    });

    // Bind the navbar search trigger button to open the palette
    var searchTrigger = document.getElementById('globalSearchTrigger');
    if (searchTrigger) {
      searchTrigger.addEventListener('click', function(e) {
        e.preventDefault();
        openPalette();
      });
    }

    // "/" shortcut to open palette (when not in an input)
    // Keyboard navigation
    document.addEventListener('keydown', function(e) {
      // "/" to open palette when not focused on an input
      if (e.key === '/' && !isOpen && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) && !document.activeElement.isContentEditable) {
        e.preventDefault();
        openPalette();
        return;
      }

      // Cmd+K / Ctrl+K to toggle
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        if (isOpen) closePalette();
        else openPalette();
        return;
      }

      if (!isOpen) return;

      if (e.key === 'Escape') {
        e.preventDefault();
        closePalette();
        return;
      }

      var items = getItems();
      if (items.length === 0) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIndex = (activeIndex + 1) % items.length;
        updateActiveItem();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIndex = activeIndex <= 0 ? items.length - 1 : activeIndex - 1;
        updateActiveItem();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (activeIndex >= 0 && activeIndex < items.length) {
          var item = items[activeIndex];
          var url = item.getAttribute('href');
          var title = item.dataset.title;
          if (url && url !== '#') {
            saveRecentPage(title, url);
            closePalette();
            window.location.href = url;
          }
        }
      }
    });
  });
})();
