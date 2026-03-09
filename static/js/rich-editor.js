/**
 * Rich Text Editor wrapper for Kanzen Suite.
 * Uses TipTap editor via CDN for rich text editing in ticket
 * descriptions and comments.
 *
 * Usage:
 *   var editor = createRichEditor('#myEditorElement', {
 *     placeholder: 'Write something...',
 *     content: '<p>Initial HTML content</p>',
 *     onChange: function(html) { console.log(html); }
 *   });
 *
 *   editor.getHTML()     // get current HTML content
 *   editor.setContent()  // set content
 *   editor.destroy()     // cleanup
 */

/* global tiptap */

function createRichEditor(selector, options) {
  options = options || {};

  var container = typeof selector === 'string' ? document.querySelector(selector) : selector;
  if (!container) {
    console.warn('Rich editor: container not found:', selector);
    return null;
  }

  // Build DOM structure
  container.classList.add('rich-editor-container');

  var toolbar = document.createElement('div');
  toolbar.className = 'rich-editor-toolbar';

  var editorArea = document.createElement('div');
  editorArea.className = 'rich-editor-content';

  container.innerHTML = '';
  container.appendChild(toolbar);
  container.appendChild(editorArea);

  // Check if TipTap is loaded
  if (typeof tiptap === 'undefined' || !tiptap.Editor) {
    editorArea.innerHTML = '<p style="color:var(--crm-text-muted);padding:0.75rem;">Rich text editor loading failed. Using plain text.</p>';
    // Fallback to textarea
    var fallback = document.createElement('textarea');
    fallback.className = 'form-control';
    fallback.rows = 6;
    fallback.placeholder = options.placeholder || '';
    fallback.value = options.content || '';
    container.innerHTML = '';
    container.appendChild(fallback);
    return {
      getHTML: function() { return fallback.value; },
      getText: function() { return fallback.value; },
      setContent: function(html) { fallback.value = html; },
      destroy: function() {},
      isFallback: true,
      element: fallback
    };
  }

  // Toolbar buttons configuration
  var toolbarButtons = [
    { cmd: 'bold',          icon: 'ti ti-bold',                  title: 'Bold (Ctrl+B)' },
    { cmd: 'italic',        icon: 'ti ti-italic',                title: 'Italic (Ctrl+I)' },
    { cmd: 'strike',        icon: 'ti ti-strikethrough',         title: 'Strikethrough' },
    { cmd: 'sep' },
    { cmd: 'heading2',      icon: 'ti ti-h-2',                   title: 'Heading 2' },
    { cmd: 'heading3',      icon: 'ti ti-h-3',                   title: 'Heading 3' },
    { cmd: 'sep' },
    { cmd: 'bulletList',    icon: 'ti ti-list',                   title: 'Bullet List' },
    { cmd: 'orderedList',   icon: 'ti ti-list-numbers',           title: 'Numbered List' },
    { cmd: 'sep' },
    { cmd: 'blockquote',    icon: 'ti ti-blockquote',             title: 'Blockquote' },
    { cmd: 'codeBlock',     icon: 'ti ti-code',                   title: 'Code Block' },
    { cmd: 'horizontalRule', icon: 'ti ti-separator-horizontal',  title: 'Horizontal Rule' },
    { cmd: 'sep' },
    { cmd: 'link',          icon: 'ti ti-link',                   title: 'Insert Link' },
  ];

  // Render toolbar
  toolbarButtons.forEach(function(btn) {
    if (btn.cmd === 'sep') {
      var sep = document.createElement('div');
      sep.className = 'rich-editor-sep';
      toolbar.appendChild(sep);
      return;
    }

    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'rich-editor-btn';
    button.title = btn.title;
    button.dataset.cmd = btn.cmd;
    button.innerHTML = '<i class="' + btn.icon + '"></i>';
    toolbar.appendChild(button);
  });

  // Initialize TipTap editor
  var extensions = [tiptap.StarterKit];

  if (tiptap.Link) {
    extensions.push(tiptap.Link.configure({
      openOnClick: false,
      HTMLAttributes: { target: '_blank', rel: 'noopener noreferrer' }
    }));
  }

  if (tiptap.Placeholder) {
    extensions.push(tiptap.Placeholder.configure({
      placeholder: options.placeholder || 'Write something...'
    }));
  }

  var editor = new tiptap.Editor({
    element: editorArea,
    extensions: extensions,
    content: options.content || '',
    onUpdate: function(props) {
      var html = props.editor.getHTML();
      updateToolbarState();
      if (typeof options.onChange === 'function') {
        options.onChange(html);
      }
    },
    onSelectionUpdate: function() {
      updateToolbarState();
    }
  });

  // Toolbar button handlers
  toolbar.addEventListener('click', function(e) {
    var btn = e.target.closest('.rich-editor-btn');
    if (!btn) return;

    e.preventDefault();
    var cmd = btn.dataset.cmd;

    switch (cmd) {
      case 'bold':          editor.chain().focus().toggleBold().run(); break;
      case 'italic':        editor.chain().focus().toggleItalic().run(); break;
      case 'strike':        editor.chain().focus().toggleStrike().run(); break;
      case 'heading2':      editor.chain().focus().toggleHeading({ level: 2 }).run(); break;
      case 'heading3':      editor.chain().focus().toggleHeading({ level: 3 }).run(); break;
      case 'bulletList':    editor.chain().focus().toggleBulletList().run(); break;
      case 'orderedList':   editor.chain().focus().toggleOrderedList().run(); break;
      case 'blockquote':    editor.chain().focus().toggleBlockquote().run(); break;
      case 'codeBlock':     editor.chain().focus().toggleCodeBlock().run(); break;
      case 'horizontalRule': editor.chain().focus().setHorizontalRule().run(); break;
      case 'link':
        var url = prompt('Enter URL:');
        if (url) {
          editor.chain().focus().setLink({ href: url }).run();
        } else {
          editor.chain().focus().unsetLink().run();
        }
        break;
    }
  });

  function updateToolbarState() {
    toolbar.querySelectorAll('.rich-editor-btn').forEach(function(btn) {
      var cmd = btn.dataset.cmd;
      var isActive = false;
      switch (cmd) {
        case 'bold':       isActive = editor.isActive('bold'); break;
        case 'italic':     isActive = editor.isActive('italic'); break;
        case 'strike':     isActive = editor.isActive('strike'); break;
        case 'heading2':   isActive = editor.isActive('heading', { level: 2 }); break;
        case 'heading3':   isActive = editor.isActive('heading', { level: 3 }); break;
        case 'bulletList': isActive = editor.isActive('bulletList'); break;
        case 'orderedList': isActive = editor.isActive('orderedList'); break;
        case 'blockquote': isActive = editor.isActive('blockquote'); break;
        case 'codeBlock':  isActive = editor.isActive('codeBlock'); break;
        case 'link':       isActive = editor.isActive('link'); break;
      }
      btn.classList.toggle('active', isActive);
    });
  }

  return {
    getHTML: function() { return editor.getHTML(); },
    getText: function() { return editor.getText(); },
    setContent: function(html) { editor.commands.setContent(html); },
    destroy: function() { editor.destroy(); },
    isFallback: false,
    editor: editor,
    element: editorArea
  };
}
