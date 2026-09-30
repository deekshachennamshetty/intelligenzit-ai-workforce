/* ==========================================================================
   Chart primitives — inline SVG, no library.
   One shared tooltip, one axis scale, whole-number ticks, a legend whenever
   more than one series is present, and every mark hoverable.
   ========================================================================== */

import { el, svg, clear } from './dom.js';

let tipNode = null;
function tooltip() {
  if (!tipNode) { tipNode = el('div', { class: 'chart-tip', role: 'status' }); document.body.append(tipNode); }
  return tipNode;
}
function showTip(event, title, rows) {
  const tip = clear(tooltip());
  tip.append(el('div', { class: 't' }, title));
  for (const [label, value] of rows) {
    tip.append(el('div', { class: 'r' }, el('span', {}, label), el('strong', {}, String(value))));
  }
  tip.style.display = 'block';
  const rect = tip.getBoundingClientRect();
  let left = event.clientX + 14;
  if (left + rect.width > window.innerWidth - 8) left = event.clientX - rect.width - 14;
  let top = event.clientY - rect.height - 12;
  if (top < 8) top = event.clientY + 16;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${top}px`;
}
export function hideTip() { if (tipNode) tipNode.style.display = 'none'; }

function attachTip(node, title, rows) {
  node.classList.add('mark');
  node.addEventListener('mousemove', (event) => showTip(event, title, rows));
  node.addEventListener('mouseleave', hideTip);
  node.addEventListener('focus', (event) => {
    const box = event.target.getBoundingClientRect();
    showTip({ clientX: box.left + box.width / 2, clientY: box.top }, title, rows);
  });
  node.addEventListener('blur', hideTip);
  node.setAttribute('tabindex', '0');
  node.setAttribute('role', 'img');
  node.setAttribute('aria-label',
    `${title}: ${rows.map(([l, v]) => `${l} ${v}`).join(', ')}`);
}

/** A count axis: whole-number steps, no repeated labels, minimal headroom. */
export function axisScale(rawMax, preferred = 4, { allowFractions = false } = {}) {
  const target = Math.max(allowFractions ? 0.001 : 1, Number(rawMax) || 0);
  const rough = target / preferred;
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(rough, 1e-9)));
  const normalised = rough / magnitude;
  const nice = normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10;
  const step = allowFractions ? nice * magnitude : Math.max(1, Math.round(nice * magnitude));
  const max = Math.ceil(target / step) * step;
  return { max, step, ticks: Math.max(1, Math.round(max / step)) };
}

function legend(series) {
  return el('div', { class: 'legend' },
    ...series.map((s) => el('span', { class: 'item' },
      el('span', { class: 'swatch', style: `background:${s.color}` }), s.label)));
}

function emptyChart(message, detail) {
  return el('div', { class: 'empty' },
    el('h3', {}, message),
    detail ? el('p', {}, detail) : null);
}

/* ------------------------------------------------------------ stacked bars */
export function stackedBars(container, data, series, {
  label = 'Value', xKey = 'day', emptyMessage = 'No data recorded yet',
  emptyDetail = 'This chart fills in once the platform has executed work.',
} = {}) {
  clear(container);
  if (!data || !data.length) { container.append(emptyChart(emptyMessage, emptyDetail)); return; }

  const width = 720, height = 230, padL = 38, padR = 12, padT = 12, padB = 30;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const totals = data.map((d) => series.reduce((sum, s) => sum + (Number(d[s.key]) || 0), 0));
  const scale = axisScale(Math.max(...totals), 4);
  const bandW = plotW / data.length;
  const barW = Math.max(3, Math.min(40, bandW * 0.6));

  const root = svg('svg', {
    viewBox: `0 0 ${width} ${height}`, role: 'img',
    'aria-label': `${label} over time, split by outcome`,
  });

  for (let t = 0; t <= scale.ticks; t += 1) {
    const value = (scale.max / scale.ticks) * t;
    const y = padT + plotH - (value / scale.max) * plotH;
    root.append(svg('line', { class: 'grid-line', x1: padL, x2: width - padR, y1: y, y2: y }));
    root.append(svg('text', { class: 'tick', x: padL - 8, y: y + 4, 'text-anchor': 'end' },
      String(Math.round(value))));
  }

  const labelEvery = Math.max(1, Math.ceil(data.length / 8));
  data.forEach((row, index) => {
    const x = padL + bandW * index + (bandW - barW) / 2;
    let cursor = padT + plotH;
    series.forEach((s) => {
      const value = Number(row[s.key]) || 0;
      if (!value) return;
      const h = (value / scale.max) * plotH;
      cursor -= h;
      const rect = svg('rect', {
        x, y: cursor, width: barW, height: Math.max(2, h - 2), rx: 3,
        fill: s.color, stroke: 'var(--surface)', 'stroke-width': 2,
      });
      attachTip(rect, String(row[xKey]),
        series.map((ss) => [ss.label, Number(row[ss.key]) || 0]));
      root.append(rect);
    });
    if (index % labelEvery === 0 || index === data.length - 1) {
      root.append(svg('text', {
        class: 'tick', x: padL + bandW * index + bandW / 2, y: height - 10,
        'text-anchor': 'middle',
      }, String(row[xKey]).slice(5) || String(row[xKey])));
    }
  });

  root.append(svg('line', {
    class: 'baseline', x1: padL, x2: width - padR, y1: padT + plotH, y2: padT + plotH,
  }));
  container.append(el('div', { class: 'chart' }, root));
  if (series.length > 1) container.append(legend(series));
}

/* --------------------------------------------------------- horizontal bars */
export function horizontalBars(container, rows, {
  valueLabel = 'Value', format = (v) => String(v), color = 'var(--seq-4)',
  emptyMessage = 'Nothing recorded yet', emptyDetail = '', maxRows = 14,
} = {}) {
  clear(container);
  const data = (rows || []).slice(0, maxRows);
  if (!data.length) { container.append(emptyChart(emptyMessage, emptyDetail)); return; }

  const rowH = 30, padL = 168, padR = 66, width = 720;
  const height = data.length * rowH + 10;
  const max = Math.max(1, ...data.map((r) => Number(r.value) || 0));
  const plotW = width - padL - padR;

  const root = svg('svg', {
    viewBox: `0 0 ${width} ${height}`, role: 'img',
    'aria-label': `${valueLabel} by category`,
  });

  data.forEach((row, index) => {
    const y = index * rowH + 5;
    const value = Number(row.value) || 0;
    const w = Math.max(2, (value / max) * plotW);
    root.append(svg('text', {
      class: 'tick', x: padL - 10, y: y + 14, 'text-anchor': 'end',
    }, row.label.length > 26 ? `${row.label.slice(0, 25)}…` : row.label));
    const rect = svg('rect', {
      x: padL, y: y + 3, width: w, height: rowH - 12, rx: 3, fill: row.color || color,
    });
    attachTip(rect, row.label, [[valueLabel, format(value)], ...(row.extra || [])]);
    root.append(rect);
    root.append(svg('text', { class: 'val-label', x: padL + w + 8, y: y + 14 }, format(value)));
  });
  container.append(el('div', { class: 'chart' }, root));
}

/* ------------------------------------------------------------- histogram */
export function histogram(container, buckets, {
  emptyMessage = 'No scores recorded yet', emptyDetail = '', label = 'Engagements',
} = {}) {
  clear(container);
  const total = (buckets || []).reduce((sum, b) => sum + (b.count || 0), 0);
  if (!total) { container.append(emptyChart(emptyMessage, emptyDetail)); return; }

  const width = 520, height = 200, padL = 32, padR = 12, padT = 16, padB = 30;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const scale = axisScale(Math.max(...buckets.map((b) => b.count)), 2);
  const bandW = plotW / buckets.length;
  const barW = Math.min(58, bandW * 0.64);
  const shades = ['var(--seq-1)', 'var(--seq-2)', 'var(--seq-3)', 'var(--seq-4)', 'var(--seq-5)'];

  const root = svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img',
    'aria-label': `Distribution of ${label.toLowerCase()}` });

  for (let t = 0; t <= scale.ticks; t += 1) {
    const y = padT + plotH - (t / scale.ticks) * plotH;
    root.append(svg('line', { class: 'grid-line', x1: padL, x2: width - padR, y1: y, y2: y }));
    root.append(svg('text', { class: 'tick', x: padL - 8, y: y + 4, 'text-anchor': 'end' },
      String(Math.round((scale.max / scale.ticks) * t))));
  }

  buckets.forEach((bucket, index) => {
    const x = padL + bandW * index + (bandW - barW) / 2;
    const h = (bucket.count / scale.max) * plotH;
    if (bucket.count) {
      const rect = svg('rect', {
        x, y: padT + plotH - h, width: barW, height: Math.max(2, h), rx: 3,
        fill: shades[index] || 'var(--seq-4)',
      });
      attachTip(rect, bucket.bucket, [[label, bucket.count]]);
      root.append(rect);
      root.append(svg('text', {
        class: 'val-label', x: x + barW / 2, y: padT + plotH - h - 6, 'text-anchor': 'middle',
      }, String(bucket.count)));
    }
    root.append(svg('text', {
      class: 'tick', x: x + barW / 2, y: height - 10, 'text-anchor': 'middle',
    }, bucket.bucket));
  });
  root.append(svg('line', {
    class: 'baseline', x1: padL, x2: width - padR, y1: padT + plotH, y2: padT + plotH }));
  container.append(el('div', { class: 'chart' }, root));
}

/* ------------------------------------------------------------------ line */
export function lineSeries(container, points, {
  valueKey = 'value', labelKey = 'label', valueLabel = 'Value', color = 'var(--series-1)',
  max = 100, emptyMessage = 'No series recorded yet', emptyDetail = '', band = null,
} = {}) {
  clear(container);
  const data = points || [];
  if (data.length < 1) { container.append(emptyChart(emptyMessage, emptyDetail)); return; }

  const width = 720, height = 220, padL = 36, padR = 16, padT = 16, padB = 28;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const scale = axisScale(Math.max(max, ...data.map((d) => Number(d[valueKey]) || 0)), 4);
  const stepX = data.length > 1 ? plotW / (data.length - 1) : 0;
  const xOf = (i) => padL + (data.length > 1 ? stepX * i : plotW / 2);
  const yOf = (v) => padT + plotH - ((Number(v) || 0) / scale.max) * plotH;

  const root = svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img',
    'aria-label': `${valueLabel} across engagements` });

  for (let t = 0; t <= scale.ticks; t += 1) {
    const value = (scale.max / scale.ticks) * t;
    const y = yOf(value);
    root.append(svg('line', { class: 'grid-line', x1: padL, x2: width - padR, y1: y, y2: y }));
    root.append(svg('text', { class: 'tick', x: padL - 8, y: y + 4, 'text-anchor': 'end' },
      String(Math.round(value))));
  }

  // A reference threshold reads better as a marked line than a filled region,
  // which would otherwise dominate a chart with few points.
  if (band) {
    const y = yOf(band.from);
    root.append(svg('line', {
      x1: padL, x2: width - padR, y1: y, y2: y,
      stroke: 'var(--good)', 'stroke-width': 1.5, 'stroke-dasharray': '4 4',
      style: 'opacity:.7',
    }));
    root.append(svg('text', {
      class: 'tick', x: width - padR, y: y - 6, 'text-anchor': 'end',
      style: 'fill:var(--good-ink)',
    }, band.label || `target ${band.from}+`));
  }

  if (data.length > 1) {
    const path = data.map((d, i) => `${i ? 'L' : 'M'}${xOf(i)},${yOf(d[valueKey])}`).join(' ');
    root.append(svg('path', { d: path, fill: 'none', stroke: color, 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  }

  data.forEach((point, index) => {
    const dot = svg('circle', {
      cx: xOf(index), cy: yOf(point[valueKey]), r: 4.5, fill: color,
      stroke: 'var(--surface)', 'stroke-width': 2,
    });
    attachTip(dot, String(point[labelKey] || `#${index + 1}`),
      [[valueLabel, point[valueKey]]]);
    root.append(dot);
  });

  root.append(svg('line', {
    class: 'baseline', x1: padL, x2: width - padR, y1: padT + plotH, y2: padT + plotH }));
  container.append(el('div', { class: 'chart' }, root));
}

/* --------------------------------------------------------------- gauge */
export function gauge(container, value, { label = 'Confidence', max = 100 } = {}) {
  clear(container);
  const size = 132, stroke = 11, radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = Math.max(0, Math.min(1, (Number(value) || 0) / max));
  const colour = pct >= 0.8 ? 'var(--good)' : pct >= 0.6 ? 'var(--series-1)'
    : pct >= 0.4 ? 'var(--warning)' : 'var(--critical)';

  const root = svg('svg', {
    viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: 'img',
    'aria-label': `${label}: ${Math.round(pct * max)} out of ${max}`,
    style: 'max-width:132px',
  },
    svg('circle', { cx: size / 2, cy: size / 2, r: radius, fill: 'none',
      stroke: 'var(--surface-3)', 'stroke-width': stroke }),
    svg('circle', {
      cx: size / 2, cy: size / 2, r: radius, fill: 'none', stroke: colour,
      'stroke-width': stroke, 'stroke-linecap': 'round',
      'stroke-dasharray': `${circumference * pct} ${circumference}`,
      transform: `rotate(-90 ${size / 2} ${size / 2})`,
    }),
    svg('text', {
      x: size / 2, y: size / 2 + 2, 'text-anchor': 'middle',
      style: 'font-size:26px;font-weight:600;fill:var(--ink)',
    }, String(Math.round(pct * max))),
    svg('text', {
      x: size / 2, y: size / 2 + 22, 'text-anchor': 'middle',
      style: 'font-size:10px;fill:var(--ink-muted);letter-spacing:.08em',
    }, `/ ${max}`));

  container.append(el('div', { class: 'chart', style: 'display:grid;place-items:center' }, root));
}
