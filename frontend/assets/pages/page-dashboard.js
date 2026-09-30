/* Executive dashboard. Every figure is read from the platform; nothing is
   seeded, estimated or held over from a previous render. */

import { el, icon, clear } from '../lib/dom.js';
import { duration, number, relative, titleise, when, truncate } from '../lib/format.js';
import { api } from '../api/index.js';
import { stackedBars } from '../lib/charts.js';
import {
  page, Card, MetricCard, StatusBadge, Badge, Button, EmptyState, Banner,
  DataTable, Feed, AgentAvatar, ProgressBar,
} from '../components/index.js';

function engineBanner(engine) {
  if (!engine) return null;
  if (engine.active_engine === 'local') {
    const reason = engine.forced_local
      ? 'FORCE_LOCAL_ENGINE is set, so the platform is deliberately running without a model provider.'
      : `No ${String(engine.provider || 'provider').toUpperCase()}_API_KEY is configured, so agents `
        + 'are executing on the local deterministic engine.';
    return Banner('warn', 'Running on the local development engine',
      `${reason} Orchestration, review, governance and persistence are fully live; agent content `
      + 'is deterministic and is labelled as such on every deliverable.',
      { action: Button('Settings', { size: 'sm', href: 'settings.html' }) });
  }
  if (engine.last_engine_used === 'local') {
    return Banner('error', `${titleise(engine.provider)} is configured but could not be reached`,
      el('span', {}, 'Agent steps have degraded to the local deterministic engine. ',
        engine.client_error ? el('span', { class: 'muted' }, `Last error: ${engine.client_error}`) : null),
      { action: Button('Settings', { size: 'sm', href: 'settings.html' }) });
  }
  return null;
}

function ActiveWorkforce(data) {
  const running = (data.recent_workflows || []).filter(
    (w) => w.status === 'running' || w.status === 'queued');
  const working = (data.agents || []).filter((a) => a.status === 'working');

  if (!running.length && !working.length) {
    return EmptyState('The workforce is idle',
      'No engagement is executing right now. Submit an objective and agents pick it up '
      + 'immediately.',
      { action: Button('New workflow', { variant: 'primary', href: 'new-workflow.html' }),
        iconName: 'agents' });
  }

  return el('div', { class: 'stack' },
    ...running.map((workflow) => el('div', {
      class: 'card inset', style: 'padding:var(--s4)',
    },
      el('div', { class: 'spread top' },
        el('div', { class: 'grow' },
          el('a', { href: `workflow.html?id=${encodeURIComponent(workflow.request_id)}`,
            class: 'strong' }, truncate(workflow.user_request, 96)),
          el('div', { class: 'small muted mt1' },
            `${workflow.request_id} · started ${relative(workflow.created_at)}`)),
        StatusBadge(workflow.status)),
      el('div', { class: 'row mt3' },
        el('span', { class: 'small muted' }, 'Current stage'),
        Badge(workflow.current_stage ? titleise(workflow.current_stage) : 'starting…',
          { variant: 'brand', dot: true })))),
    working.length
      ? el('div', {},
          el('div', { class: 'section-title mt2' }, 'Agents executing now'),
          el('div', { class: 'stack tight' },
            ...working.map((agent) => el('div', { class: 'row' },
              AgentAvatar(agent),
              el('div', { class: 'grow' },
                el('div', { class: 'strong small' }, agent.name),
                el('div', { class: 'xs muted' }, titleise(agent.department))),
              StatusBadge('working', { label: 'Working' })))))
      : null);
}

function PipelineOverview(stages, stagePerformance) {
  const byNode = new Map((stagePerformance || []).map((row) => [row.node, row]));
  return el('div', { class: 'stack tight' },
    ...stages.map((stage, index) => {
      const performance = byNode.get(stage.id);
      return el('div', {
        class: 'row', style: 'gap:var(--s3);padding:var(--s1) 0',
      },
        el('span', {
          class: 'badge',
          style: stage.parallel_group
            ? 'border-style:dashed;border-color:var(--accent-line);color:var(--accent)' : '',
        }, String(index + 1)),
        el('span', { class: 'grow small strong' }, stage.label),
        stage.parallel_group ? Badge('parallel', { variant: 'accent' }) : null,
        el('span', { class: 'xs muted nowrap' },
          performance ? `${duration(performance.avg_duration_ms)} avg` : 'not yet run'));
    }));
}

page({
  title: 'Dashboard',
  breadcrumb: [{ label: 'Platform' }, { label: 'Dashboard' }],
  active: 'dashboard',
  loadingLabel: 'Reading platform state…',
  actions: [Button('New workflow', { variant: 'primary', iconName: 'plus',
    href: 'new-workflow.html' })],
}, async () => {
  const [data, metrics, platformConfig] = await Promise.all([
    api.platform.dashboard(),
    api.metrics.report(),
    api.platform.configuration(),
  ]);

  const summary = data.summary;
  const research = data.research || {};
  const engaged = (metrics.agents || []).filter((a) => a.runs > 0);
  const reliability = engaged.length
    ? Math.round(engaged.reduce((sum, a) => sum + (a.reliability || 0), 0) / engaged.length)
    : null;
  const tokens = (summary.input_tokens || 0) + (summary.output_tokens || 0);

  const root = el('div', { class: 'stack' });
  const banner = engineBanner(data.engine);
  if (banner) root.append(banner);

  root.append(el('div', { class: 'grid k4' },
    MetricCard({
      label: 'Active workflows', value: number(summary.active_workflows),
      foot: data.active_workflows
        ? `${data.active_workflows} executing right now` : 'Nothing in flight',
      accent: Boolean(summary.active_workflows),
    }),
    MetricCard({
      label: 'Completed', value: number(summary.completed_workflows),
      foot: summary.total_workflows_finished
        ? `${summary.success_rate}% success rate` : 'No engagements finished yet',
      meter: summary.total_workflows_finished
        ? { value: summary.success_rate, variant: 'good' } : null,
    }),
    MetricCard({
      label: 'Approved first pass',
      value: summary.reviewed_workflows ? summary.review_pass_first_time_rate : null,
      unit: summary.reviewed_workflows ? '%' : '',
      foot: summary.reviewed_workflows
        ? `${summary.workflows_with_rework} of ${summary.reviewed_workflows} needed rework`
        : 'No engagement has been reviewed yet',
      meter: summary.reviewed_workflows
        ? { value: summary.review_pass_first_time_rate, variant: 'good' } : null,
    }),
    MetricCard({
      label: 'Research confidence',
      value: research.has_data ? research.avg_confidence : null,
      unit: research.has_data ? '/100' : '',
      foot: research.has_data
        ? `${research.question_completion_rate}% of research questions answered`
        : 'No research dossiers recorded yet',
      meter: research.has_data ? { value: research.avg_confidence } : null,
    })));

  root.append(el('div', { class: 'grid k4' },
    MetricCard({
      label: 'Agent reliability', value: reliability === null ? null : reliability,
      unit: reliability === null ? '' : '%',
      foot: engaged.length ? `across ${engaged.length} agents that have executed`
                           : 'No agent has executed yet',
    }),
    MetricCard({
      label: 'Average execution time',
      value: summary.avg_duration_ms ? duration(summary.avg_duration_ms) : null,
      foot: summary.completed_workflows
        ? `across ${summary.completed_workflows} completed engagements` : 'Awaiting data',
    }),
    MetricCard({
      label: 'Agent executions', value: number(summary.agent_runs),
      foot: summary.agent_runs ? `${summary.agent_run_failure_rate}% failed` : 'No activity yet',
    }),
    MetricCard({
      label: 'Model tokens', value: number(tokens),
      foot: tokens
        ? `${number(summary.input_tokens)} in · ${number(summary.output_tokens)} out`
        : (data.engine && data.engine.active_engine === 'local'
            ? 'No provider calls on the local engine'
            : 'Metered per agent execution'),
      hint: 'Cost is not shown: the platform is not configured with provider pricing.',
    })));

  const chart = el('div');
  root.append(el('div', { class: 'grid split' },
    Card({ title: 'Throughput', hint: 'engagements per day, by outcome' }, chart),
    Card({ title: 'Active workforce', hint: `${data.agents_engaged} of ${data.agents_total} engaged` },
      ActiveWorkforce(data))));

  stackedBars(chart, data.throughput, [
    { key: 'completed', label: 'Completed', color: 'var(--good)' },
    { key: 'failed', label: 'Failed', color: 'var(--critical)' },
  ], { xKey: 'day', emptyMessage: 'No engagements yet',
       emptyDetail: 'Throughput appears once the workforce has run.' });

  root.append(el('div', { class: 'grid split' },
    Card({ title: 'Recent engagements',
           actions: Button('View all', { size: 'sm', variant: 'ghost', href: 'workflows.html' }) },
      DataTable(data.recent_workflows || [], [
        { key: 'user_request', label: 'Objective',
          render: (row) => el('div', {},
            el('span', { class: 'primary-cell' }, truncate(row.user_request, 78)),
            el('span', { class: 'sub' }, `${row.request_id} · ${when(row.created_at)}`)) },
        { key: 'status', label: 'Status', render: (row) => StatusBadge(row.status) },
        { key: 'rework_count', label: 'Rework', align: 'right',
          render: (row) => String(row.rework_count || 0) },
        { key: 'duration_ms', label: 'Duration', align: 'right',
          render: (row) => duration(row.duration_ms) },
      ], {
        onRowClick: (row) => {
          window.location.href = `workflow.html?id=${encodeURIComponent(row.request_id)}`;
        },
        empty: EmptyState('No engagements yet',
          'The workforce has not been given an objective on this platform.',
          { action: Button('Submit the first objective',
              { variant: 'primary', href: 'new-workflow.html' }) }),
      })),
    Card({ title: 'Organisation activity', hint: 'live feed' },
      Feed(data.recent_events || [], { emptyMessage: 'No activity recorded yet.' }))));

  root.append(el('div', { class: 'grid split' },
    Card({ title: 'Execution pipeline',
           subtitle: 'The stages every engagement passes through, with the mean time each has '
                     + 'taken on this platform.' },
      PipelineOverview(platformConfig.stages, metrics.stages)),
    Card({ title: 'Department load', hint: 'agent executions' },
      (data.departments || []).length
        ? el('div', { class: 'stack tight' },
            ...data.departments.map((department) => {
              const top = Math.max(...data.departments.map((d) => d.runs), 1);
              return el('div', {},
                el('div', { class: 'spread' },
                  el('span', { class: 'small' }, titleise(department.department)),
                  el('span', { class: 'small muted tabular' }, number(department.runs))),
                el('div', { class: 'meter' },
                  el('span', { style: `width:${(department.runs / top) * 100}%` })));
            }))
        : EmptyState('No department activity', 'Agents have not executed yet.'))));

  return root;
});
