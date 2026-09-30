/* Organisational memory: what the workforce has learned and can reuse. */

import { el, icon, clear } from '../lib/dom.js';
import { titleise, when, relative, truncate, number } from '../lib/format.js';
import { api } from '../api/index.js';
import {
  page, Card, Button, Badge, EmptyState, Banner, MetricCard, DataTable, Tabs,
  ErrorState, Disclosure,
} from '../components/index.js';

/* The current backend writes one kind of entry: the final report from each
   completed engagement. This list is deliberately short rather than showing
   categories (objectives, decisions, insights, lessons) the platform does
   not yet classify memory into. */
const KINDS = [
  ['deliverable', 'Deliverables recorded'],
];

page({
  title: 'Organisational memory',
  breadcrumb: [{ label: 'Platform' }, { label: 'Memory' }],
  active: 'memory',
  loadingLabel: 'Reading organisational memory…',
}, async () => {
  const data = await api.memory.overview(300);
  const byKind = data.by_kind || {};
  const allEntries = data.entries || [];

  let filter = 'all';
  let query = '';
  let entries = allEntries;
  const resultSlot = el('div');
  const contextSlot = el('div');
  const hint = el('span', { class: 'hint' });

  const columns = [
    { key: 'kind', label: 'Kind', width: '140px',
      render: (entry) => Badge(titleise(entry.kind),
        { variant: entry.kind === 'lesson' ? 'serious'
          : entry.kind === 'insight' ? 'brand' : '' }) },
    { key: 'title', label: 'Topic', sortable: true,
      render: (entry) => el('span', { class: 'primary-cell' }, entry.title) },
    { key: 'content', label: 'Content',
      render: (entry) => el('span', { class: 'small' }, truncate(entry.content, 320)) },
    { key: 'request_id', label: 'Source workflow', width: '150px',
      render: (entry) => (entry.request_id
        ? el('a', { class: 'mono small',
            href: `workflow.html?id=${encodeURIComponent(entry.request_id)}` }, entry.request_id)
        : el('span', { class: 'muted' }, '—')) },
    { key: 'confidence', label: 'Relevance', align: 'right',
      render: (entry) => `${Math.round((entry.confidence || 0) * 100)}%` },
    { key: 'created_at', label: 'Created', sortable: true, width: '130px',
      render: (entry) => el('span', { class: 'small muted nowrap' }, when(entry.created_at)) },
  ];

  const draw = () => {
    const rows = entries.filter((entry) => filter === 'all' || entry.kind === filter);
    hint.textContent = `${rows.length} shown of ${data.total} stored`;
    clear(resultSlot).append(DataTable(rows, columns, {
      defaultSort: { key: 'created_at', dir: 'desc' },
      empty: query
        ? EmptyState('Nothing recalled for that query',
            'Recall is lexical overlap across stored entries. Try a broader term.',
            { iconName: 'search' })
        : EmptyState('Organisational memory is empty',
            'The AI Executive writes the final report here automatically when an engagement '
            + 'completes.',
            { action: Button('New workflow', { variant: 'primary', href: 'new-workflow.html' }),
              iconName: 'memory' }),
    }));
  };

  const search = el('input', {
    type: 'search', placeholder: 'Recall relevant prior context…',
    'aria-label': 'Search organisational memory', style: 'max-width:360px',
    onkeydown: (event) => { if (event.key === 'Enter') runSearch(); },
  });

  async function runSearch() {
    query = search.value.trim();
    clear(contextSlot);
    try {
      if (!query) {
        const fresh = await api.memory.overview(300);
        entries = fresh.entries || [];
      } else {
        const recalled = await api.memory.recall(query, 50);
        entries = recalled.entries || [];
        contextSlot.append(Banner('info', `Recall for “${query}”`,
          `${entries.length} entr${entries.length === 1 ? 'y' : 'ies'} matched. This is the same `
          + 'recall the AI Executive receives when a new objective arrives.'));
      }
      draw();
    } catch (error) {
      clear(resultSlot).append(ErrorState(error, { onRetry: runSearch }));
    }
  }

  const tabs = Tabs(
    [{ id: 'all', label: 'All', count: allEntries.length },
     ...KINDS.map(([id, label]) => ({ id, label: titleise(id), count: byKind[id] || 0 }))],
    { active: 'all', ariaLabel: 'Filter memory',
      onSelect: (id) => {
        filter = id;
        Array.from(tabs.children).forEach((button) =>
          button.setAttribute('aria-selected', String(button.id === `tab-${id}`)));
        draw();
      } });

  const recent = allEntries.slice(0, 5);

  const root = el('div', { class: 'stack' },
    el('div', { class: 'grid k5' },
      ...KINDS.map(([kind, label]) => MetricCard({
        label, value: number(byKind[kind] || 0),
      }))),
    Banner('info', 'What this layer is',
      'A persistent store of past engagement reports, written automatically when an engagement '
      + 'completes and recalled at the start of the next one. Recall is keyword overlap over the '
      + 'stored corpus, not embedding search — the interface is the seam a managed vector store '
      + 'drops into.'),
    el('div', { class: 'grid split' },
      Card({ title: 'Recent learnings', hint: 'newest first' },
        recent.length
          ? el('div', { class: 'stack tight' },
              ...recent.map((entry) => el('div', { class: 'card inset', style: 'padding:var(--s4)' },
                el('div', { class: 'spread' },
                  el('span', { class: 'strong small' }, entry.title),
                  Badge(titleise(entry.kind))),
                el('p', { class: 'small muted mt2 mb0' }, truncate(entry.content, 260)),
                el('div', { class: 'row tight mt2' },
                  el('span', { class: 'xs muted' }, relative(entry.created_at)),
                  entry.request_id
                    ? el('a', { class: 'xs',
                        href: `workflow.html?id=${encodeURIComponent(entry.request_id)}` },
                        'source engagement')
                    : null))))
          : EmptyState('Nothing learned yet', 'Complete an engagement to populate memory.')),
      Card({ title: 'How memory reaches the next engagement' },
        el('div', { class: 'timeline' },
          ...[
            ['Capture', 'When an engagement completes, its final report is written to memory '
              + 'automatically.'],
            ['Recall', 'A new objective is matched against the stored corpus before the AI '
              + 'Executive analyses it.'],
            ['Injection', 'Matching entries are handed to the executive as prior context, '
              + 'clearly framed as previous work rather than fact about the new client.'],
            ['Attribution', 'The engagement records exactly which entries it used; they are '
              + 'listed on its Overview tab.'],
          ].map(([title, body]) => el('div', { class: 'tl-item completed' },
            el('div', { class: 'tl-dot' }),
            el('div', {},
              el('div', { class: 'tl-title' }, title),
              el('div', { class: 'tl-desc' }, body))))))),
    Card({ title: 'Stored knowledge', hint },
      el('div', { class: 'row', style: 'margin-bottom:var(--s4)' },
        search,
        Button('Recall', { iconName: 'search', onClick: runSearch }),
        Button('Clear', { variant: 'ghost', onClick: () => { search.value = ''; runSearch(); } })),
      contextSlot,
      tabs,
      el('div', { class: 'mt4' }, resultSlot)));

  draw();
  return root;
});
