/* Governance: permissions, review gates, containment, audit and security posture.
   Nothing on this page reads or displays a credential. */

import { el, icon, clear } from '../lib/dom.js';
import { titleise, when, number, statusLabel, truncate } from '../lib/format.js';
import { api } from '../api/index.js';
import {
  page, Card, Button, Badge, StatusBadge, MetricCard, DataTable, EmptyState,
  Banner, Tabs, KeyValue,
} from '../components/index.js';

const STATUS_TONE = {
  enforced: 'good', enabled: 'good', permissive: 'warning', conditional: 'warning',
  not_configured: 'serious', not_implemented: '',
};

page({
  title: 'Governance',
  breadcrumb: [{ label: 'Platform' }, { label: 'Governance' }],
  active: 'governance',
  loadingLabel: 'Reading governance state…',
}, async () => {
  const data = await api.governance.overview();
  const { policy, permissions, review_gates: gates, containment, security, audit, provider } = data;

  const open = security.filter((c) => ['not_configured', 'not_implemented', 'permissive']
    .includes(c.status));

  const root = el('div', { class: 'stack' });

  root.append(el('div', { class: 'grid k4' },
    MetricCard({ label: 'Controls enforced',
      value: `${security.filter((c) => c.status === 'enforced' || c.status === 'enabled').length}/${security.length}`,
      foot: open.length ? `${open.length} require attention before exposure` : 'All controls active',
      meter: { value: (security.length - open.length) / security.length * 100,
               variant: open.length ? 'warning' : 'good' } }),
    MetricCard({ label: 'Authorisation denials', value: number(containment.authorisation_denials),
      foot: 'agents refused an action outside their permissions' }),
    MetricCard({ label: 'Contained agent failures', value: number(containment.contained_failures),
      foot: `${containment.failed_agent_runs} agent failures, `
        + `${containment.failed_workflows} workflows failed` }),
    MetricCard({ label: 'Escalations', value: number(containment.escalations),
      foot: 'released with open items for human sign-off' })));

  if (open.length) {
    root.append(Banner('warn', 'Before exposing this platform beyond localhost',
      el('ul', { style: 'margin:var(--s2) 0 0' },
        ...open.map((control) => el('li', {}, `${control.control}: ${control.detail}`)))));
  }

  root.append(el('div', { class: 'grid split' },
    Card({ title: 'Security posture', subtitle: 'Controls actually enforced by this codebase.' },
      DataTable(security, [
        { key: 'control', label: 'Control',
          render: (row) => el('span', { class: 'primary-cell' }, row.control) },
        { key: 'status', label: 'Status', width: '150px',
          render: (row) => Badge(statusLabel(row.status),
            { variant: STATUS_TONE[row.status] || '', dot: true }) },
        { key: 'detail', label: 'Detail',
          render: (row) => el('span', { class: 'small muted' }, row.detail) },
      ])),
    el('div', { class: 'stack' },
      Card({ title: 'Provider status' },
        el('div', { class: 'row tight', style: 'margin-bottom:var(--s4)' },
          provider.active_engine === 'local'
            ? Badge('Local development engine', { variant: 'warning', dot: true })
            : Badge(`${titleise(provider.provider)} configured`, { variant: 'good', dot: true })),
        KeyValue([
          ['Provider', titleise(provider.provider)],
          ['Model', provider.model],
          ['Credentials', provider.credentials_configured ? 'Configured' : 'Not configured'],
          ['Local fallback', provider.fallback_allowed ? 'Permitted' : 'Disabled'],
          ['Detail', el('span', { class: 'small muted' }, provider.detail || '—')],
        ]),
        el('p', { class: 'xs muted mt4 mb0' },
          'Credentials are read only by the configuration module and are never logged, '
          + 'persisted or returned by any endpoint. No key value appears anywhere in this '
          + 'interface.')),
      Card({ title: 'Orchestration policy' },
        KeyValue([
          ['Rework cap', `${policy.max_rework_cycles} cycles`],
          ['Human approval', policy.require_human_approval ? 'Required' : 'On escalation only'],
          ['API key required', policy.api_key_required ? 'Yes' : 'No'],
          ['CORS origins', policy.cors_origins.join(', ')],
        ])))));

  root.append(Card({
    title: 'Review gates',
    subtitle: 'Where work can be stopped, who stops it, and what happens then.',
  },
    DataTable(gates, [
      { key: 'gate', label: 'Gate', width: '170px',
        render: (row) => el('span', { class: 'primary-cell' }, row.gate) },
      { key: 'owner', label: 'Owner', width: '180px',
        render: (row) => (row.owner === 'human'
          ? Badge('Human reviewer', { variant: 'warning' })
          : el('a', { href: `agents.html?agent=${encodeURIComponent(row.owner)}` },
              titleise(row.owner))) },
      { key: 'checks', label: 'What it checks',
        render: (row) => el('span', { class: 'small' }, row.checks) },
      { key: 'enforcement', label: 'Enforcement',
        render: (row) => el('span', { class: 'small muted' }, row.enforcement) },
    ])));

  /* ---------------------------------------------------- permission matrix */
  // Opens on the agents that actually execute; the full registry is one click away.
  let permissionFilter = 'executing';
  const matrixSlot = el('div');
  const catalogue = permissions.catalogue;

  const drawMatrix = () => {
    const rows = permissions.matrix.filter((agent) =>
      permissionFilter === 'all'
        ? true
        : permissionFilter === 'executing'
          ? agent.executes
          : agent.permissions.includes(permissionFilter));
    clear(matrixSlot).append(DataTable(rows, [
      { key: 'name', label: 'Agent', sortable: true, width: '230px',
        render: (agent) => el('div', {},
          el('a', { class: 'primary-cell nowrap',
            href: `agents.html?agent=${encodeURIComponent(agent.agent_id)}` }, agent.name),
          el('span', { class: 'sub' }, agent.department_name)) },
      { key: 'executes', label: 'On path', width: '96px',
        render: (agent) => Badge(agent.executes ? 'Yes' : 'Standby',
          { variant: agent.executes ? 'good' : '', dot: true }) },
      ...catalogue.map((permission) => ({
        key: permission.id, label: permission.short || permission.label, align: 'right',
        render: (agent) => (agent.permissions.includes(permission.id)
          ? el('span', { title: `${permission.label} — ${permission.detail}`,
              style: 'color:var(--good-ink)' }, icon('check', { size: 14 }))
          : el('span', { class: 'muted', title: `Not permitted: ${permission.label}` }, '·')),
      })),
    ], { compact: true, defaultSort: { key: 'name', dir: 'asc' } }));
  };

  const permissionTabs = Tabs([
    { id: 'executing', label: 'On the execution path',
      count: permissions.matrix.filter((a) => a.executes).length },
    { id: 'all', label: 'All agents', count: permissions.matrix.length },
    ...catalogue.filter((p) => p.id.startsWith('write:') || p.id.startsWith('approve:'))
      .map((permission) => ({
        id: permission.id, label: permission.label,
        count: permissions.matrix.filter((a) => a.permissions.includes(permission.id)).length,
      })),
  ], {
    active: 'executing', ariaLabel: 'Filter the permission matrix',
    onSelect: (id) => {
      permissionFilter = id;
      Array.from(permissionTabs.children).forEach((button) =>
        button.setAttribute('aria-selected', String(button.id === `tab-${id}`)));
      drawMatrix();
    },
  });

  root.append(Card({
    title: 'Agent authorisation',
    subtitle: 'Every agent holds explicit permissions. An action outside them is refused and '
      + 'audited before any state changes. Hover a mark for the full permission.',
  }, permissionTabs, el('div', { class: 'mt4' }, matrixSlot)));
  drawMatrix();

  /* -------------------------------------------------------------- audit -- */
  let auditQuery = '';
  const auditSlot = el('div');
  const drawAudit = () => {
    const rows = (audit || []).filter((entry) => !auditQuery
      || `${entry.actor} ${entry.action} ${entry.target} ${entry.detail}`
        .toLowerCase().includes(auditQuery));
    clear(auditSlot).append(DataTable(rows, [
      { key: 'ts', label: 'Timestamp', width: '150px',
        render: (row) => el('span', { class: 'small muted nowrap' }, when(row.ts)) },
      { key: 'target', label: 'Workflow', width: '130px',
        render: (row) => (row.target && row.target.length <= 24
          ? el('a', { class: 'mono small',
              href: `workflow.html?id=${encodeURIComponent(row.target)}` }, row.target)
          : el('span', { class: 'muted small' }, row.target || '—')) },
      { key: 'actor', label: 'Agent', render: (row) => titleise(row.actor) },
      { key: 'action', label: 'Action', render: (row) => el('span', { class: 'mono small' }, row.action) },
      { key: 'outcome', label: 'Result',
        render: (row) => Badge(statusLabel(row.outcome || 'ok'),
          { variant: row.outcome === 'denied' || row.outcome === 'error' ? 'critical'
            : row.outcome === 'ok' ? 'good' : '', dot: true }) },
      { key: 'detail', label: 'Detail',
        render: (row) => el('span', { class: 'small muted' }, truncate(row.detail, 160)) },
    ], {
      defaultSort: { key: 'ts', dir: 'desc' },
      empty: EmptyState('Audit log is empty',
        'Authorisation decisions, agent failures and human sign-offs are recorded here.',
        { iconName: 'shield' }),
    }));
  };

  root.append(Card({
    title: 'Audit log',
    hint: `${(audit || []).length} most recent entries`,
  },
    el('div', { class: 'row', style: 'margin-bottom:var(--s4)' },
      el('input', {
        type: 'search', placeholder: 'Filter by agent, action or workflow…',
        'aria-label': 'Filter audit log', style: 'max-width:340px',
        oninput: (event) => { auditQuery = event.target.value.trim().toLowerCase(); drawAudit(); },
      })),
    auditSlot));
  drawAudit();

  root.append(Card({ title: 'Failure containment' },
    KeyValue([
      ['Agent executions recorded', number(containment.agent_runs)],
      ['Agent executions failed', number(containment.failed_agent_runs)],
      ['Workflows failed', number(containment.failed_workflows)],
      ['Failures contained', `${number(containment.contained_failures)} — an agent failed without `
        + 'taking the engagement or the service down'],
      ['Escalations', number(containment.escalations)],
    ]),
    el('p', { class: 'small muted mt4 mb0' },
      'Every agent execution runs inside a contained scope that records the failure, marks the '
      + 'stage, and prevents an internal error reaching the API as a stack trace. Stages that '
      + 'were never reached are marked as such rather than as failures.')));

  return root;
});
