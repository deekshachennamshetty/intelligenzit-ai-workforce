/* DataTable — sortable, keyboard-navigable, with its own empty state. */
import { el, clear } from '../lib/dom.js';
import { EmptyState } from './primitives.js';

/**
 * @param {Array} rows
 * @param {Array} columns  {key, label, align, sortable, width, render(row)}
 * @param {object} options {empty, onRowClick, rowKey, defaultSort, caption, compact}
 */
export function DataTable(rows, columns, {
  empty = null, onRowClick = null, rowKey = null, defaultSort = null,
  caption = '', compact = false,
} = {}) {
  const host = el('div', { class: 'table-wrap' });
  let sortKey = defaultSort ? defaultSort.key : null;
  let sortDir = defaultSort ? (defaultSort.dir || 'desc') : 'desc';

  const draw = () => {
    clear(host);
    let data = Array.from(rows || []);
    if (!data.length) {
      host.append(empty || EmptyState('Nothing to show', 'There are no records here yet.'));
      return;
    }
    if (sortKey) {
      const column = columns.find((c) => c.key === sortKey);
      const pick = column && column.sortValue ? column.sortValue : (row) => row[sortKey];
      data.sort((a, b) => {
        const left = pick(a); const right = pick(b);
        const numeric = typeof left === 'number' && typeof right === 'number';
        const comparison = numeric
          ? left - right
          : String(left ?? '').localeCompare(String(right ?? ''), undefined, { numeric: true });
        return sortDir === 'asc' ? comparison : -comparison;
      });
    }

    const table = el('table', { class: `data${compact ? ' compact' : ''}` });
    if (caption) table.append(el('caption', { class: 'visually-hidden' }, caption));
    const headCells = columns.map((column) => el('th', {
      class: [column.align === 'right' ? 'num' : '',
              column.sortable ? 'sortable' : ''].filter(Boolean).join(' '),
      scope: 'col',
      style: column.width ? `width:${column.width}` : null,
      'aria-sort': column.sortable && sortKey === column.key
        ? (sortDir === 'asc' ? 'ascending' : 'descending') : null,
      tabindex: column.sortable ? '0' : null,
      onclick: column.sortable ? () => {
        if (sortKey === column.key) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
        else { sortKey = column.key; sortDir = 'desc'; }
        draw();
      } : null,
      onkeydown: column.sortable ? (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.currentTarget.click();
        }
      } : null,
    }, column.label));
    table.append(el('thead', {}, el('tr', {}, headCells)));

    const body = el('tbody');
    data.forEach((row) => {
      const clickable = Boolean(onRowClick);
      const tr = el('tr', {
        class: clickable ? 'clickable' : '',
        tabindex: clickable ? '0' : null,
        onclick: clickable ? () => onRowClick(row) : null,
        onkeydown: clickable ? (event) => {
          if (event.key === 'Enter') { event.preventDefault(); onRowClick(row); }
        } : null,
        'data-key': rowKey ? rowKey(row) : null,
      }, ...columns.map((column) => el('td', {
        class: column.align === 'right' ? 'num' : (column.className || ''),
      }, column.render ? column.render(row) : (row[column.key] ?? '—'))));
      body.append(tr);
    });
    table.append(body);
    host.append(table);
  };

  draw();
  host.update = (nextRows) => { rows = nextRows; draw(); };
  return host;
}
