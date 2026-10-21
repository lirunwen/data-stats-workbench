/** 纯 SVG 图表：无第三方库、无外部资源，适配浏览器缩放与窗口尺寸。 */
import { compactMoney, escapeHtml, formatMoney, n0, toNumber } from './ui.js';

export const CATEGORY_COLORS = [
  '#2f6b5b', '#c39a51', '#cf795b', '#6e8fa3',
  '#9381ad', '#89985f', '#c17f95', '#a5a199',
];

export function colorAt(index) {
  return CATEGORY_COLORS[index % CATEGORY_COLORS.length];
}

let chartSequence = 0;

function niceTicks(min, max, count = 4) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0];
  if (max === min) return [min];
  const ticks = [];
  for (let index = 0; index <= count; index += 1) {
    ticks.push(min + ((max - min) * index) / count);
  }
  return ticks;
}

function compactAxis(value) {
  return compactMoney(value);
}

function createTooltip(container) {
  let tooltip = container.querySelector('.chart-tooltip');
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.className = 'chart-tooltip';
    container.appendChild(tooltip);
  }
  return tooltip;
}

/**
 * 近 12 个月收支趋势：分组柱 + 净额线。
 * @returns {() => void} cleanup
 */
export function renderTrendChart(container, data) {
  if (!container) return () => {};
  let ro = null;
  let lastWidth = 0;
  let destroyed = false;

  const draw = () => {
    if (destroyed) return;
    const width = Math.max(340, Math.round(container.clientWidth || 700));
    lastWidth = width;
    const height = 248;
    const margin = { top: 18, right: 14, bottom: 28, left: 48 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const values = Array.isArray(data) ? data : [];
    const maxValue = Math.max(0, ...values.flatMap((item) => [
      toNumber(item.income_cents), toNumber(item.expense_cents), toNumber(item.net_cents),
    ]));
    const minValue = Math.min(0, ...values.map((item) => toNumber(item.net_cents)));
    if (!values.length || maxValue === 0) {
      container.innerHTML = `<div class="empty-state" style="padding:42px 20px">${'<div class="empty-icon" style="width:42px;height:42px;border-radius:12px"><svg class="icon" style="width:20px;height:20px"><use href="#i-trend"></use></svg></div>'}<h3>还没有足够的趋势数据</h3><p>导入经营流水后，这里会自动生成月度收支趋势。</p></div>`;
      return;
    }
    const paddedMax = maxValue * 1.12;
    const paddedMin = minValue === 0 ? 0 : minValue * 1.15;
    const span = paddedMax - paddedMin || 1;
    const y = (value) => margin.top + plotH - ((value - paddedMin) / span) * plotH;
    const zeroY = y(0);
    const step = plotW / values.length;
    const barWidth = Math.max(5, Math.min(13, step * 0.23));
    const groupGap = 3;
    const ticks = niceTicks(paddedMin, paddedMax, 4);

    const grid = ticks.map((tick) => {
      const yy = y(tick);
      return `<line x1="${margin.left}" y1="${yy.toFixed(1)}" x2="${width - margin.right}" y2="${yy.toFixed(1)}" stroke="#edf0e9" stroke-width="1" ${tick === 0 ? 'stroke-dasharray="0"' : 'stroke-dasharray="3 4"'} />
        <text x="${margin.left - 9}" y="${(yy + 3.2).toFixed(1)}" text-anchor="end" fill="#9aa49d" font-size="8.5" font-family="Bahnschrift, sans-serif">${escapeHtml(compactAxis(tick))}</text>`;
    }).join('');

    const bars = values.map((item, index) => {
      const cx = margin.left + step * index + step / 2;
      const income = toNumber(item.income_cents);
      const expense = toNumber(item.expense_cents);
      const iy = y(income);
      const ey = y(expense);
      const ih = Math.max(0, zeroY - iy);
      const eh = Math.max(0, zeroY - ey);
      const ix = cx - barWidth - groupGap / 2;
      const ex = cx + groupGap / 2;
      return `
        <rect x="${ix.toFixed(1)}" y="${iy.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${ih.toFixed(1)}" rx="2.5" fill="url(#incomeGrad)" class="chart-bar" />
        <rect x="${ex.toFixed(1)}" y="${ey.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${eh.toFixed(1)}" rx="2.5" fill="url(#expenseGrad)" class="chart-bar" />`;
    }).join('');

    const points = values.map((item, index) => ({
      x: margin.left + step * index + step / 2,
      y: y(toNumber(item.net_cents)),
      value: toNumber(item.net_cents),
    }));
    const netLine = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
    const dots = points.map((point, index) => {
      const last = index === points.length - 1;
      return `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="${last ? 4 : 2.4}" fill="${last ? '#b68c43' : '#f8f5ed'}" stroke="#c39a51" stroke-width="${last ? 2 : 1.4}" />`;
    }).join('');

    const labels = values.map((item, index) => {
      const x = margin.left + step * index + step / 2;
      const isLast = index === values.length - 1;
      const month = Number(String(item.month || '').slice(5, 7)) || index + 1;
      return `<text x="${x.toFixed(1)}" y="${height - 7}" text-anchor="middle" fill="${isLast ? '#36584c' : '#9aa49d'}" font-size="8.5" font-weight="${isLast ? '600' : '400'}" font-family="Microsoft YaHei UI, sans-serif">${month}月</text>`;
    }).join('');

    const aria = values.map((item) => `${item.label}${item.year}年 收入${formatMoney(item.income_cents)} 支出${formatMoney(item.expense_cents)}`).join('；');

    container.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="近十二个月收支趋势。${escapeHtml(aria)}" preserveAspectRatio="xMidYMid meet">
        <defs>
          <linearGradient id="incomeGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#4e9d81"/><stop offset="1" stop-color="#2d705d"/>
          </linearGradient>
          <linearGradient id="expenseGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#e2977c"/><stop offset="1" stop-color="#cb7052"/>
          </linearGradient>
        </defs>
        ${grid}
        <rect class="chart-hover-band" x="0" y="${margin.top}" width="${step.toFixed(1)}" height="${plotH}" fill="#2f6b5b" opacity="0" rx="6" />
        ${bars}
        <path d="${netLine}" fill="none" stroke="#c39a51" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" opacity=".95" />
        ${dots}
        ${labels}
      </svg>
      <div class="chart-tooltip" role="status"></div>
    `;
    const tooltip = createTooltip(container);
    const band = container.querySelector('.chart-hover-band');
    const svg = container.querySelector('svg');
    let activeIndex = -1;

    const hide = () => {
      tooltip.classList.remove('visible');
      if (band) band.setAttribute('opacity', '0');
      activeIndex = -1;
    };

    const show = (index, clientX, clientY) => {
      if (index < 0 || index >= values.length) return;
      activeIndex = index;
      const item = values[index];
      if (band) {
        band.setAttribute('x', (margin.left + step * index + 1).toFixed(1));
        band.setAttribute('opacity', '.045');
      }
      tooltip.innerHTML = `
        <div class="tt-title">${escapeHtml(item.year)}年${Number(item.month.slice(5, 7))}月 · ${n0(item.count)} 笔</div>
        <div class="tt-row"><span><span class="dot" style="display:inline-block;background:#7bb59c"></span> 收入</span><strong>${formatMoney(item.income_cents)}</strong></div>
        <div class="tt-row"><span><span class="dot" style="display:inline-block;background:#df9278"></span> 支出</span><strong>${formatMoney(item.expense_cents)}</strong></div>
        <div class="tt-row"><span><span class="dot" style="display:inline-block;background:#c39a51"></span> 净额</span><strong style="color:${toNumber(item.net_cents) >= 0 ? '#e4ddc7' : '#f0b9a5'}">${formatMoney(item.net_cents)}</strong></div>
      `;
      const rect = container.getBoundingClientRect();
      let left = (clientX || rect.left + margin.left + step * index) - rect.left + 13;
      let top = (clientY || rect.top + 55) - rect.top - 14;
      left = Math.max(4, Math.min(left, rect.width - 164));
      top = Math.max(4, Math.min(top, rect.height - 112));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
      tooltip.classList.add('visible');
    };

    const onMove = (event) => {
      const rect = container.getBoundingClientRect();
      const scaleX = width / Math.max(1, rect.width);
      const localX = (event.clientX - rect.left) * scaleX;
      const raw = (localX - margin.left) / step;
      const index = Math.max(0, Math.min(values.length - 1, Math.floor(raw)));
      show(index, event.clientX, event.clientY);
    };
    const onLeave = () => hide();
    svg.addEventListener('pointermove', onMove);
    svg.addEventListener('pointerleave', onLeave);
    container._trendCleanup = () => {
      svg.removeEventListener('pointermove', onMove);
      svg.removeEventListener('pointerleave', onLeave);
    };
  };

  draw();
  if (typeof ResizeObserver !== 'undefined') {
    let frame = null;
    ro = new ResizeObserver(() => {
      if (Math.abs(container.clientWidth - lastWidth) < 2) return;
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(draw);
    });
    ro.observe(container);
  }
  return () => {
    destroyed = true;
    ro?.disconnect();
    container._trendCleanup?.();
    container._trendCleanup = null;
  };
}

/**
 * 环形构成图。
 */
export function renderDonut(container, items, { label = '支出合计', totalCents = 0 } = {}) {
  if (!container) return () => {};
  const size = 138;
  const center = size / 2;
  const radius = 52;
  const stroke = 15;
  const circumference = 2 * Math.PI * radius;
  const total = totalCents || items.reduce((sum, item) => sum + toNumber(item.amount_cents), 0);
  const empty = !items.length || total <= 0;
  let cumulative = 0;
  const segments = items.map((item, index) => {
    const share = total > 0 ? toNumber(item.amount_cents) / total : 0;
    const length = Math.max(0, share * circumference - 2.6);
    const offset = cumulative * circumference;
    cumulative += share;
    return `<circle class="donut-segment" data-index="${index}" cx="${center}" cy="${center}" r="${radius}"
      fill="none" stroke="${colorAt(index)}" stroke-width="${stroke}"
      stroke-dasharray="${length.toFixed(2)} ${(circumference - length).toFixed(2)}"
      stroke-dashoffset="${(-offset).toFixed(2)}" transform="rotate(-90 ${center} ${center})"
      style="cursor:pointer;transition:stroke-width .18s, opacity .18s" />`;
  }).join('');
  container.innerHTML = `
    <svg viewBox="0 0 ${size} ${size}" role="img" aria-label="${escapeHtml(label)}构成图">
      <circle cx="${center}" cy="${center}" r="${radius}" fill="none" stroke="#eff2ec" stroke-width="${stroke}" />
      ${segments}
    </svg>
    <div class="donut-center">
      <small>${escapeHtml(label)}</small>
      <strong>${empty ? '—' : `¥${compactMoney(total)}`}</strong>
      <span>${empty ? '暂无数据' : '按经营类别'}</span>
    </div>
    <div class="chart-tooltip" role="status"></div>
  `;
  const tooltip = container.querySelector('.chart-tooltip');
  let active = null;
  container.querySelectorAll('.donut-segment').forEach((segment) => {
    const index = Number(segment.dataset.index);
    const item = items[index];
    const show = (event) => {
      active = segment;
      segment.style.strokeWidth = `${stroke + 3}`;
      tooltip.innerHTML = `<div class="tt-title">${escapeHtml(item.name)}</div><div class="tt-row"><span>金额</span><strong>${formatMoney(item.amount_cents)}</strong></div><div class="tt-row"><span>占比</span><strong>${Number(item.share || 0).toFixed(1)}% · ${n0(item.count)} 笔</strong></div>`;
      const rect = container.getBoundingClientRect();
      tooltip.style.left = `${Math.max(4, Math.min(event.clientX - rect.left + 10, rect.width - 154))}px`;
      tooltip.style.top = `${Math.max(4, event.clientY - rect.top - 48)}px`;
      tooltip.classList.add('visible');
    };
    const hide = () => {
      active = null;
      segment.style.strokeWidth = `${stroke}`;
      tooltip.classList.remove('visible');
    };
    segment.addEventListener('pointerenter', show);
    segment.addEventListener('pointermove', show);
    segment.addEventListener('pointerleave', hide);
    segment.addEventListener('click', () => {
      if (typeof container._onSliceClick === 'function') container._onSliceClick(item);
    });
  });
  return () => {};
}

/**
 * KPI 迷你趋势线，只接受数字数组。
 */
export function sparkline(values, color = '#2f6b5b') {
  const width = 86;
  const height = 32;
  const numbers = (values || []).map((value) => toNumber(value));
  if (numbers.length < 2) return '';
  const max = Math.max(...numbers);
  const min = Math.min(...numbers);
  const span = max - min || 1;
  const padding = 2;
  const points = numbers.map((value, index) => {
    const x = padding + (index / (numbers.length - 1)) * (width - padding * 2);
    const y = height - padding - ((value - min) / span) * (height - padding * 2);
    return [x, y];
  });
  const line = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point[0].toFixed(1)},${point[1].toFixed(1)}`).join(' ');
  const area = `${line} L${(width - padding).toFixed(1)},${height} L${padding},${height} Z`;
  const id = `spark-${++chartSequence}`;
  const last = points[points.length - 1];
  return `
    <svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="${color}" stop-opacity=".18"/>
        <stop offset="1" stop-color="${color}" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="${area}" fill="url(#${id})" />
      <path d="${line}" fill="none" stroke="${color}" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round" />
      <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2" fill="${color}" />
    </svg>
  `;
}

export function sparklineDataFromTrend(trend, key) {
  return (trend || []).map((item) => item[key]);
}
