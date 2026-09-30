/* ==========================================================================
   Intelligenzit — public landing page.
   Every figure shown here is read from the running platform. When the platform
   cannot be reached the page still explains the product, but the live numbers
   are omitted rather than replaced with invented ones.
   ========================================================================== */

import { el, svg, icon, clear } from '../lib/dom.js';
import { titleise, number, initials } from '../lib/format.js';
import { api } from '../api/index.js';
import { Button } from '../components/primitives.js';
import { applyStoredTheme, toggleTheme } from '../components/shell.js';
import { IconButton } from '../components/primitives.js';

applyStoredTheme();

/* --------------------------------------------------------------- content */
const PROBLEMS = [
  ['layers', 'Fragmented tooling',
   'Research lives in one tool, analysis in another, the deck in a third. Nothing carries '
   + 'context forward, so the same ground is covered repeatedly.'],
  ['branch', 'Manual coordination',
   'A person has to decide who does what, chase each hand-off and notice when a piece is '
   + 'missing. Coordination becomes the bottleneck, not the thinking.'],
  ['doc', 'Disconnected outputs',
   'Findings, recommendations and risk assessments are produced separately, then reconciled '
   + 'by hand — if anyone notices they disagree.'],
  ['refresh', 'Repeated work',
   'Each engagement starts from nothing. What the last one learned lives in someone’s head '
   + 'or in a document nobody opens again.'],
  ['shield', 'No governance',
   'When output is generated rather than authored, there is rarely a record of who produced '
   + 'what, on what basis, or who approved it.'],
  ['alert', 'No accountability',
   'A single assistant answers everything and owns nothing. There is no reviewer with the '
   + 'authority to send work back.'],
];

const RESEARCH_CHAIN = [
  ['Questions', 'The department first decides which questions the engagement actually turns '
   + 'on — the ones where a different answer changes the recommendation.'],
  ['Evidence basis', 'Every finding declares what it rests on: an established pattern, a '
   + 'benchmark, an inference, the client brief, or a previous engagement.'],
  ['Evidence', 'The specific observation or reasoning behind each claim is recorded next to '
   + 'the claim, not buried in prose.'],
  ['Findings', 'Findings are linked back to the question they answer, so coverage is visible '
   + 'rather than assumed.'],
  ['Confidence', 'A single score built from evidence strength, question coverage and open '
   + 'gaps. It measures coverage, not truth.'],
  ['Decision', 'Strategy commits only to what the evidence supports. Gaps travel with the '
   + 'recommendation instead of disappearing.'],
];

const QUALITY_GATES = [
  ['Review gates', 'A Review/Critic agent checks each proposed solution against the requirements '
   + 'for completeness, internal consistency and unjustified claims, then approves it or sends it '
   + 'back with written notes on what to fix.'],
  ['Rework', 'A rejected solution returns to the Solution stage with the reviewer’s notes, so the '
   + 'next attempt addresses them directly.'],
  ['Bounded loops', 'Rework is capped. On reaching the cap the engagement is released as-is with '
   + 'the open review notes attached, rather than looping indefinitely.'],
  ['Auditability', 'Every agent run, tool call and access decision is written to an append-only '
   + 'audit log as it happens, with secrets and prompts redacted.'],
];

/** Short, unambiguous label for a node in the hero visual. */
function shortAgentLabel(name) {
  const cleaned = String(name || '').replace(/\s*Agent$/, '').trim();
  const SHORT = {
    Orchestrator: 'Orchestrator', Requirements: 'Requirements',
    'Review/Critic': 'Review',
  };
  return SHORT[cleaned] || cleaned;
}

/* ----------------------------------------------------------------- pieces */
function Nav() {
  const themeButton = IconButton('theme', { title: 'Switch between light and dark' });
  themeButton.addEventListener('click', () => toggleTheme());

  const nav = el('header', { class: 'lp-nav', id: 'lp-nav' },
    el('a', { class: 'brand-mark', href: 'index.html' },
      el('div', { class: 'brand-glyph', 'aria-hidden': 'true' }, 'IZ'),
      el('div', {},
        el('div', { class: 'brand-name' }, 'Intelligenzit'),
        el('div', { class: 'brand-sub' }, 'AI Workforce'))),
    el('nav', { class: 'lp-nav-links', 'aria-label': 'Page sections' },
      el('a', { class: 'lp-nav-link', href: '#workforce' }, 'Workforce'),
      el('a', { class: 'lp-nav-link', href: '#how' }, 'How it works'),
      el('a', { class: 'lp-nav-link', href: '#research' }, 'Research'),
      el('a', { class: 'lp-nav-link', href: '#governance' }, 'Governance'),
      themeButton,
      Button('Sign in', { variant: 'primary', size: '', href: 'login.html' })));

  const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 8);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  return nav;
}

/** The hero visual: specialised agents coordinating around one objective.
    Labels sit outside the ring so they can never clip the node they name. */
function Constellation(agents) {
  const roster = (agents && agents.length ? agents : [
    { label: 'Research', initials: 'RS' }, { label: 'Data', initials: 'DA' },
    { label: 'Risk', initials: 'RK' }, { label: 'Strategy', initials: 'ST' },
    { label: 'Architecture', initials: 'AR' }, { label: 'Quality', initials: 'QC' },
    { label: 'Deliverable', initials: 'DL' },
  ]).slice(0, 7);

  const width = 600, height = 470, cx = width / 2, cy = height / 2;
  const radius = 142, nodeR = 25, labelGap = 15;
  const root = svg('svg', {
    viewBox: `0 0 ${width} ${height}`, role: 'img',
    'aria-label': `${roster.length} specialised agents coordinating around one business `
      + `objective: ${roster.map((a) => a.label).join(', ')}`,
  });

  root.append(svg('circle', { class: 'lp-orbit', cx, cy, r: radius }));
  root.append(svg('circle', { class: 'lp-orbit', cx, cy, r: radius * 0.58 }));

  const points = roster.map((agent, index) => {
    const angle = (-Math.PI / 2) + (index * 2 * Math.PI) / roster.length;
    return {
      ...agent, angle,
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    };
  });

  // Spokes from the objective to each agent, plus a travelling pulse.
  points.forEach((point, index) => {
    root.append(svg('line', {
      class: 'lp-link', x1: cx, y1: cy, x2: point.x, y2: point.y,
      style: `opacity:${(0.3 + (index % 3) * 0.18).toFixed(2)}`,
    }));
    const length = Math.hypot(point.x - cx, point.y - cy);
    root.append(svg('line', {
      class: 'lp-pulse', x1: cx, y1: cy, x2: point.x, y2: point.y,
      'stroke-dasharray': `26 ${length}`, 'stroke-dashoffset': length + 26,
      style: `animation: travel 2.8s ${(index * 0.34).toFixed(2)}s var(--ease) infinite`,
    }));
  });

  // Hand-offs between neighbouring agents.
  points.forEach((point, index) => {
    const next = points[(index + 1) % points.length];
    root.append(svg('path', {
      class: 'lp-link', style: 'opacity:.16',
      d: `M${point.x},${point.y} Q${cx},${cy} ${next.x},${next.y}`,
    }));
  });

  points.forEach((point, index) => {
    const group = svg('g', {
      class: `lp-node-group${index % 3 === 0 ? ' active' : ''}`,
      style: `animation-delay:${(index * 0.4).toFixed(2)}s`,
    });
    group.append(svg('circle', {
      class: `lp-node${index % 3 === 0 ? ' active' : ''}`, cx: point.x, cy: point.y, r: nodeR,
    }));
    group.append(svg('text', {
      class: 'lp-node-initials', x: point.x, y: point.y + 4, 'text-anchor': 'middle',
    }, point.initials));

    // Labels sit clear of the node's outer edge, with extra room top and bottom
    // where a centred label would otherwise sit on the circle.
    const labelRadius = radius + nodeR + labelGap;
    const cos = Math.cos(point.angle);
    const sin = Math.sin(point.angle);
    const vertical = Math.abs(cos) < 0.3;
    const lx = cx + labelRadius * cos;
    const ly = cy + labelRadius * sin;
    const anchor = vertical ? 'middle' : (cos > 0 ? 'start' : 'end');
    const dy = vertical ? (sin > 0 ? 12 : -4) : 4;
    group.append(svg('text', {
      class: 'lp-node-label', x: lx, y: ly + dy, 'text-anchor': anchor,
    }, point.label));
    root.append(group);
  });

  root.append(svg('circle', { class: 'lp-core', cx, cy, r: 58 }));
  root.append(svg('text', { class: 'lp-core-sub', x: cx, y: cy - 14, 'text-anchor': 'middle' },
    'OBJECTIVE'));
  root.append(svg('text', { class: 'lp-core-label', x: cx, y: cy + 4, 'text-anchor': 'middle' },
    'One outcome'));
  root.append(svg('text', { class: 'lp-core-sub', x: cx, y: cy + 24, 'text-anchor': 'middle' },
    'REVIEWED'));

  return el('div', { class: 'lp-constellation' }, root);
}

function Hero(roster) {
  return el('section', { class: 'lp-section lp-hero' },
    el('div', { class: 'lp-inner lp-hero-grid' },
      el('div', {},
        el('span', { class: 'lp-eyebrow' }, icon('spark', { size: 13 }), 'The company of agents'),
        el('h1', { class: 'lp-h1' },
          'One AI Workforce.', el('br'),
          el('span', { class: 'accent' }, 'Built to Think, Execute, and Improve.')),
        el('p', { class: 'lp-lede' },
          'Intelligenzit turns business objectives into coordinated AI execution — assigning '
          + 'specialized agents, running work in parallel, reviewing outcomes, and continuously '
          + 'improving the result.'),
        el('div', { class: 'lp-cta-row' },
          Button('Start a Workflow', { variant: 'primary', size: 'lg', href: 'new-workflow.html',
            iconName: 'arrowRight' }),
          Button('Explore the Workforce', { size: 'lg', href: 'agents.html' })),
        el('p', { class: 'lp-hero-note', id: 'hero-note' },
          'Objective in, reviewed deliverable out — with every hand-off recorded.')),
      Constellation(roster)));
}

function StatStrip(stats) {
  if (!stats.length) return null;
  return el('section', { class: 'lp-section', style: 'padding-top:0' },
    el('div', { class: 'lp-inner lp-stats reveal' },
      ...stats.map(([value, key]) => el('div', { class: 'lp-stat' },
        el('div', { class: 'v' }, value), el('div', { class: 'k' }, key)))));
}

function ProblemSection() {
  return el('section', { class: 'lp-section lp-alt', id: 'problem' },
    el('div', { class: 'lp-inner' },
      el('div', { class: 'reveal' },
        el('div', { class: 'lp-kicker' }, 'The problem'),
        el('h2', { class: 'lp-h2' }, 'AI adoption stalls at coordination, not capability.'),
        el('p', { class: 'lp-sub' },
          'Most organisations now have capable models. What they do not have is an operating '
          + 'model around them — so the work stays manual, the output stays disconnected, and '
          + 'nobody can say who is accountable for any of it.')),
      el('div', { class: 'lp-cards reveal' },
        ...PROBLEMS.map(([glyph, title, body]) => el('article', { class: 'lp-card' },
          el('div', { class: 'lp-card-icon' }, icon(glyph, { size: 18 })),
          el('h3', {}, title),
          el('p', {}, body))))));
}

function WorkforceSection(organisation) {
  const departments = organisation ? organisation.departments : [];
  const executing = departments.flatMap((d) => d.agents.filter((a) => a.executes));

  const structure = el('div', { class: 'orgchart reveal' },
    el('div', { class: 'org-node executive' },
      el('div', { class: 'role' }, 'Accountable'), el('div', { class: 'nm' }, 'AI Executive')),
    el('div', { class: 'org-connector', 'aria-hidden': 'true' }),
    el('div', { class: 'org-node' },
      el('div', { class: 'role' }, 'Delegation'), el('div', { class: 'nm' }, 'Workflow Manager')),
    el('div', { class: 'org-connector', 'aria-hidden': 'true' }),
    el('div', { class: 'org-level' },
      ...(departments.length
        ? departments.filter((d) => d.id !== 'executive').map((department) =>
            el('div', { class: 'org-node' },
              el('div', { class: 'role' }, `${department.agents.length} agents`),
              el('div', { class: 'nm' }, department.name)))
        : [el('div', { class: 'org-node' },
            el('div', { class: 'role' }, 'Departments'),
            el('div', { class: 'nm' }, 'Unavailable while the platform is offline'))])));

  const roster = departments.length
    ? el('div', { class: 'lp-cards reveal mt6' },
        ...departments.map((department) => el('article', { class: 'lp-card' },
          el('h3', {}, department.name),
          el('p', {}, department.mandate),
          el('div', { class: 'chip-list mt4' },
            ...department.agents.map((agent) => el('span', {
              class: `badge${agent.executes ? ' brand' : ''}`,
              title: agent.mission,
            }, agent.name))))))
    : null;

  return el('section', { class: 'lp-section', id: 'workforce' },
    el('div', { class: 'lp-inner' },
      el('div', { class: 'reveal' },
        el('div', { class: 'lp-kicker' }, 'The AI workforce'),
        el('h2', { class: 'lp-h2' }, 'An organisation, not an assistant.'),
        el('p', { class: 'lp-sub' },
          organisation
            ? `${organisation.totals.agents} agents across ${organisation.totals.departments} `
              + `departments, each with a declared mission, capabilities and permissions. `
              + `${executing.length} of them execute inside the workflow today; the rest are `
              + 'registered and governed, ready to be brought onto the execution path.'
            : 'Agents are registered with a declared mission, capabilities and permissions. '
              + 'Start the platform to see the live organisation.')),
      structure,
      roster));
}

function HowItWorksSection(stages, agentNames = {}) {
  const list = stages.length ? stages : [];
  const detail = el('div', { class: 'lp-flow-detail' });
  const buttons = [];

  const select = (index) => {
    buttons.forEach((button, i) => button.setAttribute('aria-selected', String(i === index)));
    const stage = list[index];
    clear(detail).append(
      el('div', { class: 'lp-kicker' }, `Stage ${index + 1} of ${list.length}`),
      el('h3', { style: 'font-size:var(--text-xl);margin-bottom:var(--s3)' }, stage.label),
      el('p', { class: 'muted', style: 'font-size:var(--text-sm)' }, STAGE_NOTES[stage.id] || ''),
      el('dl', { class: 'kv mt5' },
        el('dt', {}, 'Accountable agent'), el('dd', {}, agentNames[stage.agent] || titleise(stage.agent)),
        el('dt', {}, 'Department'), el('dd', {}, titleise(stage.department)),
        el('dt', {}, 'Execution'), el('dd', {}, 'Runs in sequence')));
  };

  const steps = el('div', { class: 'lp-flow-list', role: 'tablist',
    'aria-label': 'Workflow stages' });
  list.forEach((stage, index) => {
    const button = el('button', {
      class: `lp-flow-step${stage.parallel_group ? ' parallel' : ''}`,
      type: 'button', role: 'tab', 'aria-selected': String(index === 0),
      onclick: () => select(index),
      onmouseenter: () => select(index),
    },
      el('span', { class: 'n' }, String(index + 1)),
      el('span', {},
        el('span', { class: 't' }, stage.label),
        el('br'),
        el('span', { class: 'who' }, agentNames[stage.agent] || titleise(stage.agent))));
    buttons.push(button);
    steps.append(button);
  });

  if (list.length) select(0);
  else clear(detail).append(el('p', { class: 'muted' },
    'The stage list is published by the running platform. Start it to see the live pipeline.'));

  return el('section', { class: 'lp-section lp-alt', id: 'how' },
    el('div', { class: 'lp-inner' },
      el('div', { class: 'reveal' },
        el('div', { class: 'lp-kicker' }, 'How it works'),
        el('h2', { class: 'lp-h2' }, `One objective, ${list.length || 'several'} accountable stages.`),
        el('p', { class: 'lp-sub' },
          'This is the actual pipeline the platform executes — read from the running service, '
          + 'not an illustration. Select a stage to see who owns it.')),
      el('div', { class: 'lp-flow reveal' }, steps, detail)));
}

const STAGE_NOTES = {
  orchestrate: 'The Orchestrator classifies the incoming objective and produces the execution '
    + 'plan naming which stages the engagement needs.',
  research: 'The Research agent gathers relevant background, prior art and constraints for the '
    + 'objective, without proposing a solution.',
  requirements: 'The Requirements agent converts the objective and the research findings into a '
    + 'structured list of functional and non-functional requirements.',
  analysis: 'The Analysis agent identifies viable options, trade-offs, risks and constraints from '
    + 'the requirements, without committing to an answer yet.',
  solution: 'The Solution agent proposes a specific, justified technical recommendation, revising '
    + 'it if Quality Review sends it back.',
  review: 'The Review/Critic agent checks the proposed solution against the requirements for '
    + 'completeness and consistency, approving it or sending it back for rework.',
  report: 'The Report agent combines the requirements, analysis and approved solution into the '
    + 'final, client-ready deliverable.',
};

function ResearchSection(researchStats) {
  const live = researchStats && researchStats.has_data;
  return el('section', { class: 'lp-section', id: 'research' },
    el('div', { class: 'lp-inner' },
      el('div', { class: 'reveal' },
        el('div', { class: 'lp-kicker' }, 'Research intelligence'),
        el('h2', { class: 'lp-h2' }, 'Evidence you can interrogate, not prose you have to trust.'),
        el('p', { class: 'lp-sub' },
          'Research is a first-class product surface. The department returns a structured '
          + 'dossier — questions, findings, the evidence behind each one, a confidence score and '
          + 'an explicit record of what it could not establish.')),
      el('div', { class: 'lp-chain reveal' },
        ...RESEARCH_CHAIN.map(([title, body], index) => el('div', { class: 'lp-chain-item' },
          el('div', { class: 'step' }, `Step ${index + 1}`),
          el('h4', {}, title),
          el('p', {}, body)))),
      el('div', { class: 'banner neutral reveal mt6' },
        icon('shield', { size: 18, cls: 'banner-icon' }),
        el('div', {},
          el('strong', {}, 'No invented citations'),
          el('div', {},
            'The platform performs no external retrieval. Every finding declares its basis — an '
            + 'established pattern, a benchmark, an inference, the client brief, or a previous '
            + 'engagement — and inferences are marked as inferences. The interface shows that '
            + 'basis next to the claim.'))),
      live
        ? el('div', { class: 'lp-stats reveal mt6' },
            el('div', { class: 'lp-stat' },
              el('div', { class: 'v' }, `${researchStats.avg_confidence}`),
              el('div', { class: 'k' }, 'Mean research confidence, out of 100')),
            el('div', { class: 'lp-stat' },
              el('div', { class: 'v' }, number(researchStats.findings)),
              el('div', { class: 'k' }, 'Findings recorded with evidence')),
            el('div', { class: 'lp-stat' },
              el('div', { class: 'v' }, `${researchStats.question_completion_rate}%`),
              el('div', { class: 'k' }, 'Research questions answered')),
            el('div', { class: 'lp-stat' },
              el('div', { class: 'v' }, number(researchStats.open_gaps)),
              el('div', { class: 'k' }, 'Evidence gaps recorded, not hidden')))
        : null));
}

function QualitySection() {
  return el('section', { class: 'lp-section', id: 'governance' },
    el('div', { class: 'lp-inner' },
      el('div', { class: 'reveal' },
        el('div', { class: 'lp-kicker' }, 'Quality control & governance'),
        el('h2', { class: 'lp-h2' }, 'A reviewer with the authority to say no.'),
        el('p', { class: 'lp-sub' },
          'Generated work is only trustworthy if something can reject it. A Review/Critic agent '
          + 'checks every proposed solution and returns deficient work to the Solution stage with '
          + 'written notes.')),
      el('div', { class: 'lp-cards reveal' },
        ...QUALITY_GATES.map(([title, body]) => el('article', { class: 'lp-card' },
          el('div', { class: 'lp-card-icon' }, icon('shield', { size: 18 })),
          el('h3', {}, title),
          el('p', {}, body))))));
}

function MemorySection() {
  return el('section', { class: 'lp-section lp-alt', id: 'memory' },
    el('div', { class: 'lp-inner' },
      el('div', { class: 'reveal' },
        el('div', { class: 'lp-kicker' }, 'Memory & learning'),
        el('h2', { class: 'lp-h2' }, 'Each engagement starts from what the last one learned.'),
        el('p', { class: 'lp-sub' },
          'When an engagement closes, its final report is written to memory automatically. '
          + 'The next matching objective recalls it, and the AI Executive receives it as prior '
          + 'context before planning.')),
      el('div', { class: 'lp-chain reveal' },
        ...[
          ['Capture', 'The final report is written to memory automatically when an engagement '
            + 'completes.'],
          ['Recall', 'A new objective retrieves the relevant prior reports before planning '
            + 'begins.'],
          ['Attribution', 'The workflow overview shows exactly which memory entries informed '
            + 'the engagement, with their source workflow.'],
        ].map(([title, body], index) => el('div', { class: 'lp-chain-item' },
          el('div', { class: 'step' }, `0${index + 1}`),
          el('h4', {}, title),
          el('p', {}, body))))));
}

function VisibilitySection(dashboard, organisation) {
  const body = el('div', { class: 'lp-preview-body' });

  if (dashboard) {
    const summary = dashboard.summary;
    body.append(
      el('div', { class: 'grid k4' },
        tile('Completed', number(summary.completed_workflows)),
        tile('Rework rate', summary.reviewed_workflows ? `${summary.rework_rate}%` : '—'),
        tile('Research confidence',
          dashboard.research && dashboard.research.has_data
            ? String(dashboard.research.avg_confidence) : '—'),
        tile('Agent executions', number(summary.agent_runs))));

    const engaged = (dashboard.agents || []).filter((a) => a.runs > 0).slice(0, 6);
    if (engaged.length) {
      body.append(el('div', {},
        el('div', { class: 'section-title' }, 'Agents with recorded executions'),
        el('div', { class: 'chip-list' },
          ...engaged.map((agent) => el('span', { class: 'badge' },
            `${agent.name} · ${agent.runs}`)))));
    }
    const events = (dashboard.recent_events || []).slice(0, 6);
    if (events.length) {
      body.append(el('div', {},
        el('div', { class: 'section-title' }, 'Recent organisation activity'),
        el('div', { class: 'stack tight' },
          ...events.map((event) => el('div', { class: 'small muted' },
            `${titleise(event.agent_id || 'platform')} — ${event.message}`)))));
    }
  } else {
    body.append(el('p', { class: 'muted', style: 'margin:0' },
      'The live preview reads from the running platform. It is not available right now, so '
      + 'nothing is shown here rather than placeholder figures.'));
  }

  return el('section', { class: 'lp-section', id: 'visibility' },
    el('div', { class: 'lp-inner' },
      el('div', { class: 'reveal' },
        el('div', { class: 'lp-kicker' }, 'Workforce visibility'),
        el('h2', { class: 'lp-h2' }, 'Everything the workforce does is on the record.'),
        el('p', { class: 'lp-sub' },
          'Agents, stages, status and metrics are all readable while work is running. '
          + 'This preview is live from the platform behind this page.')),
      el('div', { class: 'lp-preview reveal' },
        el('div', { class: 'lp-preview-bar' },
          el('span', { class: 'lp-preview-dot' }), el('span', { class: 'lp-preview-dot' }),
          el('span', { class: 'lp-preview-dot' }),
          el('span', { class: 'small muted', style: 'margin-left:8px' },
            organisation
              ? `${organisation.totals.agents} agents · ${organisation.totals.departments} departments`
              : 'Intelligenzit platform')),
        body),
      el('div', { class: 'lp-cta-row reveal mt6' },
        Button('Open the dashboard', { href: 'dashboard.html', iconName: 'dashboard' }),
        Button('Browse the workforce', { href: 'agents.html', iconName: 'agents' }),
        Button('See the metrics', { href: 'metrics.html', iconName: 'metrics' }))));
}

function tile(label, value) {
  return el('div', { class: 'tile' },
    el('div', { class: 'tile-label' }, label),
    el('div', { class: 'tile-value' }, value));
}

function CtaSection() {
  return el('section', { class: 'lp-section' },
    el('div', { class: 'lp-inner' },
      el('div', { class: 'lp-cta reveal' },
        el('h2', {}, 'Give the workforce an objective.'),
        el('p', {},
          'Describe the outcome you need. The organisation decomposes it, assigns it, executes '
          + 'it, reviews it and hands back a deliverable you can act on.'),
        el('div', { class: 'lp-cta-row', style: 'justify-content:center' },
          Button('Get started', { variant: 'primary', size: 'lg',
            href: 'login.html', iconName: 'arrowRight' }),
          Button('Explore the Workforce', { size: 'lg', href: 'agents.html' })))));
}

function Footer(health) {
  return el('footer', { class: 'lp-footer' },
    el('div', { class: 'lp-footer-inner' },
      el('div', { class: 'grow' },
        el('strong', { style: 'color:var(--ink-2)' }, 'Intelligenzit AI Workforce Platform'),
        health ? el('span', {}, ` · v${health.version}`) : null),
      el('a', { href: 'dashboard.html' }, 'Dashboard'),
      el('a', { href: 'agents.html' }, 'Agents'),
      el('a', { href: 'governance.html' }, 'Governance'),
      el('a', { href: 'settings.html' }, 'Settings')));
}

/* ------------------------------------------------------------------ mount */
function observeReveals(root) {
  const targets = root.querySelectorAll('.reveal');
  if (!('IntersectionObserver' in window)) return;
  // Arm the hidden state only now that the observer exists to un-hide it.
  root.classList.add('reveal-ready');
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('shown');
        observer.unobserve(entry.target);
      }
    });
  }, { rootMargin: '0px 0px -12% 0px', threshold: 0.05 });
  targets.forEach((node) => observer.observe(node));
}

async function main() {
  const [configResult, agentsResult, dashboardResult, healthResult] = await Promise.allSettled([
    api.platform.configuration(),
    api.agents.organisation(),
    api.platform.dashboard(),
    api.platform.health(),
  ]);

  const platformConfig = configResult.status === 'fulfilled' ? configResult.value : null;
  const organisation = agentsResult.status === 'fulfilled' ? agentsResult.value : null;
  const dashboard = dashboardResult.status === 'fulfilled' ? dashboardResult.value : null;
  const health = healthResult.status === 'fulfilled' ? healthResult.value : null;

  const stages = platformConfig ? platformConfig.stages : [];
  const agentNames = {};
  if (organisation) {
    organisation.departments.forEach((department) => {
      department.agents.forEach((agent) => { agentNames[agent.id] = agent.name; });
    });
  }

  const roster = organisation
    ? organisation.departments.flatMap((d) => d.agents)
        .filter((a) => a.executes)
        .map((a) => ({ label: shortAgentLabel(a.name), initials: initials(a.name) }))
    : [];

  const stats = [];
  if (organisation) {
    stats.push([number(organisation.totals.agents), 'Registered agents']);
    stats.push([number(organisation.totals.departments), 'Departments']);
  }
  if (stages.length) stats.push([String(stages.length), 'Accountable pipeline stages']);
  if (dashboard) {
    stats.push([number(dashboard.summary.completed_workflows), 'Engagements completed here']);
  }

  const root = el('div', { class: 'lp' },
    Nav(),
    Hero(roster),
    StatStrip(stats),
    ProblemSection(),
    WorkforceSection(organisation),
    HowItWorksSection(stages),
    ResearchSection(dashboard ? dashboard.research : null),
    QualitySection(),
    MemorySection(),
    VisibilitySection(dashboard, organisation),
    CtaSection(),
    Footer(health));

  document.body.prepend(root);
  observeReveals(root);

  if (!platformConfig) {
    const note = document.getElementById('hero-note');
    if (note) {
      clear(note).append(
        'The platform behind this page is not responding, so live figures are omitted. ',
        el('a', { href: 'settings.html' }, 'Check the connection'), '.');
    }
  }
}

main().catch((error) => {
  console.error('[Intelligenzit] landing', error);
  document.body.prepend(el('div', { class: 'lp' },
    el('section', { class: 'lp-section' },
      el('div', { class: 'lp-inner' },
        el('h1', { class: 'lp-h1' }, 'Intelligenzit AI Workforce'),
        el('p', { class: 'lp-lede' },
          'This page could not finish loading. The platform interface is still available.'),
        Button('Open the platform', { variant: 'primary', href: 'dashboard.html' })))));
});
