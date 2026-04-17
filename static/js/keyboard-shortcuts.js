/**
 * Global keyboard shortcuts for the Kanzan ticketing system.
 *
 * Shortcuts:
 *   Navigation:
 *     j / k          - Move selection down / up in ticket list
 *     Enter          - Open selected ticket
 *     Escape         - Deselect / close modals
 *
 *   Quick actions (ticket list):
 *     a              - Assign selected ticket
 *     s              - Change status of selected ticket
 *     x              - Toggle select (for bulk actions)
 *
 *   Global:
 *     Cmd/Ctrl + k   - Focus search / command palette
 *     c              - Create new ticket (when not in an input)
 *     ?              - Show keyboard shortcuts help
 *     g then d       - Go to dashboard
 *     g then t       - Go to tickets
 *     g then c       - Go to contacts
 *     g then b       - Go to kanban
 */

(function () {
    'use strict';

    let selectedIndex = -1;
    let pendingGo = false;
    let goTimeout = null;

    function isInputFocused() {
        const el = document.activeElement;
        if (!el) return false;
        const tag = el.tagName.toLowerCase();
        return (
            tag === 'input' ||
            tag === 'textarea' ||
            tag === 'select' ||
            el.isContentEditable ||
            el.closest('.tiptap') !== null
        );
    }

    function getTicketRows() {
        return document.querySelectorAll(
            '.ticket-row, tr[data-ticket-id], .ticket-card[data-ticket-id]'
        );
    }

    function selectRow(index) {
        const rows = getTicketRows();
        if (rows.length === 0) return;

        // Deselect previous
        rows.forEach(r => r.classList.remove('keyboard-selected'));

        // Clamp index
        if (index < 0) index = 0;
        if (index >= rows.length) index = rows.length - 1;
        selectedIndex = index;

        const row = rows[selectedIndex];
        row.classList.add('keyboard-selected');
        row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    function getSelectedTicketId() {
        const rows = getTicketRows();
        if (selectedIndex < 0 || selectedIndex >= rows.length) return null;
        return rows[selectedIndex].dataset.ticketId || null;
    }

    function getSelectedTicketNumber() {
        const rows = getTicketRows();
        if (selectedIndex < 0 || selectedIndex >= rows.length) return null;
        return rows[selectedIndex].dataset.ticketNumber || null;
    }

    // Help modal — uses existing Bootstrap modal from the page if present
    function showHelp() {
        let modal = document.getElementById('keyboard-shortcuts-modal');
        if (modal) {
            const bsModal = bootstrap.Modal.getOrCreateInstance(modal);
            bsModal.show();
            return;
        }

        // Build the modal using safe DOM APIs
        modal = document.createElement('div');
        modal.id = 'keyboard-shortcuts-modal';
        modal.className = 'modal fade';
        modal.tabIndex = -1;

        const dialog = document.createElement('div');
        dialog.className = 'modal-dialog modal-dialog-centered';

        const content = document.createElement('div');
        content.className = 'modal-content';

        const header = document.createElement('div');
        header.className = 'modal-header';
        const title = document.createElement('h5');
        title.className = 'modal-title';
        title.textContent = 'Keyboard Shortcuts';
        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'btn-close';
        closeBtn.setAttribute('data-bs-dismiss', 'modal');
        header.appendChild(title);
        header.appendChild(closeBtn);

        const body = document.createElement('div');
        body.className = 'modal-body';

        const shortcuts = [
            ['Navigation', [
                ['j / k', 'Next / Previous'],
                ['Enter', 'Open ticket'],
                ['Esc', 'Deselect'],
            ]],
            ['Actions', [
                ['c', 'New ticket'],
                ['a', 'Assign'],
                ['s', 'Change status'],
                ['x', 'Toggle select'],
            ]],
            ['Global', [
                ['Ctrl+k', 'Search'],
                ['?', 'This help'],
            ]],
            ['Go to...', [
                ['g d', 'Dashboard'],
                ['g t', 'Tickets'],
                ['g c', 'Contacts'],
                ['g b', 'Kanban'],
            ]],
        ];

        const row = document.createElement('div');
        row.className = 'row';

        shortcuts.forEach(function (section) {
            const col = document.createElement('div');
            col.className = 'col-6 mb-3';
            const heading = document.createElement('h6');
            heading.className = 'text-muted mb-2';
            heading.textContent = section[0];
            col.appendChild(heading);

            const dl = document.createElement('dl');
            dl.className = 'shortcuts-list';
            section[1].forEach(function (pair) {
                const dt = document.createElement('dt');
                const kbd = document.createElement('kbd');
                kbd.textContent = pair[0];
                dt.appendChild(kbd);
                const dd = document.createElement('dd');
                dd.textContent = pair[1];
                dl.appendChild(dt);
                dl.appendChild(dd);
            });
            col.appendChild(dl);
            row.appendChild(col);
        });

        body.appendChild(row);
        content.appendChild(header);
        content.appendChild(body);
        dialog.appendChild(content);
        modal.appendChild(dialog);
        document.body.appendChild(modal);

        const bsModal = new bootstrap.Modal(modal);
        bsModal.show();
    }

    document.addEventListener('keydown', function (e) {
        // Never intercept when typing in inputs
        if (isInputFocused() && e.key !== 'Escape') return;

        // Cmd/Ctrl+K - search/command palette
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            const searchInput = document.querySelector(
                '#search-input, #command-palette-input, .search-input, [data-search-input]'
            );
            if (searchInput) {
                searchInput.focus();
                searchInput.select();
            } else {
                // Try command palette
                const cmdPalette = document.getElementById('command-palette');
                if (cmdPalette) {
                    cmdPalette.classList.add('active');
                    const input = cmdPalette.querySelector('input');
                    if (input) input.focus();
                }
            }
            return;
        }

        // ? - help
        if (e.key === '?' && !e.metaKey && !e.ctrlKey) {
            showHelp();
            return;
        }

        // Escape - deselect or close modal
        if (e.key === 'Escape') {
            selectedIndex = -1;
            getTicketRows().forEach(r => r.classList.remove('keyboard-selected'));
            pendingGo = false;
            return;
        }

        // "g" prefix for go-to shortcuts
        if (e.key === 'g' && !e.metaKey && !e.ctrlKey && !pendingGo) {
            pendingGo = true;
            clearTimeout(goTimeout);
            goTimeout = setTimeout(function () { pendingGo = false; }, 1000);
            return;
        }

        if (pendingGo) {
            pendingGo = false;
            clearTimeout(goTimeout);
            var goMap = { d: '/dashboard/', t: '/tickets/', c: '/contacts/', b: '/kanban/' };
            if (goMap[e.key]) {
                window.location.href = goMap[e.key];
            }
            return;
        }

        // j / k - navigate list
        if (e.key === 'j') {
            selectRow(selectedIndex + 1);
            return;
        }
        if (e.key === 'k') {
            selectRow(selectedIndex - 1);
            return;
        }

        // Enter - open selected ticket
        if (e.key === 'Enter' && selectedIndex >= 0) {
            var number = getSelectedTicketNumber();
            if (number) {
                window.location.href = '/tickets/' + number + '/';
            }
            return;
        }

        // c - new ticket
        if (e.key === 'c') {
            window.location.href = '/tickets/new/';
            return;
        }

        // x - toggle checkbox
        if (e.key === 'x' && selectedIndex >= 0) {
            var rows = getTicketRows();
            var currentRow = rows[selectedIndex];
            var checkbox = currentRow.querySelector('input[type="checkbox"]');
            if (checkbox) {
                checkbox.checked = !checkbox.checked;
                checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            }
            return;
        }

        // a - assign (trigger assign action)
        if (e.key === 'a' && selectedIndex >= 0) {
            var ticketId = getSelectedTicketId();
            if (ticketId) {
                var assignBtn = document.querySelector(
                    '[data-ticket-id="' + ticketId + '"] .assign-btn, .assign-trigger'
                );
                if (assignBtn) assignBtn.click();
            }
            return;
        }

        // s - change status
        if (e.key === 's' && selectedIndex >= 0) {
            var statusTicketId = getSelectedTicketId();
            if (statusTicketId) {
                var statusBtn = document.querySelector(
                    '[data-ticket-id="' + statusTicketId + '"] .status-btn, .status-trigger'
                );
                if (statusBtn) statusBtn.click();
            }
            return;
        }
    });

    // Add CSS for keyboard selection
    var style = document.createElement('style');
    style.textContent = [
        '.keyboard-selected {',
        '    outline: 2px solid var(--accent-color, #2563EB) !important;',
        '    outline-offset: -2px;',
        '    background-color: rgba(37, 99, 235, 0.05) !important;',
        '}',
        '.shortcuts-list { margin: 0; }',
        '.shortcuts-list dt { float: left; clear: left; width: 100px; font-weight: normal; }',
        '.shortcuts-list dd { margin-left: 110px; margin-bottom: 4px; color: var(--text-secondary); }',
        'kbd {',
        '    background: var(--bg-secondary, #f1f3f5);',
        '    border: 1px solid var(--border-color, #dee2e6);',
        '    border-radius: 3px;',
        '    padding: 1px 5px;',
        '    font-size: 0.8em;',
        '    font-family: inherit;',
        '}',
    ].join('\n');
    document.head.appendChild(style);
})();
