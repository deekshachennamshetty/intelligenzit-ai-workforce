/* Research intelligence: the dossier for one engagement, and the index. */
import { get } from './client.js';

export const index = (limit = 50) => get(`/api/research?limit=${limit}`);
export const workspace = (requestId, options = {}) =>
  get(`/api/workflows/${encodeURIComponent(requestId)}/research`, options);
