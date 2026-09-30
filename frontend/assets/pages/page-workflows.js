/* Searchable engagement history. */

import { el, clear } from '../lib/dom.js';
import { duration, when, titleise, truncate, number } from '../lib/format.js';
import { api } from '../api/index.js';
import {
  page, Card, Button, StatusBadge, DataTable, EmptyState, Tabs, MetricCard,
} from '../components/index.js';

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'running', label: 'Running' },
  { id: 'completed', label: 'Completed' },
  { id: 'awaiting_approval', label: 'Awaiting sign-off' },
  { id: 'failed', label: 'Failed' },
  { id: 'rework', label: 'Reworked' },
];

function matches(filter, workflow) {
  if (filter === 'all') return true;
  if (filter === 'rework') return (workflow.rework_count || 0) > 0;
  if (filter === 'running') return ['running', 'queued'].includes(workflow.status);
  return workflow.status === filter;
}

page({
  title: 'Workflows',
  breadcrumb: [{ label: 'Platform' }, { label: 'Workflows' }],
  active: 'workflows',
  loadingLabel: 'Loading engagement history…',
  actions: [Button('New workflow', { variant: 'primary', iconName: 'plus',
    href: 'new-workflow.html' })],
}, async () => {
  const [data, metrics] = await Promise.all([
    api.workflows.list({ limit: 200 }),
    api.metrics.report(),
  ]);
  const items = data.items || [];
  const summary = metrics.summary;

  let filter = 'all';
  let query = '';

  const tableSlot = el('div');
  const countLabel = el('span', { class: 'hint' });

  const visible = () => items
    .filter((workflow) => matches(filter, workflow))
    .filter((workflow) => !query
      || `${workflow.user_request} ${workflow.request_id} ${workflow.request_type || ''}`
        .toLowerCase().includes(query));

  const columns = [
    { key: 'request_id', label: 'Workflow', width: '110px',
      render: (row) => el('span', { class: 'mono small' }, row.request_id) },
    { key: 'user_request', label: 'Objective', sortable: true,
      render: (row) => el('div', {},
        el('span', { class: 'primary-cell' }, truncate(row.user_request, 92)),
        el('span', { class: 'sub' },
          [titleise(row.request_type || ''), titleise(row.priority || '')]
            .filter(Boolean).join(' · '))) },
    { key: 'status', label: 'Status', sortable: true,
      render: (row) => StatusBadge(row.status) },
    { key: 'created_at', label: 'Created', sortable: true,
      sortValue: (row) => row.created_at || '',
      render: (row) => el('span', { class: 'small muted nowrap' }, when(row.created_at)) },
    { key: 'duration_ms', label: 'Duration', align: 'right', sortable: true,
      sortValue: (row) => Number(row.duration_ms) || 0,
      render: (row) => duration(row.duration_ms) },
    { key: 'rework_count', label: 'Rework', align: 'right', sortable: true,
      sortValue: (row) => Number(row.rework_count) || 0,
      render: (row) => String(row.rework_count || 0) },
    { key: 'actions', label: '', width: '200px',
      render: (row) => el('div', { class: 'row tight', style: 'flex-wrap:nowrap' },
        Button('Open', { size: 'sm',
          href: `workflow.html?id=${encodeURIComponent(row.request_id)}` }),
        ['completed', 'awaiting_approval', 'rejected'].includes(row.status)
          ? Button('Deliverable', { size: 'sm',
              href: `deliverable.html?id=${encodeURIComponent(row.request_id)}` })
          : null) },
  ];

  const draw = () => {
    const rows = visible();
    countLabel.textContent = `${rows.length} of ${items.length} shown`;
    clear(tableSlot).append(DataTable(rows, columns, {
      defaultSort: { key: 'created_at', dir: 'desc' },
      onRowClick: (row) => {
        window.location.href = `workflow.html?id=${encodeURIComponent(row.request_id)}`;
      },
      empty: items.length
        ? EmptyState('Nothing matches', 'Try another status filter or clear the search.',
            { iconName: 'search' })
        : EmptyState('No engagements yet',
            'Submit a business objective and the AI workforce executes it end to end.',
            { action: Button('New workflow', { variant: 'primary', href: 'new-workflow.html' }) }),
    }));
  };

  const search = el('input', {
    type: 'search', placeholder: 'Search objectives and workflow IDs…',
    'aria-label': 'Search engagements', style: 'max-width:340px',
    oninput: (event) => { query = event.target.value.trim().toLowerCase(); draw(); },
  });

  const tabs = Tabs(
    FILTERS.map((f) => ({
      ...f,
      count: f.id === 'all' ? items.length : items.filter((w) => matches(f.id, w)).length,
    })),
    { active: filter, ariaLabel: 'Filter engagements',
      onSelect: (id) => {
        filter = id;
        Array.from(tabs.children).forEach((button) => {
          button.setAttribute('aria-selected', String(button.id === `tab-${id}`));
        });
        draw();
      } });

  const root = el('div', { class: 'stack' },
    el('div', { class: 'grid k4' },
      MetricCard({ label: 'Total engagements', value: number(summary.total_workflows) }),
      MetricCard({ label: 'Completed', value: number(summary.completed_workflows),
        foot: summary.total_workflows_finished ? `${summary.success_rate}% success rate` : '' }),
      MetricCard({ label: 'Reworked', value: number(summary.workflows_with_rework),
        foot: summary.reviewed_workflows ? `${summary.rework_rate}% of reviewed work` : '' }),
      MetricCard({ label: 'Failed', value: number(summary.failed_workflows),
        foot: summary.total_workflows_finished ? `${summary.failure_rate}% failure rate` : '' })),
    Card({ title: 'Engagement history', hint: countLabel },
      el('div', { class: 'row', style: 'margin-bottom:var(--s4)' }, search),
      tabs,
      el('div', { class: 'mt4' }, tableSlot)));

  draw();
  return root;
});
