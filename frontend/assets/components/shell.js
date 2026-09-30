/* ==========================================================================
   Application shell: sidebar, top bar, theme, live platform status, and the
   page bootstrapper every screen is built on.
   ========================================================================== */

import { el, icon, clear, $ } from '../lib/dom.js';
import { titleise } from '../lib/format.js';
import { api, config } from '../api/index.js';
import { Button, IconButton, ErrorState, LoadingState, StatusBadge, Badge } from './primitives.js';
import { openModal, closeModal, toast } from './overlay.js';

export const NAV = [
  { id: 'dashboard', href: 'dashboard.html', label: 'Dashboard', icon: 'dashboard' },
  { id: 'new', href: 'new-workflow.html', label: 'New Workflow', icon: 'plus' },
  { id: 'workflows', href: 'workflows.html', label: 'Workflows', icon: 'workflows' },
  { id: 'agents', href: 'agents.html', label: 'Agents', icon: 'agents' },
  { id: 'research', href: 'research.html', label: 'Research', icon: 'research' },
  { id: 'memory', href: 'memory.html', label: 'Memory', icon: 'memory' },
  { id: 'metrics', href: 'metrics.html', label: 'Metrics', icon: 'metrics' },
  { id: 'governance', href: 'governance.html', label: 'Governance', icon: 'governance' },
  { id: 'settings', href: 'settings.html', label: 'Settings', icon: 'settings' },
];

const THEME_KEY = 'intelligenzit.theme';

export function applyStoredTheme() {
  let stored = '';
  try { stored = window.localStorage.getItem(THEME_KEY) || ''; } catch { /* ignore */ }
  if (stored) document.documentElement.setAttribute('data-theme', stored);
  return stored;
}

export function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const isDark = current
    ? current === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  const next = isDark ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try { window.localStorage.setItem(THEME_KEY, next); } catch { /* ignore */ }
  return next;
}

/* Health is fetched once per page load and shared by every consumer. */
let healthPromise = null;
export function platformHealth({ refresh = false } = {}) {
  if (!healthPromise || refresh) healthPromise = api.platform.health();
  return healthPromise;
}

/* Who is signed in — fetched once per page load. */
let mePromise = null;
export function currentUser() {
  if (!mePromise) {
    mePromise = api.auth.me().catch((error) => { mePromise = null; throw error; });
  }
  return mePromise;
}

async function signOut() {
  try { await api.auth.logout(); } catch { /* the session may already be gone */ }
  config.setKey('');
  window.location.assign('login.html');
}

async function renderIdentity(active) {
  const host = document.getElementById('identity');
  if (!host) return;
  try {
    const { user, auth_enabled: enforced } = await currentUser();
    if (user.role === 'admin') {
      const nav = document.querySelector('.sidebar .nav');
      if (nav) {
        nav.append(el('a', {
          class: 'nav-link', href: 'users.html',
          ...(active === 'users' ? { 'aria-current': 'page' } : {}),
        }, icon('agents', { size: 17 }), el('span', {}, 'Users')));
      }
    }
    clear(host).append(
      el('div', { class: 'status-line ok' },
        el('span', { class: 'dot' }),
        el('span', { class: 'label' }, user.display_name || user.username),
        el('span', { class: 'value' }, titleise(user.role))),
      enforced
        ? el('button', { class: 'xs muted', type: 'button', onclick: signOut,
            style: 'background:none;border:0;padding:4px 0;cursor:pointer;color:inherit;text-decoration:underline' },
            'Sign out')
        : el('div', { class: 'xs muted' }, 'Authentication is disabled'));
  } catch { /* the API client redirects to sign-in on 401; anything else shows in the page */ }
}

function Brand() {
  return el('a', { class: 'brand-mark', href: 'index.html', 'aria-label': 'Intelligenzit home' },
    el('div', { class: 'brand-glyph', 'aria-hidden': 'true' }, 'IZ'),
    el('div', {},
      el('div', { class: 'brand-name' }, 'Intelligenzit'),
      el('div', { class: 'brand-sub' }, 'AI Workforce')));
}

function Sidebar(active) {
  const nav = el('nav', { class: 'nav', 'aria-label': 'Primary' },
    el('div', { class: 'nav-label' }, 'Operations'),
    ...NAV.map((item) => el('a', {
      class: 'nav-link', href: item.href,
      ...(item.id === active ? { 'aria-current': 'page' } : {}),
    }, icon(item.icon, { size: 17 }), el('span', {}, item.label))));

  return el('aside', { class: 'sidebar', id: 'sidebar' },
    el('div', { class: 'brand-block' }, Brand()),
    nav,
    el('div', { class: 'sidebar-foot', id: 'platform-status' },
      el('div', { class: 'status-line' },
        el('span', { class: 'dot' }), el('span', { class: 'label' }, 'Checking platform…'))),
    el('div', { class: 'sidebar-foot', id: 'identity' }));
}

function closeSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (sidebar) sidebar.classList.remove('open');
  const toggle = document.querySelector('.sidebar-toggle');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
}

function statusLine(state, label, value) {
  return el('div', { class: `status-line ${state}` },
    el('span', { class: 'dot' }),
    el('span', { class: 'label' }, label),
    el('span', { class: 'value' }, value));
}

async function renderPlatformStatus() {
  const host = document.getElementById('platform-status');
  const chip = document.getElementById('provider-chip');
  if (!host) return;
  try {
    const health = await platformHealth();
    const engine = health.llm.active_engine;
    const providerLabel = engine === 'local'
      ? 'Local engine'
      : `${titleise(health.llm.provider)} · ${health.llm.model}`;
    clear(host).append(
      statusLine(health.status === 'ok' ? 'ok' : 'warn', 'Backend',
        health.status === 'ok' ? 'Online' : 'Degraded'),
      statusLine(engine === 'local' ? 'warn' : 'ok', 'Provider', providerLabel),
      statusLine('', 'Environment', config.isDefaultBase() ? 'Local' : 'Remote'));
    if (chip) {
      clear(chip).append(engine === 'local'
        ? StatusBadge('awaiting_approval', { label: 'Local engine' })
        : StatusBadge('completed', { label: titleise(health.llm.provider) }));
      chip.title = health.llm.detail || '';
    }
  } catch (error) {
    clear(host).append(
      statusLine('bad', 'Backend', 'Unreachable'),
      el('div', { class: 'xs muted' }, config.base()));
    if (chip) clear(chip).append(StatusBadge('failed', { label: 'Offline' }));
  }
}

function ApplicationMenu() {
  return IconButton('layers', {
    title: 'Application menu',
    onClick: async () => {
      let health = null;
      try { health = await platformHealth(); } catch { /* handled below */ }
      let me = null;
      try { me = await currentUser(); } catch { /* shown as unknown */ }
      openModal({
        title: 'Application',
        body: el('div', { class: 'stack' },
          el('dl', { class: 'kv' },
            el('dt', {}, 'Platform'), el('dd', {}, health ? health.product : 'Unreachable'),
            el('dt', {}, 'Version'), el('dd', {}, health ? `v${health.version}` : '—'),
            el('dt', {}, 'API address'), el('dd', { class: 'mono small' }, config.base()),
            el('dt', {}, 'Signed-in user'),
            el('dd', {}, me
              ? `${me.user.display_name || me.user.username} (${titleise(me.user.role)})`
              : '—')),
          el('div', { class: 'row' },
            Button('API reference', { iconName: 'external', href: `${config.base()}/api/docs` }),
            Button('Health endpoint', { iconName: 'external', href: `${config.base()}/health` }),
            Button('Settings', { iconName: 'settings', href: 'settings.html' }))),
        footer: [Button('Close', { onClick: closeModal })],
      });
    },
  });
}

function TopBar({ title, breadcrumb, actions }) {
  const crumbs = el('div', { class: 'breadcrumb' });
  (breadcrumb || []).forEach((crumb, index) => {
    if (index) crumbs.append(el('span', { class: 'sep', 'aria-hidden': 'true' }, '/'));
    crumbs.append(crumb.href
      ? el('a', { href: crumb.href }, crumb.label)
      : el('span', { 'aria-current': 'page' }, crumb.label));
  });

  const themeButton = IconButton('theme', { title: 'Switch between light and dark' });
  themeButton.addEventListener('click', () => { toggleTheme(); });

  return el('header', { class: 'topbar' },
    el('button', {
      class: 'icon-btn sidebar-toggle', type: 'button', 'aria-label': 'Open navigation',
      'aria-controls': 'sidebar', 'aria-expanded': 'false',
      onclick: (event) => {
        const sidebar = document.getElementById('sidebar');
        const open = sidebar.classList.toggle('open');
        event.currentTarget.setAttribute('aria-expanded', String(open));
      },
    }, icon('menu')),
    el('div', { class: 'topbar-titles grow' },
      el('h1', {}, title),
      breadcrumb && breadcrumb.length ? crumbs : null),
    el('div', { class: 'topbar-actions' },
      el('span', { id: 'provider-chip' }),
      ...(actions || []),
      themeButton,
      ApplicationMenu()));
}

/**
 * Mount the shell and return the content node.
 * Pages never build chrome themselves — they receive a content element.
 */
export function mountShell({ title, breadcrumb = [], active = '', actions = [] }) {
  applyStoredTheme();
  document.title = `${title} · Intelligenzit AI Workforce`;

  const content = el('main', { class: 'content', id: 'main', tabindex: '-1' });
  const scrim = el('div', { class: 'sidebar-scrim', 'aria-hidden': 'true' });
  const shell = el('div', { class: 'shell' },
    Sidebar(active),
    scrim,
    el('div', { class: 'main' }, TopBar({ title, breadcrumb, actions }), content));

  scrim.addEventListener('click', () => closeSidebar());

  document.body.prepend(
    el('a', { class: 'skip-link', href: '#main' }, 'Skip to content'),
    shell);

  // Mobile: close the drawer on outside click or Escape.
  document.addEventListener('click', (event) => {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar || !sidebar.classList.contains('open')) return;
    if (sidebar.contains(event.target) || event.target.closest('.sidebar-toggle')) return;
    closeSidebar();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeSidebar();
  });

  renderPlatformStatus();
  renderIdentity(active);
  return content;
}

/**
 * Bootstrap a page: mount the shell, run `render`, and own the loading, error
 * and retry states so no page repeats them.
 */
export function page(options, render) {
  const content = mountShell(options);
  let disposer = null;

  const run = async () => {
    if (typeof disposer === 'function') { disposer(); disposer = null; }
    clear(content).append(LoadingState(options.loadingLabel || 'Loading…'));
    try {
      const result = await render({ content, reload: run });
      clear(content);
      if (result && result.node) {
        content.append(result.node);
        disposer = result.dispose || null;
      } else if (result) {
        content.append(result);
      }
    } catch (error) {
      clear(content).append(ErrorState(error, { onRetry: run }));
      // Kept for support; never shown to the user as a stack trace.
      console.error('[Intelligenzit]', options.title, error);
    }
  };

  window.addEventListener('beforeunload', () => {
    if (typeof disposer === 'function') disposer();
  });

  run();
  return { content, reload: run };
}

