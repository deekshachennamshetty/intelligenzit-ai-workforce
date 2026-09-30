/* Platform-level reads: health, configuration and the dashboard roll-up. */
import { get, cached, clearCache } from './client.js';

export const health = () => get('/health');
export const configuration = () => cached('/api/config', 60000);
export const dashboard = () => get('/api/dashboard');
export const audit = (limit = 200) => get(`/api/audit?limit=${limit}`);
export const graph = () => cached('/api/graph', 60000);
export const refresh = () => clearCache();
