/* Drawer, Modal, ConfirmDialog and Toast — one instance of each, reused. */
import { el, clear, icon } from '../lib/dom.js';
import { Button } from './primitives.js';

let drawerRefs = null;
let lastFocused = null;

function trapFocus(container, event) {
  if (event.key !== 'Tab') return;
  const focusable = container.querySelectorAll(
    'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])');
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

function ensureDrawer() {
  if (drawerRefs) return drawerRefs;
  const backdrop = el('div', { class: 'overlay-backdrop', onclick: () => closeDrawer() });
  const panel = el('aside', {
    class: 'drawer', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Details',
    onkeydown: (event) => {
      if (event.key === 'Escape') closeDrawer();
      trapFocus(panel, event);
    },
  });
  document.body.append(backdrop, panel);
  drawerRefs = { backdrop, panel };
  return drawerRefs;
}

export function openDrawer(content, { label = 'Details' } = {}) {
  const { backdrop, panel } = ensureDrawer();
  lastFocused = document.activeElement;
  panel.setAttribute('aria-label', label);
  clear(panel).append(content);
  backdrop.classList.add('open');
  panel.classList.add('open');
  window.requestAnimationFrame(() => {
    const target = panel.querySelector('[data-autofocus], button, a[href]');
    if (target) target.focus();
  });
}

export function closeDrawer() {
  if (!drawerRefs) return;
  drawerRefs.backdrop.classList.remove('open');
  drawerRefs.panel.classList.remove('open');
  if (lastFocused && lastFocused.focus) lastFocused.focus();
  lastFocused = null;
}

export function DrawerHeader(title, subtitle) {
  return el('div', { class: 'drawer-head' },
    el('div', { class: 'grow' },
      el('h2', { style: 'font-size:var(--text-lg)' }, title),
      subtitle ? el('div', { class: 'small muted mt1' }, subtitle) : null),
    el('button', {
      class: 'icon-btn', type: 'button', 'aria-label': 'Close', 'data-autofocus': '',
      onclick: closeDrawer,
    }, icon('close')));
}

export function DrawerBody(...children) {
  return el('div', { class: 'drawer-body' }, children);
}

/* ------------------------------------------------------------------- Modal */
let modalRefs = null;
function ensureModal() {
  if (modalRefs) return modalRefs;
  const backdrop = el('div', { class: 'overlay-backdrop', onclick: () => closeModal() });
  const panel = el('div', {
    class: 'modal', role: 'dialog', 'aria-modal': 'true',
    onkeydown: (event) => { if (event.key === 'Escape') closeModal(); trapFocus(panel, event); },
  });
  document.body.append(backdrop, panel);
  modalRefs = { backdrop, panel };
  return modalRefs;
}

export function openModal({ title, body, footer, label = '' }) {
  const { backdrop, panel } = ensureModal();
  lastFocused = document.activeElement;
  panel.setAttribute('aria-label', label || title || 'Dialog');
  const parts = [
    el('div', { class: 'modal-head' }, el('h2', { style: 'font-size:var(--text-lg)' }, title)),
    el('div', { class: 'modal-body' }, body),
  ];
  if (footer) parts.push(el('div', { class: 'modal-foot' }, footer));
  clear(panel);
  panel.append(...parts);
  backdrop.classList.add('open');
  panel.classList.add('open');
  window.requestAnimationFrame(() => {
    const target = panel.querySelector('[data-autofocus], button');
    if (target) target.focus();
  });
}

export function closeModal() {
  if (!modalRefs) return;
  modalRefs.backdrop.classList.remove('open');
  modalRefs.panel.classList.remove('open');
  if (lastFocused && lastFocused.focus) lastFocused.focus();
  lastFocused = null;
}

export function confirmDialog({
  title, body, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false,
}) {
  return new Promise((resolve) => {
    openModal({
      title,
      body: typeof body === 'string' ? el('p', { class: 'muted' }, body) : body,
      footer: [
        Button(cancelLabel, { onClick: () => { closeModal(); resolve(false); } }),
        Button(confirmLabel, {
          variant: danger ? 'danger' : 'primary',
          onClick: () => { closeModal(); resolve(true); },
        }),
      ],
    });
  });
}

/* ------------------------------------------------------------------- Toast */
let toastRegion = null;
export function toast(message, { title = '', variant = 'info', timeout = 4200 } = {}) {
  if (!toastRegion) {
    toastRegion = el('div', { class: 'toast-region', role: 'region', 'aria-live': 'polite',
      'aria-label': 'Notifications' });
    document.body.append(toastRegion);
  }
  const node = el('div', { class: `toast ${variant}` },
    el('div', { class: 'grow' },
      title ? el('div', { class: 'toast-title' }, title) : null,
      el('div', { class: title ? 'toast-body' : '' }, message)),
    el('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Dismiss',
      onclick: () => node.remove() }, icon('close', { size: 14 })));
  toastRegion.append(node);
  if (timeout) window.setTimeout(() => node.remove(), timeout);
  return node;
}
