/* ==========================================================================
   Research workspace.
   Renders the structured dossier the Research & Intelligence department
   produced: the questions the engagement turns on, the findings against them,
   the evidence each rests on, what could not be established, and how the
   position changed when review sent it back for more.
   ========================================================================== */

import { el, icon, clear } from '../lib/dom.js';
import {
  titleise, when, relative, truncate, number, confidenceTone, statusLabel, duration,
} from '../lib/format.js';
import { api } from '../api/index.js';
import { gauge, lineSeries } from '../lib/charts.js';
import {
  page, Card, Button, Badge, StatusBadge, ConfidenceBadge, EmptyState, Banner,
  DataTable, Timeline, MetricCard, Prose, Disclosure, KeyValue, Tabs,
} from '../components/index.js';

const params = new URLSearchParams(window.location.search);
const requestId = (params.get('id') || '').trim();

const PRIORITY_TONE = { critical: 'critical', high: 'serious', medium: 'info', low: '' };

/* ------------------------------------------------------------------ index */
async function renderIndex() {
  const data = await api.research.index(100);
  const stats = data.stats || {};
  const items = data.items || [];

  const root = el('div', { class: 'stack' });

  root.append(el('div', { class: 'grid k4' },
    MetricCard({
      label: 'Engagements researched', value: number(stats.engagements_researched),
      foot: stats.dossiers ? `${stats.dossiers} dossiers recorded` : 'No research yet',
    }),
    MetricCard({
      label: 'Mean confidence', value: stats.has_data ? stats.avg_confidence : null,
      unit: stats.has_data ? '/100' : '',
      meter: stats.has_data ? { value: stats.avg_confidence } : null,
      foot: 'Coverage of the question set, not a claim about truth',
    }),
    MetricCard({
      label: 'Questions answered',
      value: stats.questions ? `${stats.questions_answered}/${stats.questions}` : null,
      foot: stats.questions ? `${stats.question_completion_rate}% completion` : 'Awaiting research',
    }),
    MetricCard({
      label: 'Open evidence gaps', value: number(stats.open_gaps),
      foot: stats.engagements_researched
        ? `${stats.gaps_per_engagement} per engagement` : 'Recorded, never hidden',
    })));

  root.append(Banner('neutral', 'How this platform sources evidence', data.disclosure,
    { iconName: 'shield' }));

  root.append(Card({
    title: 'Research by engagement',
    hint: `${items.length} engagement${items.length === 1 ? '' : 's'}`,
    actions: items.length
      ? Button('Open the most recent', {
          size: 'sm', variant: 'primary',
          href: `research.html?id=${encodeURIComponent(items[0].request_id)}`,
        })
      : null,
  },
    DataTable(items, [
      { key: 'user_request', label: 'Objective',
        render: (row) => el('div', {},
          el('span', { class: 'primary-cell' }, truncate(row.user_request || row.request_id, 84)),
          el('span', { class: 'sub' }, `${row.request_id} · ${when(row.created_at)}`)) },
      { key: 'confidence', label: 'Confidence', align: 'right', sortable: true,
        render: (row) => ConfidenceBadge(row.confidence, { label: '' }) },
      { key: 'questions', label: 'Questions', align: 'right', sortable: true,
        render: (row) => `${row.answered}/${row.questions}` },
      { key: 'findings', label: 'Findings', align: 'right', sortable: true },
      { key: 'gaps', label: 'Gaps', align: 'right', sortable: true },
      { key: 'attempts', label: 'Rounds', align: 'right',
        render: (row) => (row.attempts > 1
          ? Badge(`${row.attempts} · follow-up`, { variant: 'serious' })
          : String(row.attempts)) },
      { key: 'actions', label: '', width: '110px',
        render: (row) => Button('Open', {
          size: 'sm', href: `research.html?id=${encodeURIComponent(row.request_id)}`,
        }) },
    ], {
      defaultSort: { key: 'created_at', dir: 'desc' },
      onRowClick: (row) => {
        window.location.href = `research.html?id=${encodeURIComponent(row.request_id)}`;
      },
      empty: EmptyState('No research recorded yet',
        'The Research & Intelligence department produces a dossier on every engagement. '
        + 'Submit an objective to see one.',
        { action: Button('New workflow', { variant: 'primary', href: 'new-workflow.html' }),
          iconName: 'research' }),
    })));

  if ((stats.confidence_series || []).length > 1) {
    const chart = el('div');
    root.append(Card({ title: 'Research confidence over time',
                       hint: 'most recent dossier per engagement' }, chart));
    lineSeries(chart, stats.confidence_series.map((point) => ({
      label: truncate(point.objective || point.request_id, 40),
      value: point.confidence,
    })), { valueLabel: 'Confidence', max: 100, band: { from: 80, label: 'strong evidence base 80+' } });
  }

  return root;
}

/* -------------------------------------------------------------- workspace */
function QuestionCard(question) {
  return el('div', { class: 'card inset', style: 'padding:var(--s4)' },
    el('div', { class: 'spread top' },
      el('div', { class: 'grow' },
        el('div', { class: 'row tight' },
          el('span', { class: 'mono xs muted' }, question.id),
          Badge(titleise(question.priority),
            { variant: PRIORITY_TONE[question.priority] || '', dot: true })),
        el('div', { class: 'strong mt2' }, question.question),
        question.rationale
          ? el('div', { class: 'small muted mt2' }, `Why it matters: ${question.rationale}`)
          : null),
      el('div', { class: 'right' },
        StatusBadge(question.status),
        el('div', { class: 'xs muted mt2' },
          `${question.finding_count} finding${question.finding_count === 1 ? '' : 's'}`),
        question.confidence
          ? el('div', { class: 'xs muted' }, `${question.confidence}% mean confidence`) : null)));
}

function FindingRow(finding, questionsById) {
  const question = questionsById[finding.question_id];
  return el('div', { class: 'card inset', style: 'padding:var(--s4)' },
    el('div', { class: 'spread top' },
      el('div', { class: 'grow' },
        el('div', { class: 'row tight' },
          el('span', { class: 'mono xs muted' }, finding.id),
          Badge(finding.basis_label, {
            variant: finding.reliability === 'high' ? 'good'
              : finding.reliability === 'low' ? 'warning' : 'info',
          }),
          Badge(`${titleise(finding.relevance)} relevance`)),
        el('div', { class: 'strong mt2' }, finding.finding),
        finding.evidence
          ? el('div', { class: 'small muted mt2' }, el('strong', {}, 'Evidence: '), finding.evidence)
          : null,
        question
          ? el('div', { class: 'xs muted mt2' },
              `Answers ${finding.question_id}: ${truncate(question.question, 90)}`)
          : el('div', { class: 'xs muted mt2' }, 'Not linked to a stated question')),
      el('div', { class: 'right nowrap' },
        ConfidenceBadge(finding.confidence, { label: '' }))));
}

async function renderWorkspace(id) {
  const workspace = await api.research.workspace(id);

  const root = el('div', { class: 'stack' });

  root.append(Card({
    actions: el('div', { class: 'row tight' },
      Button('Execution view', { size: 'sm', variant: 'ghost', iconName: 'workflows',
        href: `workflow.html?id=${encodeURIComponent(id)}` }),
      Button('All research', { size: 'sm', variant: 'ghost', href: 'research.html' })),
  },
    el('div', { class: 'spread top' },
      el('div', { class: 'grow' },
        el('div', { class: 'section-title' }, 'Engagement objective'),
        el('h2', { style: 'font-size:var(--text-lg);line-height:1.45' }, workspace.objective),
        el('div', { class: 'row tight mt3 small muted' },
          el('span', { class: 'mono' }, workspace.request_id),
          el('span', {}, '·'),
          el('span', {}, `Research stage: ${statusLabel(workspace.stage_status)}`))),
      StatusBadge(workspace.workflow_status, { size: 'lg' }))));

  if (!workspace.available) {
    root.append(EmptyState('No research dossier yet', workspace.reason,
      { iconName: 'research',
        action: Button('Open the execution view', {
          variant: 'primary', href: `workflow.html?id=${encodeURIComponent(id)}` }) }));
    if ((workspace.timeline || []).length) {
      root.append(Card({ title: 'Research timeline' }, Timeline(workspace.timeline)));
    }
    return root;
  }

  const current = workspace.current;
  const coverage = current.coverage || {};
  const questionsById = Object.fromEntries((current.questions || []).map((q) => [q.id, q]));

  /* ---- overview ---------------------------------------------------- */
  const gaugeSlot = el('div');
  root.append(el('div', { class: 'grid split' },
    Card({ title: 'Research overview', hint: `Round ${current.attempt} · ${titleise(current.trigger)}` },
      el('div', { class: 'row', style: 'gap:var(--s6);align-items:flex-start' },
        el('div', {}, gaugeSlot,
          el('div', { class: 'xs muted center', style: 'max-width:132px' }, 'Confidence')),
        el('div', { class: 'grow' },
          KeyValue([
            ['Questions', `${coverage.questions} framed · ${coverage.answered} answered · `
              + `${coverage.partial} partial · ${coverage.unanswered} unanswered`],
            ['Findings', `${coverage.findings} recorded, ${coverage.high_relevance} of high relevance`],
            ['Open gaps', String(coverage.gaps)],
            ['Evidence bases', String((workspace.basis.entries || []).length)],
            ['Produced by', current.engine ? titleise(current.engine) : '—'],
            ['Recorded', when(current.created_at)],
          ]))),
      current.summary
        ? el('p', { class: 'mt5 mb0' }, current.summary) : null),
    Card({ title: 'Coverage' },
      el('div', { class: 'stack tight' },
        ...(current.questions || []).map((question) => el('div', {},
          el('div', { class: 'spread' },
            el('span', { class: 'small truncate', style: 'max-width:70%' },
              `${question.id} · ${truncate(question.question, 52)}`),
            el('span', { class: 'xs muted' }, statusLabel(question.status))),
          el('div', { class: `meter ${question.status === 'answered' ? 'good'
            : question.status === 'partial' ? 'warning' : 'critical'}` },
            el('span', {
              style: `width:${question.status === 'answered' ? 100
                : question.status === 'partial' ? 55 : 6}%`,
            })))))) ));

  gauge(gaugeSlot, current.confidence, { label: 'Research confidence' });

  root.append(Banner('neutral', 'Evidence basis for this engagement',
    workspace.basis.disclosure, { iconName: 'shield' }));

  /* ---- questions ---------------------------------------------------- */
  root.append(Card({
    title: 'Research questions',
    hint: `${coverage.questions} question${coverage.questions === 1 ? '' : 's'}`,
    subtitle: 'The department decides what the engagement turns on before it looks for answers. '
      + 'A question counts as answered only when findings are recorded against it.',
  },
    (current.questions || []).length
      ? el('div', { class: 'stack tight' }, ...current.questions.map(QuestionCard))
      : EmptyState('No questions recorded', 'The dossier contains no question set.')));

  /* ---- findings ----------------------------------------------------- */
  root.append(Card({
    title: 'Findings',
    hint: `${coverage.findings} recorded`,
    subtitle: 'Every finding carries the evidence behind it, the basis it rests on, and how '
      + 'confident the department is in it.',
  },
    (current.findings || []).length
      ? el('div', { class: 'stack tight' },
          ...current.findings.map((finding) => FindingRow(finding, questionsById)))
      : EmptyState('No findings recorded', 'The dossier established nothing.')));

  /* ---- sources / basis ledger ---------------------------------------- */
  root.append(Card({
    title: 'Sources & evidence basis',
    hint: `${workspace.basis.entries.length} bases`,
    subtitle: 'What each finding actually rests on. Entries the platform genuinely retrieved — '
      + 'its own record of previous engagements — carry their real timestamp and are linked.',
  },
    DataTable(workspace.basis.entries, [
      { key: 'source', label: 'Source',
        render: (row) => el('div', {},
          el('span', { class: 'primary-cell' }, truncate(row.source, 76)),
          el('span', { class: 'sub' }, truncate(row.description || '', 150)),
          row.request_id
            ? el('a', { class: 'xs',
                href: `workflow.html?id=${encodeURIComponent(row.request_id)}` },
                `engagement ${row.request_id}`)
            : null) },
      { key: 'type_label', label: 'Type',
        render: (row) => Badge(row.type_label, {
          variant: row.type === 'organisational_memory' ? 'brand' : '',
        }) },
      { key: 'timestamp', label: 'Timestamp',
        render: (row) => el('span', { class: 'small muted nowrap' },
          row.timestamp ? when(row.timestamp) : '—') },
      { key: 'reliability', label: 'Reliability',
        render: (row) => Badge(titleise(row.reliability), {
          variant: row.reliability === 'high' ? 'good'
            : row.reliability === 'low' ? 'warning'
            : row.reliability === 'recorded' ? 'brand' : 'info',
          dot: true,
        }) },
      { key: 'retrieved', label: 'Retrieved',
        render: (row) => (row.retrieved
          ? Badge('Yes — from this platform', { variant: 'good' })
          : el('span', { class: 'small muted' }, 'No external retrieval')) },
      { key: 'findings', label: 'Findings used', align: 'right',
        render: (row) => (row.findings
          ? el('span', { class: 'strong', title: row.finding_ids.join(', ') },
              String(row.findings))
          : el('span', { class: 'xs muted' },
              row.retrieved ? 'recalled, not cited' : '—')) },
    ], { empty: EmptyState('No bases recorded', 'No findings declared an evidence basis.') })));

  /* ---- gaps ---------------------------------------------------------- */
  root.append(Card({
    title: 'Research gaps',
    hint: `${(current.gaps || []).length} open`,
    subtitle: 'What the department could not establish, and what it would take to close it. '
      + 'Gaps travel with the recommendation instead of disappearing from it.',
  },
    (current.gaps || []).length
      ? el('div', { class: 'stack tight' },
          ...current.gaps.map((gap) => el('div', { class: 'card inset', style: 'padding:var(--s4)' },
            el('div', { class: 'row tight' },
              icon('alert', { size: 15 }), el('span', { class: 'strong' }, gap.gap)),
            gap.impact
              ? el('div', { class: 'small mt2' }, el('strong', {}, 'Exposure: '), gap.impact) : null,
            gap.how_to_close
              ? el('div', { class: 'small muted mt1' },
                  el('strong', {}, 'To close: '), gap.how_to_close) : null)))
      : el('div', { class: 'banner good', style: 'background:var(--good-soft);border-color:var(--good-soft)' },
          icon('check', { size: 18, cls: 'banner-icon' }),
          el('div', {}, el('strong', {}, 'No open gaps'),
            el('div', {}, 'The department recorded no unanswered exposure against its question set.')))));

  /* ---- follow-up ----------------------------------------------------- */
  root.append(Card({
    title: 'Follow-up research',
    hint: `${workspace.followups.length} round${workspace.followups.length === 1 ? '' : 's'}`,
    subtitle: 'Quality review can return an engagement to Research when the evidence does not '
      + 'support the recommendation. When that happens, this is why.',
  },
    workspace.followups.length
      ? el('div', { class: 'stack tight' },
          ...workspace.followups.map((followup) => el('div', {
            class: 'card inset', style: 'padding:var(--s4)',
          },
            el('div', { class: 'spread' },
              el('strong', {}, `Round ${followup.attempt}`),
              el('div', { class: 'row tight' },
                followup.confidence_before !== null && followup.confidence_before !== undefined
                  ? Badge(`${followup.confidence_before} → ${followup.confidence_after}`,
                      { variant: 'good' })
                  : Badge(`${followup.confidence_after}/100`, { variant: 'brand' }),
                el('span', { class: 'xs muted' }, when(followup.at)))),
            followup.reason ? el('p', { class: 'mt3 mb0' }, followup.reason) : null,
            (followup.findings || []).length
              ? el('div', { class: 'mt3' },
                  el('div', { class: 'section-title' }, 'Reviewer findings that triggered it'),
                  el('ul', {}, ...followup.findings.map((f) => el('li', { class: 'small' }, f))))
              : null)))
      : el('p', { class: 'muted mb0' },
          'Quality review accepted the evidence base without asking for more. '
          + 'No follow-up round was triggered.')));

  /* ---- attempts + timeline -------------------------------------------- */
  const roundCard = (round) => el('div', {
    class: 'card inset', style: 'padding:var(--s3) var(--s4)',
  },
    el('div', { class: 'spread' },
      el('span', { class: 'strong small' }, `Round ${round.attempt}`),
      Badge(statusLabel(round.trigger),
        { variant: round.trigger === 'initial' ? '' : 'serious' })),
    el('div', { class: 'row tight mt2' },
      ConfidenceBadge(round.confidence, { label: '' }),
      el('span', { class: 'xs muted' },
        `${round.answered}/${round.questions} answered · ${round.findings} findings · `
        + `${round.gaps} gaps`)),
    el('div', { class: 'xs muted mt1' }, when(round.created_at)));

  const roundsBody = el('div', { class: 'stack tight' },
    ...workspace.attempts.map(roundCard));

  root.append(el('div', { class: 'grid split' },
    Card({ title: 'Research timeline' }, Timeline(workspace.timeline)),
    Card({ title: 'Rounds recorded', hint: String(workspace.attempts.length) }, roundsBody)));

  root.append(Disclosure('Evidence base as narrative',
    Prose(workspace.narrative || 'No narrative recorded.')));

  return root;
}

page({
  title: requestId ? 'Research workspace' : 'Research',
  breadcrumb: requestId
    ? [{ label: 'Research', href: 'research.html' }, { label: requestId }]
    : [{ label: 'Platform' }, { label: 'Research' }],
  active: 'research',
  loadingLabel: 'Loading research…',
}, async () => (requestId ? renderWorkspace(requestId) : renderIndex()));
