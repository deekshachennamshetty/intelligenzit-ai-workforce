/* DOM helpers. Everything the interface renders is built through `el`, so no
   page ever concatenates HTML strings around untrusted values. */

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * Build an element.
 * @param {string} tag
 * @param {object} attrs  class, id, data and aria keys set as attributes; on* bound
 *                        as listeners; `html` sets innerHTML (only ever used
 *                        with values this codebase produced); `style` a string.
 * @param {...any} children  Nodes, strings, numbers, arrays; null/false skipped.
 */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  applyAttrs(node, attrs);
  append(node, children);
  return node;
}

export function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else {
      node.setAttribute(key, String(value));
    }
  }
  append(node, children);
  return node;
}

function applyAttrs(node, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'text') node.textContent = String(value);
    else if (key === 'style') node.setAttribute('style', value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, String(value));
  }
}

export function append(node, children) {
  for (const child of children.flat(4)) {
    if (child === null || child === undefined || child === false || child === '') continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function replace(node, ...children) {
  clear(node);
  append(node, children);
  return node;
}

export function escapeHtml(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ------------------------------------------------------------------ icons */
const ICONS = {
  dashboard: 'M3 12h4l2.5-7 4 14 2.5-7H21',
  plus: 'M12 5v14M5 12h14',
  workflows: 'M4 6h16M4 12h16M4 18h10',
  agents: 'M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM12 12l8-4.5M12 12v9M12 12L4 7.5',
  research: 'M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4.2-4.2',
  memory: 'M5 5h14v14H5zM9 9h6v6H9zM9 3v2M15 3v2M9 19v2M15 19v2M3 9h2M3 15h2M19 9h2M19 15h2',
  metrics: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  governance: 'M12 3l8 3.5v5c0 4.6-3.2 8.5-8 9.5-4.8-1-8-4.9-8-9.5v-5L12 3zM9 12l2 2 4-4',
  settings: 'M12 8.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7zM12 2.5v2.2M12 19.3v2.2M4.2 7l1.9 1.1M17.9 15.9l1.9 1.1M4.2 17l1.9-1.1M17.9 8.1l1.9-1.1',
  menu: 'M4 7h16M4 12h16M4 17h16',
  close: 'M6 6l12 12M18 6L6 18',
  theme: 'M12 3a9 9 0 109 9 7 7 0 01-9-9z',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5',
  download: 'M12 4v11M8 11l4 4 4-4M4 19h16',
  copy: 'M9 9h10v10H9zM5 15V5h10',
  refresh: 'M20 12a8 8 0 11-2.3-5.6M20 4v4h-4',
  chevron: 'M9 6l6 6-6 6',
  chevronLeft: 'M15 6l-6 6 6 6',
  arrowRight: 'M5 12h14M13 6l6 6-6 6',
  check: 'M4 12.5l5 5L20 6',
  alert: 'M12 8v5M12 16.5v.5M10.3 3.9L2.6 17.2A1.5 1.5 0 003.9 19.5h16.2a1.5 1.5 0 001.3-2.3L13.7 3.9a1.5 1.5 0 00-2.6 0z',
  info: 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 11v5M12 8v.5',
  clock: 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3.5 2',
  branch: 'M6 4v6a4 4 0 004 4h4M6 20V10M18 10a2 2 0 100-4 2 2 0 000 4zM6 22a2 2 0 100-4 2 2 0 000 4zM6 6a2 2 0 100-4 2 2 0 000 4z',
  spark: 'M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4L12 3z',
  shield: 'M12 3l8 3.5v5c0 4.6-3.2 8.5-8 9.5-4.8-1-8-4.9-8-9.5v-5L12 3z',
  inbox: 'M3 12h5l2 3h4l2-3h5M4.5 6h15l1.5 6v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6L4.5 6z',
  doc: 'M13 3H7a1 1 0 00-1 1v16a1 1 0 001 1h10a1 1 0 001-1V8l-5-5zM13 3v5h5',
  search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4.2-4.2',
  layers: 'M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17l9 5 9-5',
};

export function icon(name, { size = 18, stroke = 1.7, cls = '' } = {}) {
  const path = ICONS[name] || ICONS.info;
  return svg('svg', {
    viewBox: '0 0 24 24', width: size, height: size, fill: 'none',
    stroke: 'currentColor', 'stroke-width': stroke, 'stroke-linecap': 'round',
    'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false',
    class: cls,
  }, svg('path', { d: path }));
}

export function hasIcon(name) { return Boolean(ICONS[name]); }
