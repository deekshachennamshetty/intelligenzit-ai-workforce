/* Agent presentation: avatar, row, card and the organisation chart. */
import { el, icon } from '../lib/dom.js';
import { initials, titleise, duration, relative } from '../lib/format.js';
import { Badge, StatusBadge, EmptyState } from './primitives.js';

export function AgentAvatar(agent, { size = '' } = {}) {
  const working = agent.status === 'working';
  return el('span', {
    class: ['avatar', `dept-${agent.department || 'crossfunctional'}`, size, working ? 'working' : '']
      .filter(Boolean).join(' '),
    'aria-hidden': 'true',
  }, initials(agent.name || agent.agent_id || '?'));
}

export function agentStatusBadge(agent) {
  if (agent.status === 'working') return StatusBadge('working', { label: 'Working' });
  if (agent.executes) return Badge('On path', { variant: '', dot: true });
  return Badge('Standby', { variant: '', dot: true });
}

export function AgentRow(agent, { onSelect }) {
  return el('button', {
    class: 'agent-row', type: 'button',
    'aria-label': `${agent.name}, ${agent.title}`,
    onclick: () => onSelect(agent),
  },
    AgentAvatar(agent),
    el('div', { class: 'grow' },
      el('div', { class: 'nm' }, agent.name),
      el('div', { class: 'tt' }, agent.title)),
    el('div', { class: 'row tight' },
      agent.runs ? Badge(`${agent.runs} run${agent.runs === 1 ? '' : 's'}`) : null,
      agentStatusBadge(agent)));
}

export function DepartmentCard(department, { onSelect }) {
  return el('div', { class: 'dept-card' },
    el('div', { class: 'dept-head' },
      el('div', { class: 'spread' },
        el('h3', {}, department.name),
        Badge(`${department.agents.length} agents`)),
      el('p', {}, department.mandate)),
    ...department.agents.map((agent) => AgentRow(agent, { onSelect })));
}

/** Departments, then the agents that actually execute inside the graph — laid
    out around the pipeline's real entry point rather than an invented
    executive/manager tier. `agent.node` is the LangGraph node id the backend
    reports for that agent (null for a registered-but-not-executing role). */
export function OrgChart(departments, { onSelect = null } = {}) {
  if (!departments || !departments.length) {
    return EmptyState('Organisation unavailable', 'The agent registry could not be read.');
  }
  const executing = departments.flatMap((d) => d.agents).filter((a) => a.executes);
  const entry = executing.find((a) => a.node === 'orchestrate') || null;

  const node = (role, name, variant = '') => el('div', { class: `org-node ${variant}` },
    el('div', { class: 'role' }, role), el('div', { class: 'nm' }, name));

  return el('div', { class: 'orgchart' },
    entry ? node('Pipeline entry point', entry.name, 'executive') : null,
    entry ? el('div', { class: 'org-connector', 'aria-hidden': 'true' }) : null,
    el('div', { class: 'org-level' },
      ...departments.map((department) => el('div', { class: 'org-node' },
        el('div', { class: 'role' },
          `${department.agents.length} agent${department.agents.length === 1 ? '' : 's'}`),
        el('div', { class: 'nm' }, department.name)))),
    el('div', { class: 'org-connector', 'aria-hidden': 'true' }),
    el('div', { class: 'org-level' },
      ...executing.map((agent) =>
        el('button', {
          class: 'org-node', type: 'button',
          style: 'cursor:pointer;font:inherit;color:inherit',
          onclick: onSelect ? () => onSelect(agent) : null,
        },
          el('div', { class: 'role' }, titleise(agent.department)),
          el('div', { class: 'nm' }, agent.name)))));
}

export function AgentStatLine(stats) {
  return el('div', { class: 'grid k3' },
    el('div', { class: 'tile' },
      el('div', { class: 'tile-label' }, 'Executions'),
      el('div', { class: 'tile-value' }, String(stats.runs || 0))),
    el('div', { class: 'tile' },
      el('div', { class: 'tile-label' }, 'Mean duration'),
      el('div', { class: 'tile-value' }, stats.avg_duration_ms ? duration(stats.avg_duration_ms) : '—')),
    el('div', { class: 'tile' },
      el('div', { class: 'tile-label' }, 'Reliability'),
      el('div', { class: 'tile-value' },
        stats.reliability === null || stats.reliability === undefined ? '—' : `${stats.reliability}%`),
      el('div', { class: 'tile-foot' }, stats.last_active ? `Last active ${relative(stats.last_active)}` : 'Never run')));
}
