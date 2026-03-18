/**
 * Quick Notes Panel — slide-out right panel for personal sticky notes.
 */
document.addEventListener('DOMContentLoaded', function () {
  var panel = document.getElementById('notesPanel');
  var backdrop = document.getElementById('notesPanelBackdrop');
  var toggleBtn = document.getElementById('notesToggleBtn');
  var closeBtn = document.getElementById('notesPanelClose');
  var addBtn = document.getElementById('notesAddBtn');
  var emptyAddBtn = document.getElementById('notesEmptyAdd');
  var body = document.getElementById('notesPanelBody');
  var emptyState = document.getElementById('notesEmpty');

  if (!panel || !toggleBtn) return;

  var notes = [];
  var COLORS = ['yellow', 'blue', 'green', 'pink', 'purple', 'orange'];

  // ── Panel open / close ──────────────────────────────────────────

  function openPanel() {
    panel.classList.add('open');
    backdrop.classList.add('show');
    document.body.style.overflow = 'hidden';
    if (!notes.length) loadNotes();
  }

  function closePanel() {
    panel.classList.remove('open');
    backdrop.classList.remove('show');
    document.body.style.overflow = '';
  }

  toggleBtn.addEventListener('click', function () {
    if (panel.classList.contains('open')) closePanel();
    else openPanel();
  });
  closeBtn.addEventListener('click', closePanel);
  backdrop.addEventListener('click', closePanel);

  // Close on Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && panel.classList.contains('open')) closePanel();
  });

  // ── API helpers ─────────────────────────────────────────────────

  var API_BASE = '/api/v1/notes/notes/';

  function loadNotes() {
    Api.get(API_BASE + '?page_size=100').then(function (data) {
      notes = (data && data.results) ? data.results : [];
      render();
    }).catch(function () {
      Toast.error('Failed to load notes');
    });
  }

  function createNote() {
    Api.post(API_BASE, { content: '', color: 'yellow' }).then(function (note) {
      notes.unshift(note);
      render();
      // Focus the new note's textarea
      var first = body.querySelector('.note-card-textarea');
      if (first) first.focus();
    }).catch(function () {
      Toast.error('Failed to create note');
    });
  }

  function updateNote(id, data) {
    Api.patch(API_BASE + id + '/', data).then(function (updated) {
      var idx = notes.findIndex(function (n) { return n.id === id; });
      if (idx !== -1) notes[idx] = updated;
    }).catch(function () {
      Toast.error('Failed to save note');
    });
  }

  function deleteNote(id) {
    Api.delete(API_BASE + id + '/').then(function () {
      notes = notes.filter(function (n) { return n.id !== id; });
      render();
      Toast.success('Note deleted');
    }).catch(function () {
      Toast.error('Failed to delete note');
    });
  }

  // ── Render ──────────────────────────────────────────────────────

  function render() {
    // Sort: pinned first, then by updated_at desc
    notes.sort(function (a, b) {
      if (a.is_pinned !== b.is_pinned) return b.is_pinned ? 1 : -1;
      return new Date(b.updated_at) - new Date(a.updated_at);
    });

    if (!notes.length) {
      emptyState.style.display = '';
      // Remove all note cards
      body.querySelectorAll('.note-card').forEach(function (el) { el.remove(); });
      return;
    }

    emptyState.style.display = 'none';

    // Build HTML
    var html = '';
    notes.forEach(function (n) {
      var pinClass = n.is_pinned ? ' note-pinned' : '';
      var pinActiveClass = n.is_pinned ? ' note-pin-active' : '';
      var colorClass = ' note-color-' + (n.color || 'yellow');

      var colorDots = '';
      COLORS.forEach(function (c) {
        var active = c === n.color ? ' active' : '';
        colorDots += '<span class="note-color-dot' + active + '" data-color="' + c + '" data-note-id="' + n.id + '"></span>';
      });

      html += '<div class="note-card' + pinClass + colorClass + '" data-note-id="' + n.id + '">' +
        '<textarea class="note-card-textarea" placeholder="Write something..." data-note-id="' + n.id + '">' +
          escapeHtml(n.content) +
        '</textarea>' +
        '<div class="note-card-footer">' +
          '<span class="note-card-time">' + formatNoteTime(n.updated_at) + '</span>' +
          '<div class="note-card-actions">' +
            '<div class="note-color-picker">' + colorDots + '</div>' +
            '<button class="note-action-btn' + pinActiveClass + '" data-action="pin" data-note-id="' + n.id + '" title="' + (n.is_pinned ? 'Unpin' : 'Pin') + '">' +
              '<i class="ti ti-pin"></i>' +
            '</button>' +
            '<button class="note-action-btn note-delete-btn" data-action="delete" data-note-id="' + n.id + '" title="Delete">' +
              '<i class="ti ti-trash"></i>' +
            '</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    });

    // Preserve scroll position
    var scrollTop = body.scrollTop;
    body.querySelectorAll('.note-card').forEach(function (el) { el.remove(); });
    body.insertAdjacentHTML('beforeend', html);
    body.scrollTop = scrollTop;

    bindEvents();
  }

  // ── Event binding ───────────────────────────────────────────────

  var saveTimers = {};

  function bindEvents() {
    // Textarea auto-save with debounce
    body.querySelectorAll('.note-card-textarea').forEach(function (ta) {
      autoResize(ta);
      ta.addEventListener('input', function () {
        autoResize(this);
        var id = this.dataset.noteId;
        clearTimeout(saveTimers[id]);
        saveTimers[id] = setTimeout(function () {
          updateNote(id, { content: ta.value });
        }, 600);
      });
    });

    // Color dots
    body.querySelectorAll('.note-color-dot').forEach(function (dot) {
      dot.addEventListener('click', function () {
        var id = this.dataset.noteId;
        var color = this.dataset.color;
        updateNote(id, { color: color });
        // Update UI immediately
        var card = body.querySelector('.note-card[data-note-id="' + id + '"]');
        if (card) {
          COLORS.forEach(function (c) { card.classList.remove('note-color-' + c); });
          card.classList.add('note-color-' + color);
          card.querySelectorAll('.note-color-dot').forEach(function (d) {
            d.classList.toggle('active', d.dataset.color === color);
          });
        }
        var idx = notes.findIndex(function (n) { return n.id === id; });
        if (idx !== -1) notes[idx].color = color;
      });
    });

    // Pin / Delete buttons
    body.querySelectorAll('.note-action-btn[data-action]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = this.dataset.noteId;
        var action = this.dataset.action;
        if (action === 'pin') {
          var idx = notes.findIndex(function (n) { return n.id === id; });
          if (idx !== -1) {
            var newPinned = !notes[idx].is_pinned;
            notes[idx].is_pinned = newPinned;
            updateNote(id, { is_pinned: newPinned });
            render();
          }
        } else if (action === 'delete') {
          deleteNote(id);
        }
      });
    });
  }

  // ── Add buttons ─────────────────────────────────────────────────

  addBtn.addEventListener('click', createNote);
  emptyAddBtn.addEventListener('click', createNote);

  // ── Utilities ───────────────────────────────────────────────────

  function autoResize(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  }

  function escapeHtml(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function formatNoteTime(dateStr) {
    if (!dateStr) return '';
    var d = new Date(dateStr);
    var now = new Date();
    var diff = Math.floor((now - d) / 1000);

    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }
});
