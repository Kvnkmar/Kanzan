/**
 * Kanzen Custom Select — upgrades native <select> into styled dropdowns.
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

    // Build wrapper
    var wrap = document.createElement('div');
    wrap.className = 'td-cselect';
    if (selectEl.classList.contains('form-select-sm')) wrap.classList.add('td-cselect--sm');

    // Trigger button
    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'td-cselect-trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.innerHTML = '<span class="td-cselect-text"></span>' +
      '<i class="ti ti-chevron-down td-cselect-arrow"></i>';

    // Menu
    var menu = document.createElement('div');
    menu.className = 'td-cselect-menu';
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

    // Assemble
    if (searchWrap) menu.appendChild(searchWrap);
    menu.appendChild(optionsWrap);
    wrap.appendChild(trigger);
    wrap.appendChild(menu);
    selectEl.parentNode.insertBefore(wrap, selectEl.nextSibling);

    // Copy width constraints from original select if inline
    if (selectEl.style.maxWidth) wrap.style.maxWidth = selectEl.style.maxWidth;
    if (selectEl.style.minWidth) wrap.style.minWidth = selectEl.style.minWidth;
    if (selectEl.style.width) wrap.style.width = selectEl.style.width;

    // Track focused option index for keyboard nav
    var focusedIdx = -1;
    var visibleItems = [];

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
      optionsWrap.innerHTML = '';
      visibleItems = [];
      var filterLower = (filter || '').toLowerCase().trim();
      var hasMatch = false;

      for (var i = 0; i < selectEl.options.length; i++) {
        var opt = selectEl.options[i];
        var text = opt.textContent;

        // Skip options with empty value that serve as placeholders
        // unless there's no filter and it's the first option
        if (filterLower && text.toLowerCase().indexOf(filterLower) === -1) continue;

        hasMatch = true;
        var item = document.createElement('div');
        item.className = 'td-cselect-option' + (opt.selected ? ' selected' : '');
        item.setAttribute('role', 'option');
        item.setAttribute('data-value', opt.value);
        item.setAttribute('data-index', i);
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

    function openMenu() {
      if (selectEl.disabled) return;
      buildOptions('');
      wrap.classList.add('open');
      trigger.setAttribute('aria-expanded', 'true');

      if (searchInput) {
        searchInput.value = '';
        requestAnimationFrame(function() { searchInput.focus(); });
      }

      // Position menu above if not enough space below
      requestAnimationFrame(function() {
        var rect = wrap.getBoundingClientRect();
        var menuH = menu.offsetHeight;
        if (rect.bottom + menuH > window.innerHeight && rect.top > menuH) {
          menu.style.bottom = '100%';
          menu.style.top = 'auto';
          menu.style.marginBottom = '4px';
          menu.style.marginTop = '0';
        } else {
          menu.style.top = 'calc(100% + 4px)';
          menu.style.bottom = 'auto';
          menu.style.marginTop = '0';
          menu.style.marginBottom = '0';
        }
      });

      document.addEventListener('click', closeOnOutside, true);
      document.addEventListener('keydown', handleKeydown, true);
    }

    function closeMenu() {
      wrap.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
      menu.style.top = '';
      menu.style.bottom = '';
      menu.style.marginTop = '';
      menu.style.marginBottom = '';
      focusedIdx = -1;
      document.removeEventListener('click', closeOnOutside, true);
      document.removeEventListener('keydown', handleKeydown, true);
    }

    function closeOnOutside(e) {
      if (!wrap.contains(e.target)) closeMenu();
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
      if (!wrap.classList.contains('open')) return;

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
      if (wrap.classList.contains('open')) closeMenu();
      else openMenu();
    });

    trigger.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
        e.preventDefault();
        if (!wrap.classList.contains('open')) openMenu();
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
