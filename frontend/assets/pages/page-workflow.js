/* ==========================================================================
   Live workflow execution.
   Everything on this page reflects backend state: the pipeline, the stage
   statuses, the agent activity and the outputs. Nothing is animated ahead of
   the platform.
   ========================================================================== */

import { el, icon, clear } from '../lib/dom.js';
import {
  duration, when, clock, titleise, elapsedSince, statusLabel, truncate,
} from '../lib/format.js';
import { api, ApiError } from '../api/index.js';
import {
  page, Card, Button, StatusBadge, Badge, ConfidenceBadge, EmptyState, ErrorState,
  Banner, DataTable, Feed, Pipeline, ProgressBar, Prose, Tabs, KeyValue,
  openDrawer, DrawerHeader, DrawerBody, toast, scrollFeedToEnd, Disclosure,
} from '../components/index.js';

const TERMINAL = new Set(['completed', 'failed', 'rejected']);
const params = new URLSearchParams(window.location.search);
const requestId = (params.get('id') || '').trim();

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'research', label: 'Research' },
  { id: 'requirements', label: 'Requirements' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'solution', label: 'Solution' },
  { id: 'review', label: 'Review & decision' },
  { id: 'deliverable', label: 'Deliverable' },
];

page({
  title: 'Workflow execution',
  breadcrumb: [
    { label: 'Workflows', href: 'workflows.html' },
    { label: requestId || 'No engagement' },
  ],
  active: 'workflows',
  loadingLabel: 'Loading engagement…',
  actions: [Button('All workflows', { variant: 'ghost', href: 'workflows.html' })],
}, async ({ content }) => {
  if (!requestId) {
    return EmptyState('No engagement selected',
      'Open a workflow from the history, or submit a new objective.',
      { action: Button('New workflow', { variant: 'primary', href: 'new-workflow.html' }) });
  }

  const platformConfig = await api.platform.configuration();
  const stageMeta = Object.fromEntries(platformConfig.stages.map((s) => [s.id, s]));

  // First status call decides whether the engagement exists at all; a 404 is
  // surfaced by the page bootstrapper as a clean not-found state.
  let status = await api.workflows.status(requestId, 0);

  const state = {
    events: status.events || [],
    lastEventId: (status.events || []).reduce((max, e) => Math.max(max, e.id), 0),
    result: null,
    detail: null,
    activeTab: 'overview',
    timer: null,
    ticker: null,
  };

  /* --------------------------------------------------------------- header */
  const headerSlot = el('div');
  const elapsedSlot = el('span', { class: 'small muted' });
  const bannerSlot = el('div');
  const pipelineSlot = el('div', { class: 'pipeline' });
  const feedSlot = el('div');
  const tasksSlot = el('div');
  const tabsSlot = el('div');
  const panelSlot = el('div', {
    id: 'panel-overview', role: 'tabpanel', 'aria-labelledby': 'tab-overview', tabindex: '0',
  });

  function renderHeader() {
    clear(headerSlot).append(
      el('div', { class: 'spread top' },
        el('div', { class: 'grow', style: 'min-width:0' },
          el('h2', { style: 'font-size:var(--text-lg);line-height:1.4' }, status.objective),
          el('div', { class: 'row tight mt3 small muted' },
            el('span', { class: 'mono' }, status.request_id),
            el('span', {}, '·'),
            el('span', {}, `Submitted ${when(status.created_at)}`),
            el('span', {}, '·'),
            el('span', {}, `Priority ${titleise(status.priority)}`),
            status.request_type ? el('span', {}, '·') : null,
            status.request_type ? el('span', {}, titleise(status.request_type)) : null,
            el('span', {}, '·'),
            elapsedSlot)),
        el('div', { class: 'row tight' },
          status.rework_count ? Badge(`${status.rework_count} rework`, { variant: 'serious' }) : null,
          status.engine ? Badge(titleise(status.engine)) : null,
          StatusBadge(status.status, { size: 'lg' }))),
      el('div', { class: 'mt4' }, ProgressBar(status.progress, { label: 'Engagement progress' })),
      el('div', { class: 'small muted mt2' },
        `${status.progress}% complete · current stage: `,
        el('span', { class: 'strong' },
          status.current_stage ? titleise(status.current_stage) : '—')));
  }

  function renderElapsed() {
    if (status.duration_ms) {
      elapsedSlot.textContent = `Ran for ${duration(status.duration_ms)}`;
      return;
    }
    const since = elapsedSince(status.started_at || status.created_at);
    elapsedSlot.textContent = since === null ? '' : `Elapsed ${duration(since)}`;
  }

  /* ------------------------------------------------------------- pipeline */
  async function openStage(stage) {
    if (!state.detail) {
      try { state.detail = await api.workflows.detail(requestId); } catch { state.detail = null; }
    }
    const runs = ((state.detail && state.detail.agent_runs) || [])
      .filter((run) => run.node === stage.stage_id
        || (stage.stage_id === 'research' && run.node === 'research_followup'));

    openDrawer(el('div', {},
      DrawerHeader(stage.label,
        `${titleise(stage.agent_id)} · ${titleise(stage.department)}`),
      DrawerBody(
        KeyValue([
          ['Status', StatusBadge(stage.status)],
          ['Attempt', String(stage.attempt || 0)],
          ['Started', stage.started_at ? when(stage.started_at) : '—'],
          ['Completed', stage.completed_at ? when(stage.completed_at) : '—'],
          ['Duration', stage.duration_ms ? duration(stage.duration_ms) : '—'],
          ['Detail', stage.detail || '—'],
        ]),
        runs.length
          ? el('div', {},
              el('div', { class: 'section-title mt5' },
                `Recorded output (${runs.length} execution${runs.length === 1 ? '' : 's'})`),
              ...runs.map((run) => Disclosure(
                `${titleise(run.node)} · ${statusLabel(run.status)} · ${duration(run.duration_ms)}`,
                run.error
                  ? Banner('error', 'This execution failed', run.error)
                  : (run.output
                      ? Prose(run.output)
                      : el('p', { class: 'muted' }, 'This agent recorded no textual output.')),
                { open: runs.length === 1 })))
          : el('p', { class: 'muted' },
              stage.status === 'pending'
                ? 'This stage has not run yet, so there is no output to show.'
                : 'No output was recorded for this stage.'),
        stage.stage_id === 'research' && status.progress > 0
          ? Button('Open the research workspace', {
              variant: 'primary', iconName: 'research',
              href: `research.html?id=${encodeURIComponent(requestId)}`,
            })
          : null)),
    { label: `${stage.label} details` });
  }

  function renderPipeline() {
    clear(pipelineSlot).append(...Pipeline(status.stages, {
      meta: stageMeta, onSelect: openStage,
    }).childNodes);
  }

  /* ----------------------------------------------------------------- feed */
  // Debug lines record every agent start and finish; hidden by default so the
  // meaningful hand-offs and decisions are readable.
  let feedLevel = 'key';
  function renderFeed() {
    const events = feedLevel === 'all'
      ? state.events
      : state.events.filter((event) => event.level !== 'debug');
    const feed = Feed(events, {
      emptyMessage: feedLevel === 'key'
        ? 'No decisions recorded yet.' : 'Waiting for the first event…',
    });
    clear(feedSlot).append(feed);
    scrollFeedToEnd(feed);
  }

  const feedFilter = el('div', { class: 'segmented', role: 'group',
    'aria-label': 'Activity detail' },
    ...[['key', 'Key events'], ['all', 'All activity']].map(([id, label]) => el('button', {
      type: 'button', 'aria-pressed': String(feedLevel === id),
      onclick: (event) => {
        feedLevel = id;
        Array.from(event.currentTarget.parentElement.children).forEach((button) =>
          button.setAttribute('aria-pressed', String(button === event.currentTarget)));
        renderFeed();
      },
    }, label)));

  /* ---------------------------------------------------------------- tasks */
  function renderTasks() {
    clear(tasksSlot).append(DataTable(status.tasks || [], [
      { key: 'title', label: 'Task',
        render: (task) => el('div', {},
          el('span', { class: 'primary-cell' }, task.title),
          task.acceptance ? el('span', { class: 'sub' }, `Acceptance: ${task.acceptance}`) : null) },
      { key: 'department', label: 'Department', render: (t) => titleise(t.department) },
      { key: 'agent_id', label: 'Assigned agent',
        render: (t) => (t.agent_id
          ? el('a', { href: `agents.html?agent=${encodeURIComponent(t.agent_id)}` },
              titleise(t.agent_id))
          : el('span', { class: 'muted' }, 'pending')) },
      { key: 'status', label: 'Status', render: (t) => StatusBadge(t.status || 'pending') },
    ], {
      empty: EmptyState('Not decomposed yet',
        'The AI Executive has not assigned agents to this objective yet.'),
    }));
  }

  /* ----------------------------------------------------------------- tabs */
  function renderTabs() {
    clear(tabsSlot).append(Tabs(TABS, {
      active: state.activeTab,
      ariaLabel: 'Engagement output',
      onSelect: (id) => { state.activeTab = id; renderTabs(); renderPanel(); },
    }));
  }

  function outputCard(title, agent, body, { emptyHint = '' } = {}) {
    return Card({ title, hint: agent },
      body ? Prose(body) : EmptyState('Not produced yet',
        emptyHint || 'This stage has not reported.', { iconName: 'clock' }));
  }

  function renderPanel() {
    panelSlot.setAttribute('id', `panel-${state.activeTab}`);
    panelSlot.setAttribute('aria-labelledby', `tab-${state.activeTab}`);
    const result = state.result;
    clear(panelSlot);

    if (!result) {
      panelSlot.append(Banner('info', 'Work in progress',
        'Departmental output appears here as each agent reports. The pipeline and the activity '
        + 'feed above are live.'));
      return;
    }

    if (state.activeTab === 'overview') {
      panelSlot.append(
        Card({ title: 'Agent assignments', hint: 'AI Executive' },
          DataTable(result.assignments || [], [
            { key: 'task_title', label: 'Task' },
            { key: 'capability', label: 'Capability', render: (a) => titleise(a.capability) },
            { key: 'agent_name', label: 'Agent',
              render: (a) => el('a', { href: `agents.html?agent=${encodeURIComponent(a.agent_id)}` },
                a.agent_name) },
            { key: 'reason', label: 'Why this agent',
              render: (a) => el('span', { class: 'small muted' }, a.reason) },
          ], { empty: EmptyState('No assignments recorded', 'The AI Executive had nothing to route.') })),
        (result.memory_context || []).length
          ? Card({ title: 'Organisational memory used',
                   hint: `${result.memory_context.length} entries recalled` },
              el('div', { class: 'stack tight' },
                ...result.memory_context.map((entry) => el('div', { class: 'card inset', style: 'padding:var(--s3)' },
                  el('div', { class: 'spread' },
                    el('span', { class: 'strong small' }, entry.title),
                    Badge(titleise(entry.kind))),
                  el('p', { class: 'small muted mt2 mb0' }, truncate(entry.content, 260)),
                  entry.request_id
                    ? el('a', { class: 'xs',
                        href: `workflow.html?id=${encodeURIComponent(entry.request_id)}` },
                        `From engagement ${entry.request_id}`)
                    : null))))
          : null);
      return;
    }

    if (state.activeTab === 'research') {
      panelSlot.append(
        Card({
          title: 'Research', hint: 'Research Agent',
          actions: Button('Open the research workspace', {
            size: 'sm', variant: 'primary', iconName: 'research',
            href: `research.html?id=${encodeURIComponent(requestId)}`,
          }),
        },
          result.research ? Prose(result.research)
            : EmptyState('No research recorded', 'This engagement produced no evidence base.')));
      return;
    }

    const simple = {
      requirements: ['Requirements', 'Requirements Agent', result.data_requirements],
      analysis: ['Analysis', 'Analysis Agent', result.analysis],
      solution: ['Recommended solution', 'Solution Agent', result.strategy],
    };
    if (simple[state.activeTab]) {
      const [title, agent, body] = simple[state.activeTab];
      panelSlot.append(outputCard(title, agent, body));
      if (state.activeTab === 'solution' && (result.solution_drafts || []).length > 1) {
        panelSlot.append(Card({
          title: 'Earlier drafts', hint: `${result.solution_drafts.length} attempts`,
        },
          el('div', { class: 'stack tight' },
            ...result.solution_drafts.slice(0, -1).map((draft) => Disclosure(
              `Draft ${draft.attempt} — returned for rework`, Prose(draft.text))))));
      }
      return;
    }

    if (state.activeTab === 'review') {
      const decision = result.executive_decision || {};
      panelSlot.append(
        Card({ title: 'Quality review',
               hint: `${(result.review_history || []).length} pass(es) · Review/Critic Agent` },
          (result.review_history || []).length
            ? el('div', { class: 'stack' },
                ...result.review_history.map((pass) => el('div', {
                  class: 'card inset', style: 'padding:var(--s4)',
                },
                  el('div', { class: 'spread' },
                    el('strong', {}, `Pass ${pass.attempt}`),
                    el('div', { class: 'row tight' },
                      StatusBadge(pass.decision === 'rework' ? 'rework' : 'approved',
                        { label: pass.decision === 'rework' ? 'Returned for rework' : 'Approved' }))),
                  pass.summary ? el('p', { class: 'mt3' }, pass.summary) : null,
                  (pass.findings || []).length
                    ? el('div', {},
                        el('div', { class: 'section-title' }, 'Findings'),
                        el('ul', {}, ...pass.findings.map((f) => el('li', {}, f))))
                    : null,
                  (pass.required_changes || []).length
                    ? el('div', {},
                        el('div', { class: 'section-title' }, 'Required changes'),
                        el('ul', {}, ...pass.required_changes.map((c) => el('li', {}, c))))
                    : null,
                  pass.rework_target
                    ? el('div', { class: 'small muted' },
                        `Routed back to: ${titleise(pass.rework_target)}`)
                    : null,
                  Object.keys(pass.scores || {}).length
                    ? DataTable(Object.entries(pass.scores).map(([k, v]) => ({ k, v })), [
                        { key: 'k', label: 'Dimension', render: (r) => titleise(r.k) },
                        { key: 'v', label: 'Score', align: 'right', render: (r) => `${r.v}/10` },
                      ], { compact: true })
                    : null)))
            : EmptyState('No review recorded', 'The Review/Critic agent did not run.')),
        Card({ title: 'Executive decision', hint: 'AI Executive' },
          el('div', { class: 'row' },
            StatusBadge(decision.decision || 'approved'),
            decision.requires_human_signoff
              ? Badge('Human sign-off required', { variant: 'warning', dot: true }) : null),
          el('p', { class: 'mt3' }, decision.rationale || '—'),
          (decision.open_items || []).length
            ? el('div', {},
                el('div', { class: 'section-title' }, 'Open items carried into delivery'),
                el('ul', {}, ...decision.open_items.map((item) => el('li', {}, item))))
            : null,
          (decision.goal_alignment || []).length
            ? el('div', { class: 'mt5' },
                el('div', { class: 'section-title' },
                  `Success-criteria coverage — ${decision.criteria_addressed}/${decision.criteria_total} evidenced`),
                el('ul', {}, ...decision.goal_alignment.map((goal) =>
                  el('li', {}, goal.goal, ' — ',
                    el('em', { class: goal.addressed ? '' : 'muted' },
                      goal.addressed ? 'evidenced' : 'not evidenced')))))
            : null,
          decision.human_signoff
            ? Banner('info',
                `Human ${decision.human_signoff.decision}d by ${decision.human_signoff.reviewer || 'reviewer'}`,
                decision.human_signoff.notes || 'No notes recorded.')
            : null));
      return;
    }

    if (state.activeTab === 'deliverable') {
      panelSlot.append(result.final_proposal
        ? Card({
            title: 'Final deliverable',
            actions: Button('Open full view', {
              size: 'sm', variant: 'primary', iconName: 'doc',
              href: `deliverable.html?id=${encodeURIComponent(requestId)}`,
            }),
          }, Prose(result.final_proposal, 'prose'))
        : EmptyState('No deliverable produced',
            'This engagement did not reach the report stage.', { iconName: 'doc' }));
    }
  }

  /* ------------------------------------------------------ approval panel */
  function approvalPanel() {
    const reviewer = el('input', { type: 'text', id: 'reviewer', placeholder: 'Your name' });
    const notes = el('input', { type: 'text', id: 'notes', placeholder: 'Reason (optional)' });
    const slot = el('div');
    const act = async (decisionValue) => {
      clear(slot);
      try {
        await api.workflows.decide(requestId, {
          decision: decisionValue, reviewer: reviewer.value.trim(), notes: notes.value.trim(),
        });
        toast(`Deliverable ${decisionValue === 'approve' ? 'approved and released' : 'rejected'}.`,
          { variant: decisionValue === 'approve' ? 'good' : 'info' });
        await refresh(true);
      } catch (error) { slot.append(ErrorState(error)); }
    };
    return Card({ title: 'Human review point' },
      Banner('warn', 'Held for sign-off',
        'Governance policy requires a person to release this deliverable. The workforce has '
        + 'finished its work and the proposal is below.'),
      el('div', { class: 'grid k2 mt4' },
        el('label', { class: 'field', for: 'reviewer' },
          el('span', { class: 'label' }, 'Reviewer'), reviewer),
        el('label', { class: 'field', for: 'notes' },
          el('span', { class: 'label' }, 'Notes'), notes)),
      slot,
      el('div', { class: 'row' },
        Button('Approve and release', { variant: 'primary', iconName: 'check',
          onClick: () => act('approve') }),
        Button('Reject', { variant: 'danger', onClick: () => act('reject') })));
  }

  function renderBanner() {
    clear(bannerSlot);
    if (status.status === 'failed') {
      bannerSlot.append(Banner('error', 'This engagement failed',
        el('div', {},
          el('div', {}, status.error || 'The workflow stopped before producing a deliverable.'),
          el('div', { class: 'small muted mt2' },
            'Stages that had not started are marked "not reached". The failure is recorded in '
            + 'the audit log.'))));
    }
    if (status.status === 'awaiting_approval') bannerSlot.append(approvalPanel());
  }

  /* ------------------------------------------------------------- polling */
  async function refresh(force = false) {
    const next = await api.workflows.status(requestId, state.lastEventId);
    for (const event of next.events || []) {
      state.events.push(event);
      state.lastEventId = Math.max(state.lastEventId, event.id);
    }
    next.events = state.events;
    status = next;

    renderHeader();
    renderElapsed();
    renderPipeline();
    renderFeed();
    renderTasks();
    renderBanner();

    const finished = TERMINAL.has(status.status) || status.status === 'awaiting_approval';
    if (finished && (!state.result || force)) {
      try {
        state.result = await api.workflows.result(requestId);
        state.detail = null;
      } catch { state.result = null; }
      renderPanel();
    } else if (!state.result) {
      renderPanel();
    }

    if (TERMINAL.has(status.status) && state.timer) {
      window.clearInterval(state.timer);
      state.timer = null;
    }
    return status;
  }

  const root = el('div', { class: 'stack' },
    bannerSlot,
    Card({}, headerSlot),
    el('div', { class: 'grid split' },
      Card({ title: 'Execution pipeline', hint: 'live from backend state' },
        el('p', { class: 'small muted', style: 'margin-top:calc(var(--s4) * -1 + 2px)' },
          'Select a stage to inspect its recorded output.'),
        pipelineSlot),
      Card({ title: 'Activity', hint: feedFilter }, feedSlot)),
    Card({ title: 'Agent assignments', hint: 'AI Executive' }, tasksSlot),
    el('div', {}, tabsSlot, el('div', { class: 'mt4' }, panelSlot)));

  renderHeader();
  renderElapsed();
  renderPipeline();
  renderFeed();
  renderTasks();
  renderBanner();
  renderTabs();
  renderPanel();

  await refresh(true);

  if (!TERMINAL.has(status.status)) {
    state.timer = window.setInterval(() => {
      refresh().catch((error) => {
        if (error instanceof ApiError && error.isOffline) {
          window.clearInterval(state.timer);
          state.timer = null;
          clear(bannerSlot).append(ErrorState(error, { onRetry: () => window.location.reload() }));
        }
      });
    }, 1500);
    state.ticker = window.setInterval(renderElapsed, 1000);
  }

  return {
    node: root,
    dispose: () => {
      if (state.timer) window.clearInterval(state.timer);
      if (state.ticker) window.clearInterval(state.ticker);
    },
  };
});
