/* Permissions, review gates, containment, audit and security posture. */
import { get } from './client.js';

export const overview = () => get('/api/governance');
