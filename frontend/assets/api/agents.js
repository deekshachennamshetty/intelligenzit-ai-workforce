/* The agent organisation. */
import { get, cached } from './client.js';

export const organisation = () => get('/api/agents');
export const profile = (agentId) => get(`/api/agents/${encodeURIComponent(agentId)}`);
export const topology = () => cached('/api/graph', 60000);
