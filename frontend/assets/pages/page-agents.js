/* The AI workforce: organisation chart, department roster and agent profiles. */

import { el, icon, clear } from '../lib/dom.js';
import { titleise, duration, when, relative, truncate, number } from '../lib/format.js';
import { api } from '../api/index.js';
import {
  page, Card, Button, Badge, StatusBadge, EmptyState, Banner, MetricCard, DataTable,
  DepartmentCard, OrgChart, AgentAvatar, AgentStatLine, Prose, Disclosure, KeyValue,
  openDrawer, DrawerHeader, DrawerBody, ErrorState, Tabs,
} from '../components/index.js';

const params = new URLSearchParams(window.location.search);

async function showAgent(agentId) {
  const panel = el('div', {},
    DrawerHeader('Loading…', ''),
    DrawerBody(el('div', { class: 'loading' }, el('span', { class: 'spinner' }), 'Reading profile…')));
  openDrawer(panel, { label: 'Agent profile' });

  try {
    const data = await api.agents.profile(agentId);
    const spec = data.agent;
    const stats = data.stats || {};
    clear(panel).append(
      DrawerHeader(spec.name, `${spec.title} · ${spec.department_name}`),
      DrawerBody(
        el('div', { class: 'row tight' },
          AgentAvatar({ ...spec, status: stats.status }, { size: 'lg' }),
          el('div', { class: 'grow' },
            el('div', { class: 'row tight' },
              stats.status === 'working'
                ? StatusBadge('working', { label: 'Working now' })
                : Badge(spec.executes ? 'On the execution path' : 'Standby', { dot: true }),
              Badge(titleise(spec.seniority))),
            el('div', { class: 'xs muted mt2' },
              spec.executes
                ? 'Executes inside the LangGraph workflow.'
                : 'Registered, permissioned and governed; not on the current execution path.'))),
        el('p', {}, spec.mission),
        AgentStatLine(stats),
        el('div', {},
          el('div', { class: 'section-title' }, 'Capabilities'),
          el('div', { class: 'chip-list' },
            ...spec.capabilities.map((capability) => Badge(titleise(capability))))),
        el('div', {},
          el('div', { class: 'section-title' }, 'Permissions'),
          el('div', { class: 'chip-list' },
            ...spec.permissions.map((permission) => Badge(permission, { variant: 'brand' })))),
        el('div', {},
          el('div', { class: 'section-title' },
            `Execution history (${data.history.length})`),
          data.history.length
            ? el('div', { class: 'stack tight' },
                ...data.history.map((run) => Disclosure(
                  `${when(run.started_at)} · ${titleise(run.node)} · ${run.status} · ${duration(run.duration_ms)}`,
                  el('div', { class: 'stack tight' },
                    run.objective
                      ? el('div', { class: 'small muted' }, `Engagement: ${run.objective}`) : null,
                    run.request_id
                      ? el('a', { class: 'small',
                          href: `workflow.html?id=${encodeURIComponent(run.request_id)}` },
                          'Open this engagement')
                      : null,
                    run.error ? Banner('error', 'This execution failed', run.error) : null,
                    run.output ? Prose(run.output) : null))))
            : el('p', { class: 'muted small' },
                spec.executes
                  ? 'This agent has not executed yet on this platform.'
                  : 'This role is registered and governed but is not part of the current '
                    + 'execution path, so it has no execution history.')),
        data.peers.length
          ? el('div', {},
              el('div', { class: 'section-title' }, 'Department peers'),
              el('div', { class: 'chip-list' },
                ...data.peers.map((peer) => el('button', {
                  class: 'badge', type: 'button', style: 'cursor:pointer',
                  onclick: () => showAgent(peer.id),
                }, peer.name))))
          : null));
  } catch (error) {
    clear(panel).append(
      DrawerHeader('Could not load agent', agentId),
      DrawerBody(ErrorState(error)));
  }
}

page({
  title: 'AI Workforce',
  breadcrumb: [{ label: 'Platform' }, { label: 'Agents' }],
  active: 'agents',
  loadingLabel: 'Reading the organisation…',
}, async () => {
  const [organisation, metrics] = await Promise.all([
    api.agents.organisation(),
    api.metrics.report(),
  ]);
  const totals = organisation.totals;
  const engaged = (metrics.agents || []).filter((a) => a.runs > 0);

  let view = 'departments';
  const viewSlot = el('div');

  const drawDepartments = () => clear(viewSlot).append(
    el('div', { class: 'grid split-even' },
      ...organisation.departments.map((department) =>
        DepartmentCard(department, { onSelect: (agent) => showAgent(agent.id) }))));

  const drawChart = () => clear(viewSlot).append(
    Card({ title: 'Pipeline structure',
           subtitle: 'The objective enters at the Orchestrator and passes through each '
             + 'department in sequence — no separate delegation layer decides routing.' },
      OrgChart(organisation.departments, { onSelect: (agent) => showAgent(agent.id) })));

  const drawTable = () => clear(viewSlot).append(
    Card({ title: 'Every agent', hint: `${totals.agents} registered` },
      DataTable(organisation.departments.flatMap((d) => d.agents), [
        { key: 'name', label: 'Agent', sortable: true,
          render: (agent) => el('div', { class: 'row tight' },
            AgentAvatar(agent),
            el('div', {},
              el('span', { class: 'primary-cell' }, agent.name),
              el('span', { class: 'sub' }, agent.title))) },
        { key: 'department', label: 'Department', sortable: true,
          render: (agent) => titleise(agent.department) },
        { key: 'capabilities', label: 'Capabilities',
          render: (agent) => el('div', { class: 'chip-list' },
            ...agent.capabilities.slice(0, 3).map((c) => Badge(titleise(c))),
            agent.capabilities.length > 3
              ? Badge(`+${agent.capabilities.length - 3}`) : null) },
        { key: 'executes', label: 'Status',
          render: (agent) => (agent.status === 'working'
            ? StatusBadge('working', { label: 'Working' })
            : Badge(agent.executes ? 'On path' : 'Standby', { dot: true })) },
        { key: 'runs', label: 'Runs', align: 'right', sortable: true,
          sortValue: (agent) => Number(agent.runs) || 0,
          render: (agent) => String(agent.runs || 0) },
        { key: 'reliability', label: 'Reliability', align: 'right', sortable: true,
          sortValue: (agent) => Number(agent.reliability) || -1,
          render: (agent) => (agent.reliability === null || agent.reliability === undefined
            ? '—' : `${agent.reliability}%`) },
        { key: 'last_active', label: 'Last execution',
          render: (agent) => el('span', { class: 'small muted nowrap' },
            agent.last_active ? relative(agent.last_active) : 'never') },
      ], {
        onRowClick: (agent) => showAgent(agent.id),
        defaultSort: { key: 'runs', dir: 'desc' },
      })));

  const draw = () => {
    if (view === 'departments') drawDepartments();
    else if (view === 'chart') drawChart();
    else drawTable();
  };

  const switcher = el('div', { class: 'segmented', role: 'group', 'aria-label': 'View' },
    ...[['departments', 'Departments'], ['chart', 'Org chart'], ['table', 'All agents']]
      .map(([id, label]) => el('button', {
        type: 'button', 'aria-pressed': String(view === id),
        onclick: (event) => {
          view = id;
          Array.from(event.currentTarget.parentElement.children)
            .forEach((b) => b.setAttribute('aria-pressed', String(b === event.currentTarget)));
          draw();
        },
      }, label)));

  const root = el('div', { class: 'stack' },
    el('div', { class: 'grid k4' },
      MetricCard({ label: 'Registered agents', value: number(totals.agents),
        foot: 'across the full organisation' }),
      MetricCard({ label: 'On the execution path', value: number(totals.executing),
        foot: 'execute real work in the graph' }),
      MetricCard({ label: 'Departments', value: number(totals.departments) }),
      MetricCard({ label: 'Working now', value: number(totals.working_now),
        foot: engaged.length ? `${engaged.length} agents have executed` : 'No activity yet',
        accent: totals.working_now > 0 })),
    Banner('info', 'How to read this organisation',
      'Agents marked "on the execution path" run inside the workflow today. The remaining roles '
      + 'are registered, permissioned and governed by the same authorisation layer so they can be '
      + 'brought onto the path without re-architecting anything — they are not simulated activity.'),
    el('div', { class: 'row' }, switcher),
    viewSlot);

  draw();

  const requested = params.get('agent');
  if (requested) window.setTimeout(() => showAgent(requested), 80);

  return root;
});
