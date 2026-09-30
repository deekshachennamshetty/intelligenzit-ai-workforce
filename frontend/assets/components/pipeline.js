/* Pipeline, Timeline and activity Feed.
   The pipeline renders whatever the backend reports; parallel branches are
   grouped from the stage metadata the platform publishes in /api/config, so
   concurrency is shown because the backend declares it, not because the
   interface assumes it. */

import { el, icon, clear } from '../lib/dom.js';
import { duration, clock, titleise, statusLabel } from '../lib/format.js';
import { EmptyState } from './primitives.js';

const MARK = { completed: '✓', failed: '!', running: '', skipped: '–' };

function stageMark(index, status) {
  if (status === 'running') return icon('clock', { size: 13 });
  return MARK[status] !== undefined && MARK[status] !== '' ? MARK[status] : String(index + 1);
}

function stageMeta(stage) {
  if (stage.status === 'completed') return duration(stage.duration_ms);
  if (stage.status === 'running') return 'running…';
  if (stage.status === 'failed') return 'failed';
  if (stage.status === 'skipped') return 'not reached';
  return '';
}

function StageRow(stage, index, { onSelect, meta }) {
  const info = meta[stage.stage_id] || {};
  const rework = (stage.attempt || 0) > 1;
  const detail = [
    titleise(stage.agent_id || info.agent || ''),
    rework ? `attempt ${stage.attempt}` : '',
    stage.detail || '',
  ].filter(Boolean).join(' · ');

  const attrs = {
    class: `pstage ${stage.status}${rework && stage.status === 'pending' ? ' rework' : ''}`,
  };
  const children = [
    el('div', { class: 'mark', 'aria-hidden': 'true' }, stageMark(index, stage.status)),
    el('div', { class: 'grow' },
      el('div', { class: 'stage-name' }, stage.label),
      el('div', { class: 'stage-who' }, detail)),
    el('div', { class: 'stage-meta' }, stageMeta(stage)),
  ];

  if (!onSelect) return el('div', attrs, children);
  return el('button', {
    ...attrs,
    class: `${attrs.class} clickable`,
    type: 'button',
    style: 'text-align:left;background:none;border:0;font:inherit;color:inherit;cursor:pointer;width:100%',
    'aria-label': `${stage.label}: ${statusLabel(stage.status)}`,
    onclick: () => onSelect(stage),
  }, children);
}

export function Pipeline(stages, { meta = {}, onSelect = null } = {}) {
  const host = el('div', { class: 'pipeline' });
  if (!stages || !stages.length) {
    host.append(EmptyState('No pipeline yet', 'Stages appear as soon as the engagement starts.'));
    return host;
  }

  let index = 0;
  while (index < stages.length) {
    const stage = stages[index];
    const group = (meta[stage.stage_id] || {}).parallel_group;
    if (!group) {
      host.append(StageRow(stage, index, { onSelect, meta }));
      index += 1;
      continue;
    }
    const members = [];
    while (index < stages.length
           && (meta[stages[index].stage_id] || {}).parallel_group === group) {
      members.push({ stage: stages[index], index });
      index += 1;
    }
    const running = members.filter((m) => m.stage.status === 'running').length;
    host.append(el('div', { class: 'parallel-group' },
      el('span', { class: 'parallel-label' },
        `${members.length} agents in parallel${running > 1 ? ` · ${running} running now` : ''}`),
      ...members.map((m) => StageRow(m.stage, m.index, { onSelect, meta }))));
  }
  return host;
}

/* --------------------------------------------------------------- Timeline */
export function Timeline(items, { emptyMessage = 'Nothing recorded yet' } = {}) {
  if (!items || !items.length) return EmptyState(emptyMessage, '');
  return el('div', { class: 'timeline' },
    ...items.map((item) => el('div', { class: `tl-item ${item.status || ''}` },
      el('div', { class: 'tl-dot', 'aria-hidden': 'true' }),
      el('div', { class: 'grow' },
        el('div', { class: 'tl-title' }, item.label),
        item.description ? el('div', { class: 'tl-desc' }, item.description) : null,
        (item.at || item.detail) ? el('div', { class: 'tl-meta' },
          [item.at ? clock(item.at) : '', item.detail].filter(Boolean).join(' · ')) : null))));
}

/* ------------------------------------------------------------------- Feed */
export function Feed(events, { emptyMessage = 'Waiting for the first event…', max = 250 } = {}) {
  const host = el('div', { class: 'feed', role: 'log', 'aria-live': 'polite',
    'aria-label': 'Agent activity' });
  if (!events || !events.length) {
    host.append(el('div', { class: 'muted small', style: 'padding:var(--s3)' }, emptyMessage));
    return host;
  }
  for (const event of events.slice(-max)) {
    host.append(el('div', { class: `feed-row ${event.level || 'info'}` },
      el('span', { class: 'ts' }, clock(event.ts)),
      el('span', {},
        el('span', { class: 'msg' }, event.message),
        event.agent_id
          ? el('span', { class: 'who small' }, ` · ${titleise(event.agent_id)}`) : null)));
  }
  return host;
}

export function scrollFeedToEnd(feed) {
  if (!feed) return;
  // After layout, so the scroll height is real.
  window.requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
}

