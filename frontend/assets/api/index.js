/* One import surface for every page: `import { api } from '../api/index.js'`. */
import * as platform from './platform.js';
import * as workflows from './workflows.js';
import * as agents from './agents.js';
import * as research from './research.js';
import * as metrics from './metrics.js';
import * as memory from './memory.js';
import * as governance from './governance.js';
import * as auth from './auth.js';
import { ApiError, config } from './client.js';

export const api = { platform, workflows, agents, research, metrics, memory, governance, auth };
export { ApiError, config };
