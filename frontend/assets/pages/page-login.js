/* Sign in. Stores the session token where the API client already looks for it. */

import { el, clear } from '../lib/dom.js';
import { api, config, ApiError } from '../api/index.js';
import { Card, Button, Banner } from '../components/index.js';

const params = new URLSearchParams(window.location.search);

/* Only ever return to one of this application's own pages — never an arbitrary URL. */
function destination() {
  const next = params.get('next') || '';
  return /^[A-Za-z0-9_-]+\.html(\?[^#]*)?$/.test(next) ? next : 'dashboard.html';
}

async function alreadySignedIn() {
  try {
    const status = await api.auth.status();
    if (!status.auth_enabled) return true;
    if (!config.key()) return false;
    await api.auth.me();
    return true;
  } catch {
    return false;
  }
}

async function main() {
  if (await alreadySignedIn()) {
    window.location.replace(destination());
    return;
  }

  let status = { users_configured: true };
  try { status = await api.auth.status(); } catch { /* the form still renders */ }

  const alertSlot = el('div');
  const username = el('input', {
    type: 'text', id: 'username', name: 'username', autocomplete: 'username', required: true,
    autocapitalize: 'none', spellcheck: 'false',
  });
  const password = el('input', {
    type: 'password', id: 'password', name: 'password', autocomplete: 'current-password',
    required: true,
  });
  const submit = Button('Sign in', { variant: 'primary', size: 'lg', type: 'submit' });

  const form = el('form', {
    id: 'login-form', novalidate: true,
    onsubmit: async (event) => {
      event.preventDefault();
      clear(alertSlot);
      if (!username.value.trim() || !password.value) {
        alertSlot.append(Banner('warn', 'Enter your username and password', ''));
        return;
      }
      submit.disabled = true;
      try {
        const session = await api.auth.login(username.value.trim(), password.value);
        config.setKey(session.token);
        window.location.replace(destination());
      } catch (error) {
        submit.disabled = false;
        password.value = '';
        const message = error instanceof ApiError ? error.message : 'Could not sign in.';
        alertSlot.append(Banner('error', 'Sign-in failed', message));
      }
    },
  },
    el('label', { class: 'field', for: 'username' }, el('span', { class: 'label' }, 'Username'), username),
    el('label', { class: 'field', for: 'password' }, el('span', { class: 'label' }, 'Password'), password),
    alertSlot,
    el('div', { class: 'row mt4' }, submit));

  const setupHint = status.users_configured ? null : Banner('info', 'No users exist yet',
    el('span', {}, 'Create the first administrator on the server with ',
      el('code', {}, 'python -m backend.auth create-user --username <name> --role admin'),
      ', or set INTELLIGENZIT_ADMIN_USERNAME and INTELLIGENZIT_ADMIN_PASSWORD before starting the service.'));

  document.body.append(el('div', {
    style: 'min-height:100vh;display:grid;place-items:center;padding:24px',
  },
    el('div', { style: 'width:100%;max-width:400px', class: 'stack' },
      el('div', { class: 'brand-mark', style: 'justify-content:center' },
        el('div', { class: 'brand-glyph', 'aria-hidden': 'true' }, 'IZ'),
        el('div', {},
          el('div', { class: 'brand-name' }, 'Intelligenzit'),
          el('div', { class: 'brand-sub' }, 'AI Workforce'))),
      setupHint,
      Card({ title: 'Sign in', subtitle: 'Use the account an administrator created for you.' }, form))));

  username.focus();
}

main();
