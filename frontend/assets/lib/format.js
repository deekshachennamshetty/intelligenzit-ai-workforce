/* Presentation helpers. Pure functions — no DOM, no fetching. */

export function duration(ms) {
  if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return '—';
  const value = Number(ms);
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} s`;
  const minutes = Math.floor(value / 60000);
  const seconds = Math.round((value % 60000) / 1000);
  if (minutes < 60) return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}

export function elapsedSince(iso) {
  if (!iso) return null;
  const started = new Date(iso).getTime();
  if (Number.isNaN(started)) return null;
  return Math.max(0, Date.now() - started);
}

export function when(iso, { withYear = false } = {}) {
  const date = toDate(iso);
  if (!date) return '—';
  return date.toLocaleString(undefined, {
    month: 'short', day: 'numeric', ...(withYear ? { year: 'numeric' } : {}),
    hour: '2-digit', minute: '2-digit',
  });
}

export function day(iso) {
  const date = toDate(iso);
  if (!date) return '—';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function clock(iso) {
  const date = toDate(iso);
  if (!date) return '';
  return date.toLocaleTimeString(undefined, { hour12: false });
}

export function relative(iso) {
  const date = toDate(iso);
  if (!date) return 'never';
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
  if (seconds < 86400) return `${Math.max(1, Math.round(seconds / 3600))} h ago`;
  if (seconds < 604800) return `${Math.max(1, Math.round(seconds / 86400))} d ago`;
  return day(iso);
}

function toDate(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/* Identifiers carry acronyms the naive title-caser would mangle ("ai_executive"
   must not render as "Ai Executive"). */
const ACRONYMS = new Set(['ai', 'qa', 'hr', 'okr', 'kpi', 'roi', 'api', 'ux', 'ui', 'llm',
                          'sla', 'crm', 'sap', 'etl', 'cors', 'tls', 'id']);

export function titleise(value) {
  return String(value || '')
    .replace(/[_-]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => (ACRONYMS.has(word.toLowerCase())
      ? word.toUpperCase()
      : word.charAt(0).toUpperCase() + word.slice(1)))
    .join(' ')
    .trim();
}

export function sentence(value) {
  const text = String(value || '').replace(/[_-]+/g, ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
}

export function count(value, singular, plural) {
  const number = Number(value) || 0;
  return `${number.toLocaleString()} ${number === 1 ? singular : (plural || `${singular}s`)}`;
}

export function number(value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? fallback : parsed.toLocaleString();
}

export function percent(value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? fallback : `${parsed}%`;
}

export function truncate(text, limit = 120) {
  const value = String(text || '');
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}

/* Maps every backend status/decision onto one of the design system's tones. */
const TONES = {
  completed: 'good', approved: 'good', answered: 'good', ok: 'good', healthy: 'good',
  enforced: 'good', enabled: 'good', connected: 'good', high: 'good', recorded: 'good',
  running: 'info', queued: 'info', working: 'info', partial: 'info', moderate: 'info',
  stated: 'info', in_progress: 'info',
  awaiting_approval: 'warning', approved_with_conditions: 'warning', rework: 'serious',
  escalated: 'serious', degraded: 'warning', permissive: 'warning', conditional: 'warning',
  low: 'warning', medium: 'warning',
  failed: 'critical', rejected: 'critical', error: 'critical', denied: 'critical',
  unanswered: 'critical', critical: 'critical',
  pending: '', skipped: '', idle: '', standby: '', not_configured: '', not_implemented: '',
};

export function tone(status) {
  const key = String(status || '').toLowerCase();
  return Object.prototype.hasOwnProperty.call(TONES, key) ? TONES[key] : '';
}

const STATUS_LABELS = {
  awaiting_approval: 'Awaiting sign-off',
  approved_with_conditions: 'Approved with conditions',
  not_configured: 'Not configured',
  not_implemented: 'Not implemented',
  research_followup: 'Research follow-up',
  review_followup: 'Review follow-up',
};

export function statusLabel(status) {
  const key = String(status || '').toLowerCase();
  return STATUS_LABELS[key] || titleise(status);
}

export function confidenceTone(value) {
  const score = Number(value) || 0;
  if (score >= 80) return 'good';
  if (score >= 60) return 'info';
  if (score >= 40) return 'warning';
  return 'critical';
}

export function initials(name) {
  const words = String(name || '').replace(/[^A-Za-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean);
  if (!words.length) return '??';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}
