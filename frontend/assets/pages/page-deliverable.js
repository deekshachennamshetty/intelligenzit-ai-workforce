/* The final deliverable: the client-ready proposal, section by section, with
   the review outcome and executive decision that released it. */

import { el, icon, clear } from '../lib/dom.js';
import { titleise, when, duration, number, truncate } from '../lib/format.js';
import { api } from '../api/index.js';
import {
  page, Card, Button, Badge, StatusBadge, ConfidenceBadge, EmptyState, Banner,
  Prose, KeyValue, DataTable, Tabs, toast, copyText, Disclosure,
} from '../components/index.js';

const params = new URLSearchParams(window.location.search);
const requestId = (params.get('id') || '').trim();

const SECTIONS = [
  { id: 'summary', label: 'Overview' },
  { id: 'research', label: 'Research' },
  { id: 'requirements', label: 'Requirements' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'solution', label: 'Solution' },
  { id: 'review', label: 'Quality review' },
  { id: 'decision', label: 'Executive decision' },
  { id: 'document', label: 'Full document' },
];

page({
  title: 'Final deliverable',
  breadcrumb: [
    { label: 'Workflows', href: 'workflows.html' },
    { label: requestId || '—',
      href: requestId ? `workflow.html?id=${encodeURIComponent(requestId)}` : '' },
    { label: 'Deliverable' },
  ],
  active: 'workflows',
  loadingLabel: 'Assembling the deliverable…',
}, async () => {
  if (!requestId) {
    return EmptyState('No engagement selected',
      'Open a completed engagement from the workflow history to read its deliverable.',
      { action: Button('Browse workflows', { variant: 'primary', href: 'workflows.html' }),
        iconName: 'doc' });
  }

  const [result, researchWorkspace] = await Promise.all([
    api.workflows.result(requestId),
    api.research.workspace(requestId).catch(() => null),
  ]);

  const decision = result.executive_decision || {};
  const lastReview = (result.review_history || []).slice(-1)[0] || {};

  let active = 'summary';
  const panel = el('div', {
    id: 'panel-summary', role: 'tabpanel', 'aria-labelledby': 'tab-summary', tabindex: '0',
  });
  const tabsSlot = el('div');

  const downloadMarkdown = () => {
    const blob = new Blob([result.final_proposal || ''], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = el('a', { href: url, download: `intelligenzit-${requestId}.md` });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast('Markdown downloaded.', { variant: 'good' });
  };

  const copyDocument = async () => {
    const ok = await copyText(result.final_proposal || '');
    toast(ok ? 'Deliverable copied to the clipboard.' : 'Could not access the clipboard.',
      { variant: ok ? 'good' : 'error' });
  };

  function renderPanel() {
    panel.setAttribute('id', `panel-${active}`);
    panel.setAttribute('aria-labelledby', `tab-${active}`);
    clear(panel);

    const body = {
      summary: () => [
        Card({ title: 'Deliverable preview', hint: 'Report Agent',
               subtitle: 'The Report agent produces one complete document rather than separate '
                 + 'sections — see Full document for the whole text.' },
          result.final_proposal
            ? Prose(truncate(result.final_proposal, 1200), 'prose')
            : EmptyState('No deliverable was produced', 'This engagement did not reach the report stage.')),
      ],
      research: () => [
        researchWorkspace && researchWorkspace.available
          ? Card({
              title: 'Research position',
              hint: ConfidenceBadge(researchWorkspace.current.confidence),
              actions: Button('Open the research workspace', {
                size: 'sm', variant: 'ghost', iconName: 'research',
                href: `research.html?id=${encodeURIComponent(requestId)}`,
              }),
            },
              KeyValue([
                ['Questions',
                  `${researchWorkspace.current.coverage.answered} answered of `
                  + `${researchWorkspace.current.coverage.questions}`],
                ['Findings', String(researchWorkspace.current.coverage.findings)],
                ['Open gaps', String(researchWorkspace.current.coverage.gaps)],
                ['Rounds', String(researchWorkspace.attempts.length)],
              ]),
              (researchWorkspace.current.gaps || []).length
                ? Banner('warn', 'Evidence gaps carried into this deliverable',
                    el('ul', { style: 'margin:var(--s2) 0 0' },
                      ...researchWorkspace.current.gaps.map((gap) => el('li', {}, gap.gap))))
                : null)
          : null,
        Card({ title: 'Research findings', hint: 'Research Agent' },
          Prose(result.research || 'Not recorded.')),
      ],
      requirements: () => [Card({ title: 'Requirements', hint: 'Requirements Agent' },
        Prose(result.data_requirements || 'Not recorded.'))],
      analysis: () => [Card({ title: 'Analysis', hint: 'Analysis Agent' },
        Prose(result.analysis || 'Not recorded.'))],
      solution: () => [
        Card({ title: 'Recommended solution', hint: 'Solution Agent' },
          Prose(result.strategy || 'Not recorded.')),
      ],
      review: () => [
        Card({ title: 'Quality review',
               hint: `${(result.review_history || []).length} pass(es)` },
          (result.review_history || []).length
            ? el('div', { class: 'stack' },
                ...result.review_history.map((pass) => el('div', {
                  class: 'card inset', style: 'padding:var(--s4)',
                },
                  el('div', { class: 'spread' },
                    el('strong', {}, `Pass ${pass.attempt}`),
                    StatusBadge(pass.decision === 'rework' ? 'rework' : 'approved')),
                  pass.summary ? el('p', { class: 'mt3' }, pass.summary) : null,
                  Object.keys(pass.scores || {}).length
                    ? DataTable(Object.entries(pass.scores).map(([k, v]) => ({ k, v })), [
                        { key: 'k', label: 'Dimension', render: (r) => titleise(r.k) },
                        { key: 'v', label: 'Score', align: 'right', render: (r) => `${r.v}/10` },
                      ], { compact: true })
                    : null,
                  (pass.findings || []).length
                    ? el('div', {},
                        el('div', { class: 'section-title' }, 'Findings'),
                        el('ul', {}, ...pass.findings.map((f) => el('li', {}, f))))
                    : null)))
            : EmptyState('No review recorded', 'The Review/Critic agent did not run.')),
      ],
      decision: () => [
        Card({ title: 'Executive decision', hint: 'AI Executive' },
          el('div', { class: 'row' },
            StatusBadge(decision.decision || 'approved', { size: 'lg' }),
            decision.requires_human_signoff
              ? Badge('Human sign-off required', { variant: 'warning', dot: true }) : null),
          el('p', { class: 'mt3' }, decision.rationale || '—'),
          (decision.open_items || []).length
            ? el('div', {},
                el('div', { class: 'section-title' }, 'Open items'),
                el('ul', {}, ...decision.open_items.map((item) => el('li', {}, item))))
            : null,
          (decision.goal_alignment || []).length
            ? el('div', { class: 'mt5' },
                el('div', { class: 'section-title' },
                  `Success-criteria coverage — ${decision.criteria_addressed}/`
                  + `${decision.criteria_total} evidenced`),
                DataTable(decision.goal_alignment, [
                  { key: 'goal', label: 'Criterion' },
                  { key: 'addressed', label: 'Evidenced', align: 'right',
                    render: (row) => Badge(row.addressed ? 'Yes' : 'Not evidenced',
                      { variant: row.addressed ? 'good' : 'warning', dot: true }) },
                ], { compact: true }))
            : null,
          decision.human_signoff
            ? Banner('info',
                `Human ${decision.human_signoff.decision}d by `
                + `${decision.human_signoff.reviewer || 'reviewer'}`,
                decision.human_signoff.notes || 'No notes recorded.')
            : null),
      ],
      document: () => [
        Card({
          title: 'Complete deliverable',
          actions: el('div', { class: 'row tight' },
            Button('Copy', { size: 'sm', iconName: 'copy', onClick: copyDocument }),
            Button('Download Markdown', { size: 'sm', iconName: 'download',
              onClick: downloadMarkdown })),
        }, Prose(result.final_proposal || 'No deliverable was produced.', 'prose')),
      ],
    }[active];

    panel.append(...(body ? body().filter(Boolean) : []));
  }

  function renderTabs() {
    clear(tabsSlot).append(Tabs(SECTIONS, {
      active, ariaLabel: 'Deliverable sections',
      onSelect: (id) => { active = id; renderTabs(); renderPanel(); },
    }));
  }

  const root = el('div', { class: 'stack' },
    Card({},
      el('div', { class: 'spread top' },
        el('div', { class: 'grow' },
          el('div', { class: 'section-title' }, 'Business proposal'),
          el('h2', { style: 'font-size:var(--text-xl);line-height:1.35' }, result.objective),
          el('div', { class: 'row tight mt3 small muted' },
            el('span', { class: 'mono' }, result.request_id),
            el('span', {}, '·'),
            el('span', {}, `${duration(result.duration_ms)} to produce`),
            el('span', {}, '·'),
            el('span', {}, `${(result.review_history || []).length} review pass(es)`),
            result.rework_count ? el('span', {}, '·') : null,
            result.rework_count ? el('span', {}, `${result.rework_count} rework cycle(s)`) : null,
            result.engine ? el('span', {}, '·') : null,
            result.engine ? el('span', {}, `${titleise(result.engine)} engine`) : null)),
        el('div', { class: 'row tight' },
          StatusBadge(decision.decision || result.status, { size: 'lg' }))),
      el('div', { class: 'row mt5' },
        Button('Download Markdown', { variant: 'primary', iconName: 'download',
          onClick: downloadMarkdown }),
        Button('Copy', { iconName: 'copy', onClick: copyDocument }),
        Button('Back to workflow', { variant: 'ghost', iconName: 'chevronLeft',
          href: `workflow.html?id=${encodeURIComponent(requestId)}` }),
        Button('Research workspace', { variant: 'ghost', iconName: 'research',
          href: `research.html?id=${encodeURIComponent(requestId)}` }))),
    el('div', {}, tabsSlot, el('div', { class: 'mt4' }, panel)));

  renderTabs();
  renderPanel();
  return root;
});
