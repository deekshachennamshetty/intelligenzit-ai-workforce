/* Organisational memory. */
import { get } from './client.js';

export const overview = (limit = 200) => get(`/api/memory?limit=${limit}`);
export const byKind = (kind, limit = 200) =>
  get(`/api/memory?limit=${limit}&kind=${encodeURIComponent(kind)}`);
export const recall = (query, limit = 20) =>
  get(`/api/memory?q=${encodeURIComponent(query)}&limit=${limit}`);
