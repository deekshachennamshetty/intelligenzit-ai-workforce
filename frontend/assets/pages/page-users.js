/* User administration (admins only; the API enforces this — the page just reports what it is told). */

import { el, clear } from '../lib/dom.js';
import { titleise, when } from '../lib/format.js';
import { api, ApiError } from '../api/index.js';
import {
  page, Card, Button, Badge, DataTable, EmptyState, Banner, toast, currentUser,
} from '../components/index.js';

const ROLE_HELP = {
  viewer: 'Read-only access to workflows, agents, memory and metrics.',
  operator: 'Everything a viewer can do, plus creating and running workflows.',
  admin: 'Everything an operator can do, plus users, governance and the audit log.',
};

page({
  title: 'Users',
  breadcrumb: [{ label: 'Platform' }, { label: 'Users' }],
  active: 'users',
  loadingLabel: 'Loading users…',
}, async ({ reload }) => {
  const [{ users, roles }, { user: me }] = await Promise.all([api.auth.listUsers(), currentUser()]);

  const run = async (action, success) => {
    try {
      await action();
      toast(success, { variant: 'good' });
      await reload();
    } catch (error) {
      toast(error instanceof ApiError ? error.message : 'That did not work.', { variant: 'error' });
    }
  };

  const roleSelect = (user) => {
    const select = el('select', {
      'aria-label': `Role for ${user.username}`, disabled: user.id === me.id,
      onchange: (event) => run(
        () => api.auth.updateUser(user.id, { role: event.target.value }),
        `${user.username} is now ${titleise(event.target.value)}.`),
    }, ...roles.map((role) => el('option', { value: role, ...(role === user.role ? { selected: true } : {}) },
      titleise(role))));
    return select;
  };

  const columns = [
    { key: 'username', label: 'User', sortable: true,
      render: (user) => el('div', {},
        el('span', { class: 'primary-cell' }, user.username),
        el('span', { class: 'sub' }, user.display_name || '—')) },
    { key: 'role', label: 'Role', render: roleSelect },
    { key: 'enabled', label: 'Status',
      render: (user) => Badge(user.enabled ? 'Active' : 'Disabled',
        { variant: user.enabled ? 'good' : 'warning', dot: true }) },
    { key: 'last_login_at', label: 'Last sign-in',
      render: (user) => el('span', { class: 'small muted nowrap' },
        user.last_login_at ? when(user.last_login_at) : 'never') },
    { key: 'actions', label: '',
      render: (user) => el('div', { class: 'row tight', style: 'flex-wrap:nowrap' },
        Button('Reset password', { size: 'sm', onClick: () => {
          const password = window.prompt(`New password for ${user.username} (at least 8 characters):`);
          if (password) run(() => api.auth.updateUser(user.id, { password }),
            `Password reset for ${user.username}. Their sessions were ended.`);
        } }),
        user.id === me.id ? null : Button(user.enabled ? 'Disable' : 'Enable', { size: 'sm', onClick: () =>
          run(() => api.auth.updateUser(user.id, { enabled: !user.enabled }),
            `${user.username} ${user.enabled ? 'disabled' : 'enabled'}.`) }),
        user.id === me.id ? null : Button('Delete', { size: 'sm', variant: 'danger', onClick: () => {
          if (window.confirm(`Delete ${user.username}? This cannot be undone.`)) {
            run(() => api.auth.deleteUser(user.id), `${user.username} deleted.`);
          }
        } })),
    },
  ];

  /* ------------------------------------------------------------ create form */
  const alertSlot = el('div');
  const username = el('input', { type: 'text', id: 'new-username', autocomplete: 'off',
    autocapitalize: 'none', spellcheck: 'false', placeholder: 'e.g. jsmith' });
  const displayName = el('input', { type: 'text', id: 'new-display-name', placeholder: 'Optional' });
  const password = el('input', { type: 'password', id: 'new-password', autocomplete: 'new-password',
    placeholder: 'At least 8 characters' });
  const role = el('select', { id: 'new-role' },
    ...roles.map((r) => el('option', { value: r, ...(r === 'viewer' ? { selected: true } : {}) }, titleise(r))));
  const help = el('span', { class: 'help' }, ROLE_HELP.viewer);
  role.addEventListener('change', () => { help.textContent = ROLE_HELP[role.value] || ''; });

  const form = el('form', {
    id: 'create-user-form', novalidate: true,
    onsubmit: async (event) => {
      event.preventDefault();
      clear(alertSlot);
      try {
        await api.auth.createUser({
          username: username.value.trim(), password: password.value,
          role: role.value, display_name: displayName.value.trim(),
        });
        toast(`${username.value.trim()} created.`, { variant: 'good' });
        await reload();
      } catch (error) {
        alertSlot.append(Banner('error', 'Could not create the user',
          error instanceof ApiError ? error.message : 'Unexpected error.'));
      }
    },
  },
    el('div', { class: 'grid k2' },
      el('label', { class: 'field', for: 'new-username' }, el('span', { class: 'label' }, 'Username'), username),
      el('label', { class: 'field', for: 'new-display-name' }, el('span', { class: 'label' }, 'Display name'), displayName)),
    el('div', { class: 'grid k2' },
      el('label', { class: 'field', for: 'new-password' }, el('span', { class: 'label' }, 'Initial password'), password),
      el('label', { class: 'field', for: 'new-role' }, el('span', { class: 'label' }, 'Role'), role, help)),
    alertSlot,
    el('div', { class: 'row mt4' }, Button('Create user', { variant: 'primary', type: 'submit', iconName: 'plus' })));

  return el('div', { class: 'stack' },
    Card({ title: 'Accounts', hint: `${users.length} user${users.length === 1 ? '' : 's'}` },
      DataTable(users, columns, {
        defaultSort: { key: 'username', dir: 'asc' },
        empty: EmptyState('No users', 'Create the first account below.'),
      })),
    Card({ title: 'Add a user', subtitle: 'Roles: viewer, operator, admin. Changing a role, disabling an account or resetting a password ends that user\'s sessions.' }, form));
});
