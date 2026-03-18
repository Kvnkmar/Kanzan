/**
 * Kanzen Custom Select — upgrades native <select> into styled dropdowns.
 * Uses portal rendering (menu appended to document.body) to avoid
 * overflow/clipping issues inside modals, cards, and other containers.
 *
 * Usage:
 *   KanzenSelect.upgrade(selectElement, { searchable: true });
 *   KanzenSelect.upgradeAll('.form-select');
 *
 * Options:
 *   searchable  — boolean, adds search input for long lists (auto if > 8 options)
 *   placeholder — string, placeholder text for search input
 */
window.KanzenSelect = (function() {
  'use strict';

  var SEARCH_THRESHOLD = 8;

  function upgrade(selectEl, opts) {
    if (!selectEl || selectEl._ksUpgraded) return;
    selectEl._ksUpgraded = true;
    opts = opts || {};

    var optionCount = selectEl.options.length;
    var searchable = opts.searchable != null ? opts.searchable : (optionCount > SEARCH_THRESHOLD);
    var searchPlaceholder = opts.placeholder || 'Search...';

    selectEl.style.display = 'none';
    selectEl.setAttribute('tabindex', '-1');
    selectEl.setAttribute('aria-hidden', 'true');

    // Build wrapper (stays inline where the select was)
    var wrap = document.createElement('div');
    wrap.className = 'td-cselect';
    if (selectEl.classList.contains('form-select-sm')) wrap.classList.add('td-cselect--sm');

    // Trigger button — built with safe DOM methods
    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'td-cselect-trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');

    var triggerText = document.createElement('span');
    triggerText.className = 'td-cselect-text';
    trigger.appendChild(triggerText);

    var triggerArrow = document.createElement('i');
    triggerArrow.className = 'ti ti-chevron-down td-cselect-arrow';
    trigger.appendChild(triggerArrow);

    // Menu — will be portalled to document.body
    var menu = document.createElement('div');
    menu.className = 'td-cselect-menu td-cselect-portal';
    menu.setAttribute('role', 'listbox');

    // Search input (if searchable)
    var searchWrap = null, searchInput = null;
    if (searchable) {
      searchWrap = document.createElement('div');
      searchWrap.className = 'td-cselect-search';
      searchInput = document.createElement('input');
      searchInput.type = 'text';
      searchInput.className = 'td-cselect-search-input';
      searchInput.placeholder = searchPlaceholder;
      searchInput.setAttribute('autocomplete', 'off');
      searchInput.setAttribute('spellcheck', 'false');
      searchWrap.appendChild(searchInput);
    }

    // Options container (for scrolling)
    var optionsWrap = document.createElement('div');
    optionsWrap.className = 'td-cselect-options';

    // Assemble trigger into wrap (inline)
    wrap.appendChild(trigger);
    selectEl.parentNode.insertBefore(wrap, selectEl.nextSibling);

    // Assemble menu (portal — appended to body on open, hidden by default)
    if (searchWrap) menu.appendChild(searchWrap);
    menu.appendChild(optionsWrap);

    // Copy width constraints from original select if inline
    if (selectEl.style.maxWidth) wrap.style.maxWidth = selectEl.style.maxWidth;
    if (selectEl.style.minWidth) wrap.style.minWidth = selectEl.style.minWidth;
    if (selectEl.style.width) wrap.style.width = selectEl.style.width;

    // Track focused option index for keyboard nav
    var focusedIdx = -1;
    var visibleItems = [];
    var isOpen = false;

    function syncText() {
      var sel = selectEl.options[selectEl.selectedIndex];
      var textEl = trigger.querySelector('.td-cselect-text');
      if (sel && sel.value) {
        textEl.textContent = sel.textContent;
        textEl.classList.remove('td-cselect-placeholder');
      } else if (sel) {
        textEl.textContent = sel.textContent;
        textEl.classList.add('td-cselect-placeholder');
      } else {
        textEl.textContent = '--';
        textEl.classList.add('td-cselect-placeholder');
      }
    }

    function buildOptions(filter) {
      optionsWrap.textContent = '';
      visibleItems = [];
      var filterLower = (filter || '').toLowerCase().trim();
      var hasMatch = false;

      for (var i = 0; i < selectEl.options.length; i++) {
        var opt = selectEl.options[i];
        var text = opt.textContent;

        if (filterLower && text.toLowerCase().indexOf(filterLower) === -1) continue;

        hasMatch = true;
        var item = document.createElement('div');
        item.className = 'td-cselect-option' + (opt.selected ? ' selected' : '');
        item.setAttribute('role', 'option');
        item.setAttribute('data-value', opt.value);
        item.setAttribute('data-index', String(i));
        item.textContent = text;
        optionsWrap.appendChild(item);
        visibleItems.push(item);
      }

      if (!hasMatch) {
        var empty = document.createElement('div');
        empty.className = 'td-cselect-empty';
        empty.textContent = 'No results found';
        optionsWrap.appendChild(empty);
      }

      focusedIdx = -1;
    }

    function setFocus(idx) {
      visibleItems.forEach(function(el) { el.classList.remove('td-cselect-focused'); });
      if (idx >= 0 && idx < visibleItems.length) {
        focusedIdx = idx;
        visibleItems[idx].classList.add('td-cselect-focused');
        visibleItems[idx].scrollIntoView({ block: 'nearest' });
      }
    }

    /** Position the portal menu relative to the trigger using fixed coords */
    function positionMenu() {
      var triggerRect = trigger.getBoundingClientRect();
      var menuH = menu.offsetHeight;
      var viewH = window.innerHeight;
      var viewW = window.innerWidth;

      // Width: match trigger width, minimum 200px
      var width = Math.max(triggerRect.width, 200);
      menu.style.width = width + 'px';

      // Horizontal: align left edge with trigger, clamp to viewport
      var left = triggerRect.left;
      if (left + width > viewW - 8) left = viewW - width - 8;
      if (left < 8) left = 8;
      menu.style.left = left + 'px';

      // Vertical: prefer below, flip above if no room
      var spaceBelow = viewH - triggerRect.bottom - 8;
      var spaceAbove = triggerRect.top - 8;

      if (spaceBelow >= menuH || spaceBelow >= spaceAbove) {
        // Open below
        menu.style.top = (triggerRect.bottom + 4) + 'px';
        menu.style.bottom = 'auto';
        menu.style.maxHeight = Math.min(260, spaceBelow) + 'px';
      } else {
        // Open above
        menu.style.bottom = (viewH - triggerRect.top + 4) + 'px';
        menu.style.top = 'auto';
        menu.style.maxHeight = Math.min(260, spaceAbove) + 'px';
      }
    }

    function openMenu() {
      if (selectEl.disabled) return;
      buildOptions('');
      isOpen = true;
      wrap.classList.add('open');
      trigger.setAttribute('aria-expanded', 'true');

      // Portal: append menu to body
      document.body.appendChild(menu);
      menu.style.display = 'block';

      if (searchInput) {
        searchInput.value = '';
      }

      // Position after render
      requestAnimationFrame(function() {
        positionMenu();
        if (searchInput) searchInput.focus();
      });

      document.addEventListener('click', closeOnOutside, true);
      document.addEventListener('keydown', handleKeydown, true);
      window.addEventListener('resize', positionMenu, { passive: true });
      window.addEventListener('scroll', positionMenu, true);
    }

    function closeMenu() {
      isOpen = false;
      wrap.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
      focusedIdx = -1;

      // Remove portal menu from body
      menu.style.display = 'none';
      if (menu.parentNode === document.body) {
        document.body.removeChild(menu);
      }

      document.removeEventListener('click', closeOnOutside, true);
      document.removeEventListener('keydown', handleKeydown, true);
      window.removeEventListener('resize', positionMenu);
      window.removeEventListener('scroll', positionMenu, true);
    }

    function closeOnOutside(e) {
      if (!wrap.contains(e.target) && !menu.contains(e.target)) closeMenu();
    }

    function selectItem(item) {
      if (!item) return;
      selectEl.value = item.getAttribute('data-value');
      selectEl.dispatchEvent(new Event('change', { bubbles: true }));
      syncText();
      closeMenu();
      trigger.focus();
    }

    function handleKeydown(e) {
      if (!isOpen) return;

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setFocus(Math.min(focusedIdx + 1, visibleItems.length - 1));
          break;
        case 'ArrowUp':
          e.preventDefault();
          setFocus(Math.max(focusedIdx - 1, 0));
          break;
        case 'Enter':
          e.preventDefault();
          if (focusedIdx >= 0 && visibleItems[focusedIdx]) {
            selectItem(visibleItems[focusedIdx]);
          }
          break;
        case 'Escape':
          e.preventDefault();
          closeMenu();
          trigger.focus();
          break;
        case 'Tab':
          closeMenu();
          break;
      }
    }

    // Events
    trigger.addEventListener('click', function(e) {
      e.stopPropagation();
      if (isOpen) closeMenu();
      else openMenu();
    });

    trigger.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
        e.preventDefault();
        if (!isOpen) openMenu();
      }
    });

    optionsWrap.addEventListener('click', function(e) {
      var item = e.target.closest('.td-cselect-option');
      if (item) selectItem(item);
    });

    // Hover focus
    optionsWrap.addEventListener('mousemove', function(e) {
      var item = e.target.closest('.td-cselect-option');
      if (item) {
        var idx = visibleItems.indexOf(item);
        if (idx !== -1 && idx !== focusedIdx) setFocus(idx);
      }
    });

    if (searchInput) {
      searchInput.addEventListener('input', function() {
        buildOptions(this.value);
        // Re-position in case height changed
        requestAnimationFrame(positionMenu);
      });
      searchInput.addEventListener('click', function(e) {
        e.stopPropagation();
      });
    }

    // Observe changes to the native select (e.g., dynamically added options)
    var observer = new MutationObserver(function() {
      syncText();
    });
    observer.observe(selectEl, { childList: true, subtree: true, attributes: true });

    // Intercept programmatic .value sets
    var origDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    if (origDesc && origDesc.set) {
      var origSet = origDesc.set;
      Object.defineProperty(selectEl, 'value', {
        get: function() { return origDesc.get.call(this); },
        set: function(v) { origSet.call(this, v); syncText(); },
        configurable: true
      });
    }

    // Handle disabled state
    if (selectEl.disabled) {
      trigger.disabled = true;
      wrap.classList.add('td-cselect--disabled');
    }

    // Watch for disabled attribute changes
    var disabledObserver = new MutationObserver(function() {
      trigger.disabled = selectEl.disabled;
      wrap.classList.toggle('td-cselect--disabled', selectEl.disabled);
    });
    disabledObserver.observe(selectEl, { attributes: true, attributeFilter: ['disabled'] });

    syncText();

    // Return API for external control
    return {
      refresh: function() { syncText(); },
      open: openMenu,
      close: closeMenu,
      destroy: function() {
        if (isOpen) closeMenu();
        observer.disconnect();
        disabledObserver.disconnect();
        wrap.remove();
        selectEl.style.display = '';
        selectEl.removeAttribute('tabindex');
        selectEl.removeAttribute('aria-hidden');
        selectEl._ksUpgraded = false;
      }
    };
  }

  function upgradeAll(selector, opts) {
    var selects = document.querySelectorAll(selector || 'select.form-select');
    var instances = [];
    selects.forEach(function(el) {
      instances.push(upgrade(el, opts));
    });
    return instances;
  }

  return { upgrade: upgrade, upgradeAll: upgradeAll };
})();
