/* Analytics, all computed by the backend from recorded execution. */
import { get } from './client.js';

export const report = () => get('/api/metrics');
