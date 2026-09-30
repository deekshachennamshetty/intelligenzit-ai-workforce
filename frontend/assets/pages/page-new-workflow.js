/* Submit an objective to the workforce.
   On submission the form is replaced in place by a live launch panel — the page
   is never reloaded and the operator keeps their context. */

import { el, icon, clear, append } from '../lib/dom.js';
import { titleise, duration, elapsedSince } from '../lib/format.js';
import { api, ApiError } from '../api/index.js';
import {
  page, Card, Button, StatusBadge, Badge, ErrorState, ProgressBar, Pipeline,
  EmptyState, toast, Banner,
} from '../components/index.js';

const REFERENCE_OBJECTIVE =
  'An organisation wants to improve a core business process using AI. Analyze the problem, '
  + 'identify relevant data, recommend a solution, assess risks, and create an implementation roadmap.';

const MIN_CHARS = 15;
const MAX_CHARS = 8000;

/* Matches AgentRegistry's actual departments (see backend/agent_registry.py) so a
   selection here names a department that genuinely exists on the platform. */
const DEPARTMENTS = [
  ['', 'No specific department'],
  ['workflow_orchestration', 'Workflow Orchestration'],
  ['research_intelligence', 'Research & Intelligence'],
  ['business_analysis', 'Business Analysis'],
  ['solution_delivery', 'Solution Delivery'],
  ['quality_assurance', 'Quality Assurance'],
  ['reporting', 'Reporting'],
];

page({
  title: 'New workflow',
  breadcrumb: [{ label: 'Workflows', href: 'workflows.html' }, { label: 'New' }],
  active: 'new',
  loadingLabel: 'Preparing the workforce…',
}, async ({ content }) => {
  const platformConfig = await api.platform.configuration();
  const stageMeta = Object.fromEntries(platformConfig.stages.map((s) => [s.id, s]));

  const root = el('div', { class: 'grid split' });
  const left = el('div', { class: 'stack' });
  const right = el('div', { class: 'stack' });
  root.append(left, right);

  /* ------------------------------------------------------------- the form */
  const objective = el('textarea', {
    id: 'objective', name: 'objective', required: true, 'aria-describedby': 'objective-help',
    maxlength: String(MAX_CHARS),
    placeholder: 'Describe the business objective the workforce should deliver against — the '
      + 'problem, the outcome you need, and anything it must respect…',
  });
  const counter = el('div', { class: 'char-count' }, `0 / ${MAX_CHARS}`);
  objective.addEventListener('input', () => {
    counter.textContent = `${objective.value.length} / ${MAX_CHARS}`;
    objective.setAttribute('aria-invalid', 'false');
    clear(alertSlot);
  });

  const priority = el('select', { id: 'priority' },
    ...['normal', 'low', 'high', 'critical'].map((value) =>
      el('option', { value, ...(value === 'normal' ? { selected: true } : {}) }, titleise(value))));
  const department = el('select', { id: 'department' },
    ...DEPARTMENTS.map(([value, label]) => el('option', { value }, label)));
  const outcome = el('input', { type: 'text', id: 'expected_outcome',
    placeholder: 'e.g. A fundable proposal with a phased roadmap' });
  const constraints = el('input', { type: 'text', id: 'constraints',
    placeholder: 'e.g. Delivery within two quarters; no new data platform purchase' });

  const alertSlot = el('div');
  const submitButton = Button('Run AI Workforce', {
    variant: 'primary', size: 'lg', type: 'submit', iconName: 'arrowRight',
  });

  const form = el('form', {
    id: 'workflow-form', novalidate: true,
    onsubmit: async (event) => {
      event.preventDefault();
      clear(alertSlot);
      const value = objective.value.trim();
      if (value.length < MIN_CHARS) {
        objective.setAttribute('aria-invalid', 'true');
        alertSlot.append(Banner('warn', 'The objective needs more detail',
          `Describe the problem and the outcome in at least ${MIN_CHARS} characters so the AI `
          + 'Executive has something to analyse.'));
        objective.focus();
        return;
      }
      submitButton.disabled = true;
      clear(submitButton).append(el('span', { class: 'spinner' }), el('span', {}, 'Dispatching…'));
      try {
        const response = await api.workflows.create({
          objective: value,
          priority: priority.value,
          department: department.value,
          expected_outcome: outcome.value.trim(),
          constraints: constraints.value.trim(),
          submitted_by: 'operator',
        });
        toast('The AI Executive has taken the objective.',
          { title: 'Engagement dispatched', variant: 'good' });
        showLaunch(response.request_id, value);
      } catch (error) {
        submitButton.disabled = false;
        clear(submitButton).append(icon('arrowRight', { size: 15 }),
          el('span', {}, 'Run AI Workforce'));
        alertSlot.append(ErrorState(error));
      }
    },
  },
    el('label', { class: 'field', for: 'objective' },
      el('span', { class: 'label' }, 'Business objective', el('span', { class: 'req' }, '*')),
      objective, counter,
      el('span', { class: 'help', id: 'objective-help' },
        'The AI Executive reads this the way a chief executive would: the real problem beneath '
        + 'the request, the outcome being bought, and the constraints that are not negotiable.')),
    el('div', { class: 'grid k2' },
      el('label', { class: 'field', for: 'priority' },
        el('span', { class: 'label' }, 'Priority'), priority),
      el('label', { class: 'field', for: 'department' },
        el('span', { class: 'label' }, 'Requesting department'), department)),
    el('label', { class: 'field', for: 'expected_outcome' },
      el('span', { class: 'label' }, 'Expected outcome'), outcome,
      el('span', { class: 'help' },
        'Optional. Used by the Goal Engine to align the work and to score coverage at review.')),
    el('label', { class: 'field', for: 'constraints' },
      el('span', { class: 'label' }, 'Constraints'), constraints,
      el('span', { class: 'help' }, 'Optional. Anything the recommendation must respect.')),
    alertSlot,
    el('div', { class: 'row mt4' },
      submitButton,
      Button('Use reference objective', {
        variant: 'ghost',
        onClick: () => {
          objective.value = REFERENCE_OBJECTIVE;
          outcome.value = 'A fundable proposal with a phased roadmap.';
          constraints.value = 'Delivery within two quarters.';
          objective.dispatchEvent(new Event('input'));
          objective.focus();
        },
      })));

  const formCard = Card({
    title: 'Engagement brief',
    subtitle: 'One objective in. A reviewed, execution-ready deliverable out.',
  }, form);
  left.append(formCard);

  /* ----------------------------------------------------------- what's next */
  right.append(Card({
    title: 'What happens next',
    hint: `${platformConfig.stages.length} stages`,
  },
    el('div', { class: 'pipeline' },
      ...platformConfig.stages.map((stage, index) => el('div', { class: 'pstage pending' },
        el('div', { class: 'mark' }, String(index + 1)),
        el('div', { class: 'grow' },
          el('div', { class: 'stage-name' }, stage.label),
          el('div', { class: 'stage-who' }, titleise(stage.agent))),
        el('div', { class: 'stage-meta' },
          stage.parallel_group ? Badge('parallel', { variant: 'accent' }) : '')))),
    el('p', { class: 'small muted mt4 mb0' },
      'Each stage runs in sequence. Quality Review can return the draft to the Solution stage '
      + `for rework, up to ${platformConfig.max_rework_cycles} times, before the report is `
      + 'released.')));

  right.append(Card({ title: 'Policy in force' },
    el('dl', { class: 'kv' },
      el('dt', {}, 'Rework cap'), el('dd', {}, `${platformConfig.max_rework_cycles} cycles`),
      el('dt', {}, 'Human sign-off'),
      el('dd', {}, platformConfig.require_human_approval
        ? 'Required on every deliverable' : 'Only when the reviewer escalates'),
      el('dt', {}, 'Engine'),
      el('dd', {}, platformConfig.credentials_configured
        ? `${titleise(platformConfig.provider)} · ${platformConfig.model}`
        : 'Local development engine'))));

  /* -------------------------------------------------------- launch panel */
  let poller = null;
  function showLaunch(requestId, objectiveText) {
    clear(left);
    const statusSlot = el('div', { class: 'stack' });
    left.append(Card({
      title: 'Engagement dispatched',
      actions: Button('Open full execution view', {
        variant: 'primary', size: 'sm', iconName: 'arrowRight',
        href: `workflow.html?id=${encodeURIComponent(requestId)}`,
      }),
    },
      el('dl', { class: 'kv' },
        el('dt', {}, 'Workflow ID'), el('dd', { class: 'mono' }, requestId),
        el('dt', {}, 'Objective'), el('dd', {}, objectiveText)),
      el('div', { class: 'mt5' }, statusSlot)));

    left.append(Card({ title: 'Submit another objective' },
      el('p', { class: 'muted small' },
        'The workforce runs engagements concurrently. You can dispatch another while this one '
        + 'executes.'),
      Button('New brief', { iconName: 'plus', onClick: () => window.location.reload() })));

    const render = (status) => {
      const assigned = (status.tasks || []).filter((task) => task.agent_id);
      clear(statusSlot);
      append(statusSlot, [
        el('div', { class: 'spread' },
          StatusBadge(status.status),
          el('span', { class: 'small muted' },
            status.status === 'completed' && status.duration_ms
              ? `Completed in ${duration(status.duration_ms)}`
              : `Elapsed ${duration(elapsedSince(status.created_at))}`)),
        ProgressBar(status.progress, { label: 'Engagement progress' }),
        el('div', { class: 'small muted' },
          `${status.progress}% · current stage: `,
          el('span', { class: 'strong' },
            status.current_stage ? titleise(status.current_stage) : 'starting')),
        assigned.length
          ? el('div', {},
              el('div', { class: 'section-title mt4' }, 'Assigned agents'),
              el('div', { class: 'chip-list' },
                ...Array.from(new Set(assigned.map((t) => t.agent_id)))
                  .map((agentId) => Badge(titleise(agentId), { variant: 'brand' }))))
          : el('div', { class: 'small muted mt3' },
              'The AI Executive is still selecting which agents this objective needs.'),
        el('div', { class: 'mt4' },
          Pipeline(status.stages, { meta: stageMeta })),
        status.status === 'completed'
          ? el('div', { class: 'row mt4' },
              Button('View the deliverable', {
                variant: 'primary', iconName: 'doc',
                href: `deliverable.html?id=${encodeURIComponent(requestId)}`,
              }),
              Button('Research workspace', {
                iconName: 'research',
                href: `research.html?id=${encodeURIComponent(requestId)}`,
              }))
          : null,
        status.status === 'failed'
          ? Banner('error', 'This engagement failed',
              status.error || 'The workflow stopped before producing a deliverable.')
          : null,
      ]);
    };

    const tick = async () => {
      try {
        const status = await api.workflows.status(requestId);
        render(status);
        if (['completed', 'failed', 'rejected', 'awaiting_approval'].includes(status.status)) {
          window.clearInterval(poller);
          poller = null;
        }
      } catch (error) {
        if (error instanceof ApiError && error.isOffline) {
          window.clearInterval(poller);
          poller = null;
          clear(statusSlot).append(ErrorState(error, { onRetry: () => showLaunch(requestId, objectiveText) }));
        }
      }
    };
    tick();
    poller = window.setInterval(tick, 1500);
  }

  window.requestAnimationFrame(() => objective.focus());

  return { node: root, dispose: () => { if (poller) window.clearInterval(poller); } };
});
