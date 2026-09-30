/* Analytics. Every figure is computed by the backend from recorded execution. */

import { el, clear } from '../lib/dom.js';
import { duration, titleise, relative, number, truncate } from '../lib/format.js';
import { api } from '../api/index.js';
import { stackedBars, horizontalBars, lineSeries, gauge } from '../lib/charts.js';
import {
  page, Card, Button, Badge, MetricCard, EmptyState, Banner, DataTable,
} from '../components/index.js';

page({
  title: 'Metrics',
  breadcrumb: [{ label: 'Platform' }, { label: 'Metrics' }],
  active: 'metrics',
  loadingLabel: 'Computing metrics…',
}, async () => {
  const metrics = await api.metrics.report();
  const summary = metrics.summary;
  const research = metrics.research || {};
  const engaged = (metrics.agents || []).filter((a) => a.runs > 0);

  const root = el('div', { class: 'stack' });

  if (!summary.has_data) {
    root.append(Banner('info', 'No execution data yet',
      'Every figure on this screen is computed from workflow records and agent runs the platform '
      + 'actually wrote. Run an engagement and the charts populate.',
      { action: Button('New workflow', { size: 'sm', variant: 'primary',
        href: 'new-workflow.html' }) }));
  }

  root.append(el('div', { class: 'grid k4' },
    MetricCard({ label: 'Total workflows', value: number(summary.total_workflows) }),
    MetricCard({ label: 'Completed', value: number(summary.completed_workflows),
      foot: `${summary.success_rate}% success rate`,
      meter: summary.total_workflows_finished ? { value: summary.success_rate, variant: 'good' } : null }),
    MetricCard({ label: 'Failed', value: number(summary.failed_workflows),
      foot: `${summary.failure_rate}% failure rate`,
      meter: summary.failed_workflows ? { value: summary.failure_rate, variant: 'critical' } : null }),
    MetricCard({ label: 'Active', value: number(summary.active_workflows) })));

  root.append(el('div', { class: 'grid k4' },
    MetricCard({ label: 'Average execution time',
      value: summary.avg_duration_ms ? duration(summary.avg_duration_ms) : null,
      foot: `across ${summary.completed_workflows} completed` }),
    MetricCard({ label: 'First-pass approval',
      value: summary.reviewed_workflows ? summary.review_pass_first_time_rate : null,
      unit: summary.reviewed_workflows ? '%' : '',
      foot: `${summary.workflows_with_rework} of ${summary.reviewed_workflows} needed rework` }),
    MetricCard({ label: 'Rework rate',
      value: summary.reviewed_workflows ? summary.rework_rate : null,
      unit: summary.reviewed_workflows ? '%' : '',
      foot: `${summary.total_reworks} total rework cycles` })));

  root.append(el('div', { class: 'grid k4' },
    MetricCard({ label: 'Agent executions', value: number(summary.agent_runs),
      foot: `${summary.failed_agent_runs} failed` }),
    MetricCard({ label: 'Agent failure rate', value: summary.agent_run_failure_rate, unit: '%' }),
    MetricCard({ label: 'Model tokens in', value: number(summary.input_tokens) }),
    MetricCard({ label: 'Model tokens out', value: number(summary.output_tokens),
      hint: 'Cost is not shown because the platform is not configured with provider pricing.' })));

  const throughputSlot = el('div');
  root.append(Card({ title: 'Workflow throughput', hint: 'engagements per day, by outcome' }, throughputSlot));
  stackedBars(throughputSlot, metrics.throughput, [
    { key: 'completed', label: 'Completed', color: 'var(--good)' },
    { key: 'failed', label: 'Failed', color: 'var(--critical)' },
  ], { xKey: 'day' });

  /* ---------------------------------------------------------- research ---- */
  const confidenceSlot = el('div');
  const gaugeSlot = el('div');
  root.append(Card({
    title: 'Research intelligence',
    hint: research.has_data ? `${research.dossiers} dossiers` : 'no dossiers yet',
    actions: Button('Research workspace', { size: 'sm', variant: 'ghost', href: 'research.html' }),
  },
    research.has_data
      ? el('div', {},
          el('div', { class: 'grid k4' },
            MetricCard({ label: 'Mean confidence', value: research.avg_confidence, unit: '/100',
              meter: { value: research.avg_confidence } }),
            MetricCard({ label: 'Question completion', value: research.question_completion_rate,
              unit: '%', foot: `${research.questions_answered} of ${research.questions} answered` }),
            MetricCard({ label: 'Findings recorded', value: number(research.findings),
              foot: `${research.gaps_per_engagement} gaps per engagement` }),
            MetricCard({ label: 'Follow-up rate', value: research.followup_rate, unit: '%',
              foot: `${research.followup_rounds} extra rounds triggered by review` })),
          el('div', { class: 'grid split mt5' },
            el('div', {},
              el('div', { class: 'section-title' }, 'Confidence per engagement'),
              confidenceSlot),
            el('div', {},
              el('div', { class: 'section-title' }, 'Mean research confidence'),
              gaugeSlot)))
      : EmptyState('No research recorded yet',
          'The Research & Intelligence department produces a structured dossier on every '
          + 'engagement; its confidence and coverage are measured here.',
          { iconName: 'research' })));

  if (research.has_data) {
    lineSeries(confidenceSlot, (research.confidence_series || []).map((point) => ({
      label: truncate(point.objective || point.request_id, 40),
      value: point.confidence,
    })), { valueLabel: 'Confidence', max: 100,
           band: { from: 80, label: 'strong evidence base 80+' },
           emptyMessage: 'Not enough dossiers to plot' });
    gauge(gaugeSlot, research.avg_confidence, { label: 'Mean research confidence' });
  }

  /* ------------------------------------------------------------- stages -- */
  const stageSlot = el('div');
  const deptSlot = el('div');
  root.append(el('div', { class: 'grid split-even' },
    Card({ title: 'Stage duration', hint: 'mean, per stage' }, stageSlot),
    Card({ title: 'Department load', hint: 'agent executions' }, deptSlot)));
  horizontalBars(stageSlot, (metrics.stages || []).map((row) => ({
    label: row.label || row.agent, value: row.avg_duration_ms,
    extra: [['Agent', row.agent], ['Runs', row.runs]],
  })), { valueLabel: 'Mean duration', format: duration,
         emptyMessage: 'No stage timings yet',
         emptyDetail: 'Timings appear once agents have executed.' });
  horizontalBars(deptSlot, (metrics.department_load || []).map((row) => ({
    label: titleise(row.department), value: row.runs,
  })), { valueLabel: 'Executions', format: (v) => String(v),
         emptyMessage: 'No department activity yet' });

  /* ------------------------------------------------------------- agents -- */
  const reliabilitySlot = el('div');
  root.append(Card({
    title: 'Agent reliability',
    hint: `${engaged.length} of ${(metrics.agents || []).length} agents have executed`,
  }, reliabilitySlot,
    engaged.length
      ? el('div', { class: 'mt5' },
          DataTable(engaged, [
            { key: 'name', label: 'Agent',
              render: (agent) => el('a', {
                href: `agents.html?agent=${encodeURIComponent(agent.agent_id)}`,
              }, agent.name) },
            { key: 'department', label: 'Department', render: (a) => titleise(a.department) },
            { key: 'runs', label: 'Runs', align: 'right', sortable: true,
              sortValue: (a) => a.runs },
            { key: 'completed', label: 'Completed', align: 'right' },
            { key: 'failed', label: 'Failed', align: 'right' },
            { key: 'avg_duration_ms', label: 'Mean time', align: 'right', sortable: true,
              sortValue: (a) => a.avg_duration_ms, render: (a) => duration(a.avg_duration_ms) },
            { key: 'reliability', label: 'Reliability', align: 'right', sortable: true,
              sortValue: (a) => a.reliability || 0,
              render: (a) => (a.reliability === null ? '—' : `${a.reliability}%`) },
            { key: 'last_active', label: 'Last active',
              render: (a) => el('span', { class: 'small muted nowrap' }, relative(a.last_active)) },
          ], { defaultSort: { key: 'runs', dir: 'desc' } }))
      : null));
  horizontalBars(reliabilitySlot, engaged.map((agent) => ({
    label: agent.name, value: agent.reliability === null ? 0 : agent.reliability,
    color: (agent.reliability || 0) >= 99 ? 'var(--good)'
      : (agent.reliability || 0) >= 90 ? 'var(--series-1)' : 'var(--warning)',
    extra: [['Runs', agent.runs], ['Failed', agent.failed]],
  })), { valueLabel: 'Reliability', format: (v) => `${v}%`,
         emptyMessage: 'No agent executions recorded',
         emptyDetail: 'Agent performance is derived from real execution records.' });

  return root;
});
