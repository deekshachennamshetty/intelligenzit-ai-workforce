/* ==========================================================================
   The single HTTP boundary between the interface and the platform.
   Every network call in this application goes through `request` — no page or
   component calls fetch() directly. Errors are normalised here once, so every
   screen can render the same states without repeating error handling.
   ========================================================================== */

const BASE_KEY = 'intelligenzit.apiBase';
const KEY_KEY = 'intelligenzit.apiKey';

function readStore(key) {
  try { return window.localStorage.getItem(key) || ''; } catch { return ''; }
}
function writeStore(key, value) {
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch { /* storage unavailable — same-origin defaults still work */ }
}

export const config = {
  base() {
    const stored = readStore(BASE_KEY).trim();
    if (stored) return stored.replace(/\/+$/, '');
    return window.location.origin.startsWith('http')
      ? window.location.origin
      : 'http://127.0.0.1:8000';
  },
  setBase(value) { writeStore(BASE_KEY, (value || '').trim().replace(/\/+$/, '')); },
  key() { return readStore(KEY_KEY); },
  setKey(value) { writeStore(KEY_KEY, (value || '').trim()); },
  isDefaultBase() { return !readStore(BASE_KEY).trim(); },
};

/** Every failure the interface can encounter, with a message a person can act on. */
export class ApiError extends Error {
  constructor(kind, message, { status = 0, detail = '', path = '' } = {}) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.detail = detail;
    this.path = path;
  }

  get isOffline() { return this.kind === 'offline'; }
  get isNotFound() { return this.kind === 'not_found'; }
  get isConflict() { return this.kind === 'conflict'; }
}

const MESSAGES = {
  offline: 'The Intelligenzit platform is not responding. Confirm the service is running '
         + 'and that the API address in Settings is correct.',
  timeout: 'The platform took too long to respond. It may be under load — try again.',
  unauthorised: 'Your session has expired or you are not signed in. Sign in again to continue.',
  forbidden: 'This action is not permitted.',
  not_found: 'That record does not exist on this platform.',
  invalid: 'The request was rejected as invalid.',
  conflict: 'That is not possible in the current state.',
  unprocessable: 'The details submitted were incomplete or in the wrong format.',
  rate_limited: 'Too many requests. Wait a moment and try again.',
  server: 'The platform could not complete this request. The failure has been recorded '
        + 'in its audit log.',
  malformed: 'The platform returned a response this interface could not read.',
};

function kindFor(status) {
  if (status === 401) return 'unauthorised';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409) return 'conflict';
  if (status === 422) return 'unprocessable';
  if (status === 429) return 'rate_limited';
  if (status === 400) return 'invalid';
  return 'server';
}

/* Pages that are public: the sign-in page itself (nothing to bounce from),
   and the landing page, which renders for anonymous visitors and only omits
   the live, authenticated figures it optionally tries to fetch. A 401 from
   either must never trigger a redirect. */
const PUBLIC_PAGES = new Set(['login.html', '', 'index.html']);

/** An expired or missing session sends the operator to the sign-in page —
    except from a public page, which handles a 401 by omitting data instead. */
function redirectToLogin() {
  writeStore(KEY_KEY, '');
  const here = window.location.pathname.split('/').pop() || '';
  if (PUBLIC_PAGES.has(here)) return;
  const next = /^[A-Za-z0-9_-]+\.html$/.test(here) ? `${here}${window.location.search}` : '';
  window.location.assign(next ? `login.html?next=${encodeURIComponent(next)}` : 'login.html');
}

const DEFAULT_TIMEOUT = 20000;
const inflight = new Map();

export async function request(path, {
  method = 'GET', body = null, signal = null, timeout = DEFAULT_TIMEOUT, dedupe = true,
} = {}) {
  const url = `${config.base()}${path}`;
  const key = `${method} ${url}`;

  // Identical concurrent reads share one round trip. Screens can therefore ask
  // for what they need without coordinating with one another.
  if (method === 'GET' && dedupe && inflight.has(key)) return inflight.get(key);

  const promise = execute(url, path, { method, body, signal, timeout });
  if (method === 'GET' && dedupe) {
    inflight.set(key, promise);
    promise.finally(() => inflight.delete(key)).catch(() => {});
  }
  return promise;
}

async function execute(url, path, { method, body, signal, timeout }) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort('timeout'), timeout);
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  const headers = { Accept: 'application/json' };
  if (body) headers['Content-Type'] = 'application/json';
  const apiKey = config.key();
  if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

  let response;
  try {
    response = await fetch(url, {
      method, headers, signal: controller.signal,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    if (controller.signal.reason === 'timeout') {
      throw new ApiError('timeout', MESSAGES.timeout, { path });
    }
    if (error && error.name === 'AbortError') throw error;
    throw new ApiError('offline', MESSAGES.offline, { path });
  } finally {
    window.clearTimeout(timer);
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      throw new ApiError('malformed', MESSAGES.malformed, { status: response.status, path });
    }
  }

  if (response.status === 401 && !path.startsWith('/api/auth/login')) redirectToLogin();

  if (!response.ok) {
    const kind = kindFor(response.status);
    const detail = payload && typeof payload.detail === 'string' ? payload.detail : '';
    const validation = !detail && payload && Array.isArray(payload.detail)
      ? payload.detail.map((d) => d.msg).filter(Boolean).join('; ')
      : '';
    throw new ApiError(kind, detail || validation || MESSAGES[kind] || MESSAGES.server, {
      status: response.status, detail: detail || validation, path,
    });
  }
  return payload;
}

export const get = (path, options) => request(path, { ...options, method: 'GET' });
export const post = (path, body, options) => request(path, { ...options, method: 'POST', body });

/** Small TTL cache for values that barely change within a page's lifetime. */
const cache = new Map();
export async function cached(path, ttlMs = 30000) {
  const entry = cache.get(path);
  if (entry && Date.now() - entry.at < ttlMs) return entry.value;
  const value = await get(path);
  cache.set(path, { value, at: Date.now() });
  return value;
}
export function clearCache() { cache.clear(); }
