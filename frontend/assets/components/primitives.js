/* ==========================================================================
   Primitive components. Every screen composes these; no page writes its own
   card, badge, tile or state markup.
   ========================================================================== */

import { el, icon, clear } from '../lib/dom.js';
import { markdownToHtml } from '../lib/markdown.js';
import { tone, statusLabel, titleise, confidenceTone } from '../lib/format.js';

/* ------------------------------------------------------------------ Button */
export function Button(label, {
  variant = 'default', size = '', iconName = '', onClick = null, href = '',
  disabled = false, type = 'button', title = '', ariaLabel = '', download = '',
} = {}) {
  const classes = ['btn', variant === 'default' ? '' : variant, size].filter(Boolean).join(' ');
  const children = [iconName ? icon(iconName, { size: 15 }) : null, label ? el('span', {}, label) : null];
  if (href) {
    return el('a', {
      class: classes, href, title: title || null, 'aria-label': ariaLabel || null,
      download: download || null, ...(disabled ? { 'aria-disabled': 'true' } : {}),
    }, children);
  }
  return el('button', {
    class: classes, type, disabled, title: title || null,
    'aria-label': ariaLabel || null, onclick: onClick,
  }, children);
}

export function IconButton(iconName, { onClick = null, title = '', href = '' } = {}) {
  const children = [icon(iconName), el('span', { class: 'visually-hidden' }, title)];
  return href
    ? el('a', { class: 'icon-btn', href, title, 'aria-label': title }, children)
    : el('button', { class: 'icon-btn', type: 'button', title, 'aria-label': title, onclick: onClick }, children);
}

/* -------------------------------------------------------------------- Card */
export function Card({ title = '', subtitle = '', hint = null, actions = null,
                       flush = false, inset = false, id = '' } = {}, ...children) {
  const head = (title || hint || actions)
    ? el('div', { class: 'card-head' },
        title ? el('h2', {}, title) : null,
        hint || actions ? el('div', { class: 'hint' }, hint, actions) : null)
    : null;
  return el('section', {
    class: `card${flush ? ' flush' : ''}${inset ? ' inset' : ''}`, id: id || null,
  }, head, subtitle ? el('p', { class: 'card-sub' }, subtitle) : null, children);
}

export function SectionTitle(text) {
  return el('div', { class: 'section-title' }, text);
}

/* ------------------------------------------------------------------ Badges */
export function Badge(label, { variant = '', dot = false, size = '' } = {}) {
  return el('span', { class: ['badge', variant, size].filter(Boolean).join(' ') },
    dot ? el('span', { class: 'dot' }) : null, label);
}

export function StatusBadge(status, { label = '', size = '' } = {}) {
  const variant = tone(status);
  const running = ['running', 'working', 'queued', 'in_progress'].includes(String(status));
  return el('span', {
    class: ['badge', variant, running ? 'running' : '', size].filter(Boolean).join(' '),
  }, el('span', { class: 'dot' }), label || statusLabel(status));
}

export function ConfidenceBadge(value, { label = 'confidence' } = {}) {
  const score = Number(value) || 0;
  return el('span', { class: `badge ${confidenceTone(score)}` },
    el('span', { class: 'dot' }), `${score}/100 ${label}`);
}

/* -------------------------------------------------------------- MetricCard */
export function MetricCard({
  label, value, unit = '', foot = '', meter = null, accent = false, badge = null, hint = '',
}) {
  return el('div', { class: `tile${accent ? ' accent' : ''}` },
    el('div', { class: 'tile-label' }, label, hint ? el('span', { title: hint }, icon('info', { size: 12 })) : null),
    el('div', { class: 'tile-value' },
      value === null || value === undefined || value === '' ? '—' : String(value),
      unit ? el('span', { class: 'unit' }, unit) : null,
      badge),
    meter ? el('div', { class: `meter ${meter.variant || ''}` },
      el('span', { style: `width:${Math.max(0, Math.min(100, meter.value))}%` })) : null,
    foot ? el('div', { class: 'tile-foot' }, foot) : null);
}

/* ------------------------------------------------------------------ States */
export function EmptyState(title, detail = '', { action = null, iconName = 'inbox' } = {}) {
  return el('div', { class: 'empty' },
    el('div', { class: 'empty-glyph' }, icon(iconName, { size: 20 })),
    el('h3', {}, title),
    detail ? el('p', {}, detail) : null,
    action ? el('div', { class: 'empty-action' }, action) : null);
}

export function LoadingState(label = 'Loading…') {
  return el('div', { class: 'loading', role: 'status', 'aria-live': 'polite' },
    el('span', { class: 'spinner' }), el('span', {}, label));
}

export function ErrorState(error, { onRetry = null, context = '' } = {}) {
  const kind = error && error.kind ? error.kind : 'server';
  const heading = {
    offline: 'Cannot reach the Intelligenzit platform',
    timeout: 'The platform did not respond in time',
    not_found: 'Not found',
    unauthorised: 'API key required',
    forbidden: 'Not permitted',
    conflict: 'Not available yet',
    unprocessable: 'Those details were rejected',
    invalid: 'That request was rejected',
    malformed: 'Unreadable response',
    rate_limited: 'Too many requests',
  }[kind] || 'Something went wrong';

  return el('div', { class: 'banner error', role: 'alert' },
    icon('alert', { size: 18, cls: 'banner-icon' }),
    el('div', { class: 'grow' },
      el('strong', {}, heading),
      el('div', {}, (error && error.message) || String(error)),
      context ? el('div', { class: 'small muted mt2' }, context) : null,
      kind === 'unauthorised'
        ? el('div', { class: 'small muted mt2' },
            'Enter the platform API key on the Settings page.') : null),
    onRetry ? el('div', { class: 'banner-action' },
      Button('Retry', { size: 'sm', iconName: 'refresh', onClick: onRetry })) : null);
}

export function Banner(variant, title, body, { action = null, iconName = '' } = {}) {
  const glyph = iconName || (variant === 'error' ? 'alert' : variant === 'warn' ? 'alert' : 'info');
  return el('div', { class: `banner ${variant}` },
    icon(glyph, { size: 18, cls: 'banner-icon' }),
    el('div', { class: 'grow' },
      title ? el('strong', {}, title) : null,
      body ? el('div', {}, body) : null),
    action ? el('div', { class: 'banner-action' }, action) : null);
}

/* ------------------------------------------------------------- Key / value */
export function KeyValue(pairs, { className = '' } = {}) {
  const list = el('dl', { class: `kv ${className}`.trim() });
  for (const [key, value] of pairs) {
    if (value === null || value === undefined) continue;
    list.append(el('dt', {}, key), el('dd', {}, value));
  }
  return list;
}

/* -------------------------------------------------------------- Progress */
export function ProgressBar(percentValue, { indeterminate = false, label = '' } = {}) {
  const clamped = Math.max(0, Math.min(100, Number(percentValue) || 0));
  return el('div', {
    class: `progress${indeterminate ? ' indeterminate' : ''}`,
    role: 'progressbar', 'aria-valuenow': indeterminate ? null : Math.round(clamped),
    'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-label': label || 'Progress',
  }, el('span', { style: `width:${clamped}%` }));
}

/* ---------------------------------------------------------------- Markdown */
export function Prose(source, { className = '' } = {}) {
  return el('div', { class: `md ${className}`.trim(), html: markdownToHtml(source) });
}

/* -------------------------------------------------------------------- Tabs */
export function Tabs(items, { active, onSelect, ariaLabel = 'Sections' }) {
  const bar = el('div', { class: 'tabs', role: 'tablist', 'aria-label': ariaLabel });
  const buttons = new Map();
  items.forEach((item) => {
    const button = el('button', {
      type: 'button', role: 'tab', id: `tab-${item.id}`,
      'aria-selected': String(item.id === active),
      'aria-controls': `panel-${item.id}`,
      tabindex: item.id === active ? '0' : '-1',
      onclick: () => onSelect(item.id),
      onkeydown: (event) => {
        const order = items.map((i) => i.id);
        const at = order.indexOf(item.id);
        if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
          event.preventDefault();
          const next = order[(at + (event.key === 'ArrowRight' ? 1 : order.length - 1)) % order.length];
          onSelect(next);
          const target = buttons.get(next);
          if (target) target.focus();
        }
      },
    }, item.label,
       item.count !== undefined && item.count !== null
         ? el('span', { class: 'count' }, String(item.count)) : null);
    buttons.set(item.id, button);
    bar.append(button);
  });
  return bar;
}

/* ------------------------------------------------------------- Disclosure */
export function Disclosure(summary, body, { open = false } = {}) {
  return el('details', { class: 'disclosure', ...(open ? { open: true } : {}) },
    el('summary', {}, summary),
    el('div', { class: 'disclosure-body' }, body));
}

/* --------------------------------------------------------------- Copy text */
export async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    try {
      const area = el('textarea', { style: 'position:fixed;opacity:0' });
      area.value = value;
      document.body.append(area);
      area.select();
      const ok = document.execCommand('copy');
      area.remove();
      return ok;
    } catch { return false; }
  }
}

