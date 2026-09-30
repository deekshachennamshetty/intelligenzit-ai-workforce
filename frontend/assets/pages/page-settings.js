/* Settings: configuration, connectivity and provider status.
   Secrets are never displayed. The API key field is write-only: the interface
   shows whether one is stored, never its value. */

import { el, icon, clear } from '../lib/dom.js';
import { titleise, number, statusLabel } from '../lib/format.js';
import { api, config, ApiError } from '../api/index.js';
import {
  page, Card, Button, Badge, StatusBadge, KeyValue, Banner, DataTable,
  ErrorState, toast, EmptyState, MetricCard,
} from '../components/index.js';
import { platformHealth } from '../components/shell.js';

const CONNECTION_STATES = {
  connected: ['Connected', 'good'],
  degraded: ['Degraded', 'warning'],
  not_configured: ['Not configured', 'serious'],
  error: ['Error', 'critical'],
};

function connectionBadge(state) {
  const [label, variant] = CONNECTION_STATES[state] || CONNECTION_STATES.error;
  return Badge(label.toUpperCase(), { variant, dot: true, size: 'lg' });
}

function providerState(health) {
  if (!health) return 'error';
  if (health.llm.active_engine === 'local') {
    return health.llm.credentials_configured ? 'degraded' : 'not_configured';
  }
  return 'connected';
}

page({
  title: 'Settings',
  breadcrumb: [{ label: 'Platform' }, { label: 'Settings' }],
  active: 'settings',
  loadingLabel: 'Reading configuration…',
}, async () => {
  const [health, platformConfig, governance] = await Promise.all([
    platformHealth({ refresh: true }),
    api.platform.configuration(),
    api.governance.overview().catch(() => null),
  ]);

  const state = providerState(health);
  const root = el('div', { class: 'stack' });

  root.append(el('div', { class: 'grid k4' },
    MetricCard({ label: 'Platform', value: health.status === 'ok' ? 'Healthy' : 'Degraded',
      foot: `${health.product} v${health.version}` }),
    MetricCard({ label: 'Database', value: titleise(health.database),
      foot: 'SQLite, local to this deployment' }),
    MetricCard({ label: 'Active workflows', value: number(health.active_workflows) }),
    MetricCard({ label: 'Registered agents', value: number(health.agents_registered),
      foot: `${health.departments} departments` })));

  /* ------------------------------------------------------------ provider */
  root.append(el('div', { class: 'grid split' },
    Card({ title: 'Language model provider', hint: connectionBadge(state) },
      KeyValue([
        ['Provider', titleise(health.llm.provider)],
        ['Model', el('span', { class: 'mono small' }, health.llm.model)],
        ['Selectable providers',
          (health.llm.supported_providers || []).map(titleise).join(', ') || '—'],
        ['Credentials', health.llm.credentials_configured
          ? Badge('Configured', { variant: 'good', dot: true })
          : Badge('Not configured', { variant: 'serious', dot: true })],
        ['Active engine', health.llm.active_engine === 'local'
          ? Badge('Local development engine', { variant: 'warning', dot: true })
          : Badge(titleise(health.llm.active_engine), { variant: 'good', dot: true })],
        ['Local fallback', platformConfig.local_fallback_allowed ? 'Permitted' : 'Disabled'],
        ['Detail', el('span', { class: 'small muted' }, health.llm.detail || '—')],
      ]),
      state !== 'connected'
        ? Banner('warn', 'Agent content is not model-generated right now',
            `Set ${String(health.llm.provider).toUpperCase()}_API_KEY in the platform's .env file `
            + 'and restart. Orchestration, review, governance and persistence run unchanged; '
            + 'every deliverable produced meanwhile says so in its execution note.')
        : null,
      el('p', { class: 'xs muted mt4 mb0' },
        'Credentials are read from the environment or the .env file by the configuration module '
        + 'alone. They are never written to logs, stored in the database, or returned by any '
        + 'endpoint — including this one.')),
    Card({ title: 'Environment' },
      KeyValue([
        ['API address', el('span', { class: 'mono small' }, config.base())],
        ['Origin', config.isDefaultBase() ? 'Same origin as this interface' : 'Overridden in this browser'],
        ['Interface API key', config.key()
          ? Badge('Stored in this browser', { variant: 'good', dot: true })
          : Badge('None stored', { dot: true })],
        ['Platform requires a key', platformConfig.api_key_required ? 'Yes' : 'No'],
        ['Database file', el('span', { class: 'mono small' }, platformConfig.database)],
        ['Version', `v${platformConfig.version}`],
      ]))));

  /* -------------------------------------------------------- connection -- */
  const baseInput = el('input', {
    type: 'text', id: 'api-base', value: config.base(), placeholder: 'http://127.0.0.1:8000',
  });
  const keyInput = el('input', {
    type: 'password', id: 'api-key', value: '', autocomplete: 'off',
    placeholder: config.key() ? '•••••••• (stored — type to replace)' : 'Only needed if the platform requires one',
  });
  const testSlot = el('div');

  root.append(Card({ title: 'Interface connection',
                     subtitle: 'Where this browser sends its API calls.' },
    el('label', { class: 'field', for: 'api-base' },
      el('span', { class: 'label' }, 'Backend API address'), baseInput,
      el('span', { class: 'help' },
        'Leave as the current origin unless the interface is served separately from the API.')),
    el('label', { class: 'field', for: 'api-key' },
      el('span', { class: 'label' }, 'Platform API key'), keyInput,
      el('span', { class: 'help' },
        'Sent as the X-API-Key header. Stored in this browser only and never displayed again '
        + 'once saved.')),
    testSlot,
    el('div', { class: 'row' },
      Button('Save and test', { variant: 'primary', iconName: 'check', onClick: async () => {
        config.setBase(baseInput.value);
        if (keyInput.value.trim()) config.setKey(keyInput.value.trim());
        keyInput.value = '';
        clear(testSlot);
        try {
          const check = await api.platform.health();
          testSlot.append(Banner('info', 'Connected',
            `${check.product} v${check.version} responded successfully.`));
          toast('Connection verified.', { variant: 'good' });
        } catch (error) {
          testSlot.append(ErrorState(error));
        }
      } }),
      Button('Clear stored key', { onClick: () => {
        config.setKey('');
        keyInput.placeholder = 'Only needed if the platform requires one';
        toast('Stored API key removed from this browser.');
      } }),
      Button('Reset to defaults', { variant: 'ghost', onClick: () => {
        config.setBase(''); config.setKey('');
        baseInput.value = config.base(); keyInput.value = '';
        clear(testSlot);
        toast('Connection settings reset.');
      } }))));

  /* ------------------------------------------------ workflow & governance */
  root.append(el('div', { class: 'grid k3' },
    Card({ title: 'Workflow configuration' },
      KeyValue([
        ['Pipeline stages', String(platformConfig.stages.length)],
        ['Graph nodes', String(platformConfig.graph.nodes.length)],
        ['Execution order', 'Sequential — one stage runs at a time'],
        ['Rework cap', `${platformConfig.max_rework_cycles} cycles`],
      ])),
    Card({ title: 'Memory configuration' },
      KeyValue([
        ['Store', 'SQLite, its own database separate from engagement history'],
        ['Recall', 'Lexical overlap across the stored corpus'],
        ['Written by', 'The AI Executive, automatically, after each engagement completes'],
        ['Injected into', 'The AI Executive, before planning the next engagement'],
      ]),
      el('p', { class: 'xs muted mt4 mb0' },
        'Recall is keyword overlap, not embedding search. This is the seam a managed vector '
        + 'store would replace.')),
    Card({ title: 'Governance configuration' },
      KeyValue([
        ['Human review point', platformConfig.require_human_approval
          ? Badge('Required on every deliverable', { variant: 'warning', dot: true })
          : 'On escalation only'],
        ['API authentication', platformConfig.api_key_required
          ? Badge('Enabled', { variant: 'good', dot: true })
          : Badge('Not configured', { variant: 'serious', dot: true })],
        ['Audit entries', governance ? number((governance.audit || []).length) : '—'],
        ['Controls enforced', governance
          ? `${governance.security.filter((c) => ['enforced', 'enabled'].includes(c.status)).length}`
            + ` of ${governance.security.length}` : '—'],
      ]),
      Button('Open governance', { size: 'sm', variant: 'ghost', href: 'governance.html',
        iconName: 'governance' }))));

  /* ------------------------------------------------------------- graph -- */
  root.append(Card({ title: 'Execution graph', hint: 'LangGraph topology in use' },
    DataTable(platformConfig.graph.edges, [
      { key: 'from', label: 'From', render: (edge) => titleise(edge.from) },
      { key: 'to', label: 'To', render: (edge) => titleise(edge.to) },
      { key: 'kind', label: 'Edge',
        render: (edge) => (edge.condition
          ? Badge(`conditional · ${edge.condition}`, { variant: 'serious' })
          : edge.parallel ? Badge('parallel fan-out', { variant: 'accent' })
          : edge.join ? Badge('join', { variant: 'accent' })
          : Badge('sequential')) },
    ], { compact: true })));

  /* ------------------------------------------------------------ api ----- */
  root.append(Card({ title: 'API status', subtitle: 'Endpoints this interface depends on.' },
    DataTable([
      ['GET /health', 'Service, database and provider status'],
      ['GET /api/config', 'Public configuration, stage list and graph topology'],
      ['GET /api/dashboard', 'Dashboard roll-up'],
      ['POST /api/workflows', 'Submit an objective'],
      ['GET /api/workflows/{id}/status', 'Live pipeline state, events and tasks'],
      ['GET /api/workflows/{id}/result', 'Departmental output and deliverable'],
      ['GET /api/workflows/{id}/research', 'Structured research dossier'],
      ['GET /api/research', 'Research across engagements'],
      ['GET /api/agents', 'Organisation chart with live activity'],
      ['GET /api/metrics', 'Metrics computed from execution records'],
      ['GET /api/memory', 'Organisational memory and recall'],
      ['GET /api/governance', 'Permissions, gates, containment and audit'],
    ].map(([endpoint, purpose]) => ({ endpoint, purpose })), [
      { key: 'endpoint', label: 'Endpoint',
        render: (row) => el('span', { class: 'mono small' }, row.endpoint) },
      { key: 'purpose', label: 'Purpose',
        render: (row) => el('span', { class: 'small muted' }, row.purpose) },
    ], { compact: true }),
    el('div', { class: 'row mt4' },
      Button('OpenAPI reference', { iconName: 'external', href: `${config.base()}/api/docs` }),
      Button('Health endpoint', { iconName: 'external', href: `${config.base()}/health` }))));

  return root;
});
