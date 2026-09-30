/* Engagements: submission, live status, result and the human review point. */
import { get, post } from './client.js';

const id = (value) => encodeURIComponent(String(value || ''));

export const list = ({ limit = 100, offset = 0, status = '' } = {}) => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) params.set('status', status);
  return get(`/api/workflows?${params}`);
};

export const create = (payload) => post('/api/workflows', payload);
export const status = (requestId, afterEvent = 0, options = {}) =>
  get(`/api/workflows/${id(requestId)}/status?after_event=${afterEvent}`, options);
export const result = (requestId) => get(`/api/workflows/${id(requestId)}/result`);
export const detail = (requestId) => get(`/api/workflows/${id(requestId)}`);
export const decide = (requestId, payload) =>
  post(`/api/workflows/${id(requestId)}/decision`, payload);
