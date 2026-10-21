/** 经营总览页。 */
import { api } from '../api.js';
import { state, refreshApp } from '../store.js';
import {
  buildHash, confirmDialog, e, formatDateCN, formatDateShort, formatMoney, formatPct,
  greeting, icon, kpiMoneyHTML, n0, openModal, relativeTime,
  toast, weekdayCN,
} from '../ui.js';
import {
  renderDonut, renderTrendChart, sparkline, sparklineDataFromTrend, colorAt,
} from '../charts.js';

let preferredPreset = 'year';
let customRange = null;
let compositionDirection = 'expense';
let loadSequence = 0;
let trendCleanup = null;
let donutCleanup = null;
let currentContainer = null;
let pageSequence = 0;

const PERIODS = [
  { key: 'month', label: '本月' },
  { key: 'quarter', label: '本季' },
  { key: 'year', label: '本年' },
  { key: 'all', label: '全部' },
];

function periodParams() {
  if (preferredPreset === 'custom' && customRange) {
    return { period: 'custom', start: customRange.start, end: customRange.end };
  }
  return { period: preferredPreset };
}

function dateRangeText(period) {
  if (period.preset === 'all') return '全部历史数据';
  const start = period.start === '1990-01-01' ? '' : formatDateCN(period.start);
  return `${start || '最早'} — ${formatDateCN(period.end)}`;
}

function previousLabel(period) {
  return period?.previous_label || '较上一周期';
}

function deltaPill(kpi, { inverse = false } = {}) {
  const pct = kpi.change_pct;
  if (pct === null || pct === undefined) {
    return `<span class="change flat">无同期</span>`;
  }
  if (Math.abs(Number(pct)) < 0.05) {
    return `<span class="change flat">持平</span>`;
  }
  const up = Number(pct) > 0;
  const good = inverse ? !up : up;
  return `<span class="change ${up ? 'up' : 'down'} ${good ? 'good' : 'bad'}">
    ${icon(up ? 'arrow-up-right' : 'arrow-down-right')}${formatPct(pct)}
  </span>`;
}

function sparkFor(data, key, color) {
  if (!data.trend?.length) return '';
  const values = key === 'count'
    ? sparklineDataFromTrend(data.trend, 'count')
    : sparklineDataFromTrend(data.trend, key);
  return `<div class="kpi-spark">${sparkline(values, color)}</div>`;
}

function kpiCard(kpi, { iconName, tone, inverse = false, spark = '', extra = '' }) {
  const countKpi = kpi.key === 'count';
  const value = countKpi
    ? `<span class="number">${n0(kpi.value)}</span><span class="unit">笔</span>`
    : kpiMoneyHTML(kpi.value_cents);
  return `
    <article class="kpi-card kpi-${tone}">
      <div class="kpi-top">
        <span class="kpi-label">${e(kpi.label)}</span>
        <span class="kpi-icon">${icon(iconName)}</span>
      </div>
      <div class="kpi-value">${value}</div>
      <div class="kpi-bottom">
        ${deltaPill(kpi, { inverse })}
        <span>${countKpi ? '较上期笔数' : (kpi.change_pct === null ? '暂无同期数据' : '较上期')}</span>
        ${extra}
      </div>
      ${spark}
    </article>
  `;
}

function skeletonPage() {
  return `
    <div class="dash-skeleton">
      <div class="skeleton skeleton-heading"></div>
      <div class="skeleton-kpis">
        <div class="skeleton skeleton-kpi"></div><div class="skeleton skeleton-kpi"></div>
        <div class="skeleton skeleton-kpi"></div><div class="skeleton skeleton-kpi"></div>
      </div>
      <div class="skeleton-grid"><div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div></div>
    </div>`;
}

function dashboardHeading(data) {
  const serverDate = data.server_date || state.app?.server_date;
  const org = state.settings?.org_name || '';
  const customActive = preferredPreset === 'custom' && customRange;
  return `
    <div class="dashboard-heading">
      <div class="dashboard-greeting">
        <p class="eyebrow">${e(formatDateCN(serverDate))} · ${e(weekdayCN(serverDate))}</p>
        <h2>${greeting()}，今天的经营数据已就绪。</h2>
        <p class="head-sub">
          <span class="inline-dot"></span>
          <span>${org ? `${e(org)} · ` : ''}${e(data.period.label)}</span>
          <span class="text-faint">·</span>
          <span>${e(dateRangeText(data.period))}</span>
          <span class="text-faint">·</span>
          <span>数据仅存本机</span>
        </p>
      </div>
      <div class="heading-actions">
        <div class="period-switcher" role="group" aria-label="统计期间">
          ${PERIODS.map((item) => `
            <button type="button" data-period="${item.key}" class="${preferredPreset === item.key ? 'active' : ''}" aria-pressed="${preferredPreset === item.key}">${item.label}</button>
          `).join('')}
        </div>
        <button type="button" class="custom-period-btn ${customActive ? 'is-active' : ''}" data-action="custom-period" title="自定义统计期间">
          ${icon('calendar')}<span>${customActive ? `${formatDateShort(customRange.start)} 至 ${formatDateShort(customRange.end)}` : '自定义'}</span>
        </button>
      </div>
    </div>
  `;
}

function demoBanner(data) {
  if (!data.demo_count) return '';
  return `
    <div class="notice notice-demo demo-banner">
      ${icon('sparkles')}
      <div><strong>当前包含 ${n0(data.demo_count)} 条演示数据。</strong> 用于体验汇总与图表效果，不会上传到任何地方。</div>
      <button type="button" class="text-link notice-link" data-action="clear-demo">清除演示数据</button>
    </div>`;
}

function onboarding() {
  const serverDate = state.app?.server_date;
  return `
    <div class="dashboard-page">
      <div class="dashboard-heading">
        <div class="dashboard-greeting">
          <p class="eyebrow">${e(formatDateCN(serverDate))} · ${e(weekdayCN(serverDate))}</p>
          <h2>${greeting()}，从第一份经营数据开始。</h2>
          <p class="head-sub"><span class="inline-dot"></span><span>本地导入</span><span class="text-faint">·</span><span>自动汇总</span><span class="text-faint">·</span><span>数据不出本机</span></p>
        </div>
      </div>
      <section class="dashboard-empty">
        <div class="dashboard-empty-copy">
          <p class="eyebrow">欢迎使用衡策工作台</p>
          <h2>把每一笔经营，汇成一张清晰的图。</h2>
          <p>导入 Excel 台账或 Outlook 邮件中的表格，工作台会自动识别字段、校验数据、合并重复项，并把收入、支出与经营净额汇总成可读的图表。</p>
          <div class="empty-actions">
            <a class="btn btn-primary btn-lg" href="#/import">${icon('upload')}导入第一份数据</a>
            <button class="btn btn-secondary btn-lg" type="button" data-action="seed-demo">${icon('sparkles')}载入演示数据</button>
          </div>
          <div class="empty-meta">
            <div>${icon('check-circle')}自动识别表头与字段</div>
            <div>${icon('shield')}本地校验与判重</div>
            <div>${icon('trend')}实时汇总图表</div>
          </div>
        </div>
        <div class="empty-visual" aria-hidden="true">
          <div class="empty-float-card">
            <small>本月经营净额</small>
            <strong>¥ 428,600</strong>
            <span>↑ 12.8% 较上期</span>
          </div>
          <div class="empty-chart">
            <span style="height:34%"></span><span style="height:48%"></span><span style="height:41%"></span>
            <span style="height:63%"></span><span style="height:55%"></span><span style="height:76%"></span>
            <span style="height:68%"></span><span style="height:88%"></span>
          </div>
          <svg class="empty-chart-line" viewBox="0 0 400 100" fill="none" aria-hidden="true" preserveAspectRatio="none">
            <path d="M0 78 C40 74, 52 58, 86 61 S142 70, 178 47 S238 35, 268 43 S328 22, 400 12" stroke="#d9bd85" stroke-width="1.7" stroke-linecap="round" opacity=".8"/>
          </svg>
          <span class="empty-quote">LOCAL · PRIVATE · CLEAR</span>
        </div>
      </section>
    </div>`;
}

function rangeEmpty(data) {
  const customLabel = preferredPreset === 'custom' && customRange
    ? `${formatDateCN(customRange.start)} — ${formatDateCN(customRange.end)}`
    : `${data.period.label}（${dateRangeText(data.period)}）`;
  return `
    <div class="dashboard-page">
      ${dashboardHeading(data)}
      <div class="range-empty">
        <div class="empty-icon">${icon('calendar', 'icon-xl')}</div>
        <h2>这段时间还没有经营流水</h2>
        <p>当前查看的是 ${e(customLabel)}。系统里已有 ${n0(data.total_records)} 条历史数据，换个期间就能看到汇总。</p>
        <div class="actions">
          <button type="button" class="btn btn-primary" data-period="all">${icon('history')}查看全部数据</button>
          <a class="btn btn-secondary" href="#/import">${icon('upload')}导入数据</a>
        </div>
      </div>
    </div>`;
}

function trendSection(data) {
  const netTotal = data.trend.reduce((sum, item) => sum + Number(item.net_cents || 0), 0);
  const incomeTotal = data.trend.reduce((sum, item) => sum + Number(item.income_cents || 0), 0);
  const activeMonths = data.trend.filter((item) => item.count > 0).length || 1;
  return `
    <section class="card dash-card trend-card span-8">
      <header class="card-head">
        <div>
          <div class="card-title-row">${icon('trend')}<h3>收支趋势</h3></div>
          <p>近 12 个月 · 收入 / 支出 / 净额</p>
        </div>
        <div class="chart-legend" aria-label="图例">
          <span class="legend-item"><i class="legend-swatch income"></i>收入</span>
          <span class="legend-item"><i class="legend-swatch expense"></i>支出</span>
          <span class="legend-item"><i class="legend-swatch net"></i>净额</span>
        </div>
      </header>
      <div class="chart-wrap"><div class="trend-chart" id="trendChart"></div></div>
      <footer class="chart-foot">
        <span>近 12 个月净额<strong>${formatMoney(netTotal, { symbol: true })}</strong></span>
        <span>月均收入<strong>${formatMoney(Math.round(incomeTotal / activeMonths))}</strong></span>
        <span class="spacer"></span>
        <span>按业务日期统计</span>
      </footer>
    </section>`;
}

function qualitySection(data) {
  const quality = data.quality;
  const rate = Number(quality.classification_rate || 0);
  const uncategorized = Number(quality.uncategorized || 0);
  const noDepartment = Number(quality.no_department || 0);
  const invalid = Number(quality.recent_invalid || 0);
  const duplicates = Number(quality.recent_duplicate || 0);
  const largest = data.insight?.largest_record;
  const netMargin = Number(data.insight?.net_margin || 0);
  const periodQuery = data.period.start !== '1990-01-01'
    ? `start=${encodeURIComponent(data.period.start)}&end=${encodeURIComponent(data.period.end)}`
    : '';
  const categoryHref = `#/records?${periodQuery ? `${periodQuery}&` : ''}category=${encodeURIComponent('未分类')}`;
  const deptHref = `#/records?${periodQuery ? `${periodQuery}&` : ''}department=${encodeURIComponent('未指定')}`;
  const hasAlerts = uncategorized || noDepartment || invalid || duplicates;
  const items = [];
  if (uncategorized) {
    items.push(`
      <li class="quality-item"><span class="quality-dot"></span><p><strong>${n0(uncategorized)} 笔</strong> 未分类流水</p><a href="${categoryHref}">去处理</a></li>`);
  }
  if (noDepartment) {
    items.push(`
      <li class="quality-item"><span class="quality-dot grey"></span><p><strong>${n0(noDepartment)} 笔</strong> 未填写部门</p><a href="${deptHref}">查看</a></li>`);
  }
  if (invalid) {
    items.push(`
      <li class="quality-item"><span class="quality-dot coral"></span><p>近 30 天 <strong>${n0(invalid)} 行</strong> 导入异常</p><a href="#/history">查看</a></li>`);
  }
  if (duplicates) {
    items.push(`
      <li class="quality-item"><span class="quality-dot green"></span><p>识别 <strong>${n0(duplicates)} 条</strong> 重复数据</p><a href="#/history">记录</a></li>`);
  }
  return `
    <section class="card quality-card span-4">
      <header class="card-head">
        <div>
          <div class="card-title-row">${icon('shield')}<h3>数据质量</h3></div>
          <p>分类、部门与最近导入</p>
        </div>
        <div class="quality-score"><strong>${rate.toFixed(1)}%</strong><span>分类完整度</span></div>
      </header>
      <div class="quality-meter"><span style="width:${Math.max(0, Math.min(100, rate))}%"></span></div>
      <div class="quality-meter-label"><span>本期 ${n0(quality.records)} 笔流水</span><span>${uncategorized ? `${uncategorized} 笔待补类别` : '类别已补全'}</span></div>
      ${hasAlerts ? `<ul class="quality-list">${items.join('')}</ul>` : `
        <div class="quality-good">${icon('check-circle')}<span>本期数据分类与部门信息完整，没有发现需要处理的问题。</span></div>`}
      <div class="quality-summary">
        <div><span>经营净利率</span><strong>${netMargin.toFixed(1)}%</strong><small>净额 ÷ 收入</small></div>
        <div><span>本期最大单笔</span><strong>${largest ? formatMoney(largest.amount_cents) : '—'}</strong><small>${largest ? `${e(largest.category || '未分类')} · ${e(largest.department || '未指定')}` : '本期暂无记录'}</small></div>
      </div>
      <div class="quality-foot">
        <a class="link-arrow" href="#/history">查看导入记录 ${icon('arrow-right')}</a>
      </div>
    </section>`;
}

function compositionSection(data) {
  const direction = compositionDirection === 'expense' ? 'expense' : 'income';
  const items = data.composition[direction] || [];
  const total = data.composition[`${direction}_total_cents`] || 0;
  const title = direction === 'expense' ? '支出构成' : '收入构成';
  const legend = items.length ? items.map((item, index) => `
    <button type="button" class="comp-legend-item" data-category="${e(item.name)}" data-direction="${direction}">
      <i class="dot" style="background:${colorAt(index)}"></i>
      <span class="name">${e(item.name)}</span>
      <span class="pct">${Number(item.share || 0).toFixed(1)}%</span>
    </button>`).join('') : '';
  return `
    <section class="card composition-card span-4" id="compositionCard">
      <header class="card-head">
        <div>
          <div class="card-title-row">${icon('pie')}<h3>${title}</h3></div>
          <p>${data.period.label} · 按经营类别</p>
        </div>
        <div class="segmented segmented-sm comp-toggle" role="group" aria-label="构成方向">
          <button type="button" data-comp="expense" class="${direction === 'expense' ? 'active' : ''}">支出</button>
          <button type="button" data-comp="income" class="${direction === 'income' ? 'active' : ''}">收入</button>
        </div>
      </header>
      ${items.length ? `
        <div class="donut-area">
          <div class="donut-chart" id="compositionDonut"></div>
          <div class="comp-legend">${legend}</div>
        </div>
        <div class="comp-foot"><span>共 ${n0(items.reduce((sum, item) => sum + Number(item.count || 0), 0))} 笔</span><span>点击类别查看明细</span></div>
      ` : `<div class="empty-state" style="padding:42px 10px 30px">
        <div class="empty-icon" style="width:40px;height:40px;border-radius:11px">${icon('pie')}</div>
        <h3>本期暂无${direction === 'expense' ? '支出' : '收入'}数据</h3>
        <p>换个期间或导入流水后再查看构成。</p>
      </div>`}
    </section>`;
}

function departmentSection(data) {
  const departments = (data.departments || []).slice(0, 5);
  const totalHeights = departments.map((item) => Number(item.income_cents || 0) + Number(item.expense_cents || 0));
  const maxTotal = Math.max(1, ...totalHeights);
  const rows = departments.map((item) => {
    const total = Number(item.income_cents || 0) + Number(item.expense_cents || 0);
    const incomeWidth = Math.max(0, (Number(item.income_cents || 0) / maxTotal) * 100);
    const expenseWidth = Math.max(0, (Number(item.expense_cents || 0) / maxTotal) * 100);
    return `
      <button type="button" class="dept-row" data-department="${e(item.name)}">
        <span class="dept-name">${e(item.name)}</span>
        <span class="dept-bar" title="收入 ${formatMoney(item.income_cents)} · 支出 ${formatMoney(item.expense_cents)}">
          <span class="in" style="width:${incomeWidth.toFixed(1)}%"></span><span class="out" style="width:${expenseWidth.toFixed(1)}%"></span>
        </span>
        <span class="dept-net">${formatMoney(item.net_cents)}<small>净额</small></span>
      </button>`;
  }).join('');
  return `
    <section class="card dept-card span-4">
      <header class="card-head">
        <div>
          <div class="card-title-row">${icon('building')}<h3>部门表现</h3></div>
          <p>${data.period.label} · 按收支总额排序</p>
        </div>
        <a class="link-arrow" href="#/records">明细 ${icon('arrow-right')}</a>
      </header>
      ${rows ? `<div class="dept-list">${rows}</div>
        <div class="chart-foot" style="margin-top:17px;padding-top:11px">
          <span><i class="legend-swatch income" style="display:inline-block;margin-right:4px"></i>收入</span>
          <span><i class="legend-swatch expense" style="display:inline-block;margin-right:4px"></i>支出</span>
          <span class="spacer"></span><span>前 ${n0(departments.length)} 个部门</span>
        </div>` : `<div class="empty-state" style="padding:42px 10px 30px">
          <div class="empty-icon" style="width:40px;height:40px;border-radius:11px">${icon('building')}</div>
          <h3>本期暂无部门数据</h3>
          <p>补充部门字段后，可以看到各部门的经营贡献。</p>
        </div>`}
    </section>`;
}

function recentSection(data) {
  const batches = data.recent_batches || [];
  const rows = batches.slice(0, 4).map((batch) => {
    const kind = String(batch.first_file || '').toLowerCase().match(/\.(msg|eml|oft)$/) ? 'mail' : 'file-xls';
    const primary = batch.imported_rows > 0 ? `+${n0(batch.imported_rows)}` : (batch.updated_rows > 0 ? `${n0(batch.updated_rows)}` : '0');
    let secondary = batch.imported_rows > 0 ? '新增记录' : '已完成';
    if (batch.updated_rows > 0 && batch.imported_rows === 0) secondary = '更新记录';
    if (batch.duplicate_rows > 0 && batch.imported_rows === 0) secondary = `重复 ${n0(batch.duplicate_rows)}`;
    const warning = batch.invalid_rows > 0
      ? `<span class="recent-warn">${n0(batch.invalid_rows)} 行异常</span>`
      : `<span>${secondary}</span>`;
    return `
      <a class="recent-item" href="#/history?open=${encodeURIComponent(batch.id)}">
        <span class="recent-icon ${kind === 'mail' ? 'mail' : ''}">${icon(kind === 'mail' ? 'mail' : 'file-xls')}</span>
        <span class="recent-main">
          <strong>${e(batch.first_file || '批量导入')}</strong>
          <span>${e(relativeTime(batch.created_at))}${batch.is_demo ? ' · 演示' : ''}</span>
        </span>
        <span class="recent-count">
          <strong>${primary}</strong>
          ${warning}
        </span>
      </a>`;
  }).join('');
  return `
    <section class="card recent-card span-4">
      <header class="card-head">
        <div>
          <div class="card-title-row">${icon('clock')}<h3>最近导入</h3></div>
          <p>每次导入都有记录</p>
        </div>
        <a class="link-arrow" href="#/history">全部 ${icon('arrow-right')}</a>
      </header>
      ${rows ? `<div class="recent-list">${rows}</div>` : `<div class="empty-state" style="padding:34px 8px 24px">
        <div class="empty-icon" style="width:40px;height:40px;border-radius:11px">${icon('upload')}</div>
        <h3>还没有导入记录</h3>
        <p>第一次导入后，这里会留下可追溯的结果。</p>
      </div>`}
      <div class="recent-foot">
        <a class="btn btn-secondary btn-sm btn-block" href="#/import">${icon('plus')}导入新数据</a>
      </div>
    </section>`;
}

function bindCharts(data) {
  const trendContainer = document.getElementById('trendChart');
  if (trendCleanup) {
    try { trendCleanup(); } catch { /* ignore */ }
    trendCleanup = null;
  }
  if (trendContainer) {
    trendCleanup = renderTrendChart(trendContainer, data.trend);
  }
  if (donutCleanup) {
    try { donutCleanup(); } catch { /* ignore */ }
    donutCleanup = null;
  }
  const donutContainer = document.getElementById('compositionDonut');
  if (donutContainer) {
    const direction = compositionDirection;
    donutCleanup = renderDonut(donutContainer, data.composition[direction] || [], {
      label: direction === 'expense' ? '支出合计' : '收入合计',
      totalCents: data.composition[`${direction}_total_cents`] || 0,
    });
  }
}

function paint(data) {
  if (!currentContainer) return;
  currentContainer.innerHTML = `
    <div class="dashboard-page">
      ${dashboardHeading(data)}
      ${demoBanner(data)}
      <section class="kpi-grid" aria-label="关键经营指标">
        ${kpiCard(data.kpis[0], { iconName: 'wallet', tone: 'income', spark: sparkFor(data, 'income', '#2f6b5b') })}
        ${kpiCard(data.kpis[1], { iconName: 'receipt', tone: 'expense', inverse: true, spark: sparkFor(data, 'expense', '#cb7052') })}
        ${kpiCard(data.kpis[2], { iconName: 'trend', tone: 'net', spark: sparkFor(data, 'net', '#d1b271') })}
        ${kpiCard(data.kpis[3], { iconName: 'layers', tone: 'count', spark: sparkFor(data, 'count', '#6e8fa3') })}
      </section>
      <div class="dashboard-grid">
        ${trendSection(data)}
        ${qualitySection(data)}
        ${compositionSection(data)}
        ${departmentSection(data)}
        ${recentSection(data)}
      </div>
    </div>`;
  window.requestAnimationFrame(() => bindCharts(data));
}

async function load(showSkeleton = true) {
  const sequence = ++loadSequence;
  const container = currentContainer;
  if (showSkeleton && currentContainer) {
    if (trendCleanup) { try { trendCleanup(); } catch { /* ignore */ } trendCleanup = null; }
    if (donutCleanup) { try { donutCleanup(); } catch { /* ignore */ } donutCleanup = null; }
    currentContainer.innerHTML = skeletonPage();
  }
  const result = await api.dashboard(periodParams());
  if (sequence !== loadSequence || container !== currentContainer || !container?.isConnected) return;
  const data = result.dashboard;
  container._dashboardData = data;
  if (!data.has_data) {
    container.innerHTML = onboarding();
    return;
  }
  if (!data.range_has_data) {
    container.innerHTML = rangeEmpty(data);
    return;
  }
  paint(data);
}

function openCustomPeriod(data) {
  const today = data?.server_date || new Date().toISOString().slice(0, 10);
  const defaultStart = customRange?.start || `${today.slice(0, 7)}-01`;
  const defaultEnd = customRange?.end || today;
  const modal = openModal({
    title: '自定义统计期间',
    subtitle: '按自然日汇总，含开始和结束当天',
    body: `
      <form class="form-grid" id="customPeriodForm">
        <div class="field"><label class="field-label" for="periodStart">开始日期</label><input class="input" type="date" id="periodStart" name="start" value="${e(defaultStart)}" max="${e(today)}" required></div>
        <div class="field"><label class="field-label" for="periodEnd">结束日期</label><input class="input" type="date" id="periodEnd" name="end" value="${e(defaultEnd)}" max="${e(today)}" required></div>
      </form>
      <div class="notice notice-info modal-note">${icon('info')}<span>对比指标会自动取“上一个等长周期”，便于查看变化。</span></div>
    `,
    footer: `
      <button type="button" class="btn btn-ghost" data-modal-close>取消</button>
      <button type="button" class="btn btn-primary" data-apply-period>应用期间</button>
    `,
    onMount(dialog) {
      dialog.querySelector('[data-apply-period]').addEventListener('click', async () => {
        const form = dialog.querySelector('#customPeriodForm');
        if (!form.reportValidity()) return;
        const start = form.elements.start.value;
        const end = form.elements.end.value;
        if (start > end) {
          toast('开始日期不能晚于结束日期', 'warning');
          return;
        }
        customRange = { start, end };
        preferredPreset = 'custom';
        modal.close();
        try {
          await load(true);
        } catch (error) {
          toast(error.message, 'error');
        }
      });
    },
  });
}

async function seedDemo(button) {
  if (!button) return;
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="loading-spinner dark"></span>正在准备示例…`;
  try {
    const result = await api.seedDemo();
    await refreshApp();
    toast(result.message || '演示数据已载入', 'success');
    await load(true);
  } catch (error) {
    toast(error.message, 'error');
    button.disabled = false;
    button.innerHTML = original;
  }
}

async function clearDemo(button) {
  const confirmed = await confirmDialog({
    title: '清除演示数据',
    message: `将删除当前工作台中的全部演示数据。手工录入和真实导入的数据不会被删除。`,
    confirmText: '清除演示数据',
    tone: 'danger',
  });
  if (!confirmed) return;
  if (button) button.disabled = true;
  try {
    const result = await api.clearDemo();
    await refreshApp();
    toast(`已清除 ${result.deleted || 0} 条演示数据`, 'success');
    await load(true);
  } catch (error) {
    toast(error.message, 'error');
    if (button) button.disabled = false;
  }
}

function delegatedClick(event) {
  const periodButton = event.target.closest('[data-period]');
  if (periodButton) {
    preferredPreset = periodButton.dataset.period;
    customRange = null;
    load(true).catch((error) => toast(error.message, 'error'));
    return;
  }
  const customButton = event.target.closest('[data-action="custom-period"]');
  if (customButton) {
    // 需要当前日期作为 max，若页面为空时回退到系统日期。
    openCustomPeriod({ server_date: state.app?.server_date });
    return;
  }
  const compButton = event.target.closest('[data-comp]');
  if (compButton) {
    compositionDirection = compButton.dataset.comp;
    const data = currentContainer?._dashboardData;
    if (data) {
      paint(data);
    } else {
      load(false).catch((error) => toast(error.message, 'error'));
    }
    return;
  }
  const categoryButton = event.target.closest('[data-category]');
  if (categoryButton) {
    const direction = categoryButton.dataset.direction;
    const params = { category: categoryButton.dataset.category, direction };
    if (preferredPreset !== 'all') {
      const data = currentContainer?._dashboardData;
      if (data?.period?.start && data.period.start !== '1990-01-01') {
        params.start = data.period.start;
        params.end = data.period.end;
      }
    }
    window.location.hash = buildHash('/records', params);
    return;
  }
  const departmentButton = event.target.closest('[data-department]');
  if (departmentButton) {
    const params = { department: departmentButton.dataset.department };
    const data = currentContainer?._dashboardData;
    if (data?.period?.start && data.period.start !== '1990-01-01') {
      params.start = data.period.start;
      params.end = data.period.end;
    }
    window.location.hash = buildHash('/records', params);
    return;
  }
  const seedButton = event.target.closest('[data-action="seed-demo"]');
  if (seedButton) {
    seedDemo(seedButton);
    return;
  }
  const clearButton = event.target.closest('[data-action="clear-demo"]');
  if (clearButton) {
    clearDemo(clearButton);
  }
}

export async function render(container, ctx) {
  const sequence = ++pageSequence;
  loadSequence += 1;
  container.addEventListener('click', delegatedClick);
  currentContainer = container;
  container.innerHTML = skeletonPage();
  try {
    const result = await api.dashboard(periodParams());
    if (sequence !== pageSequence) {
      container.removeEventListener('click', delegatedClick);
      return () => {};
    }
    const data = result.dashboard;
    container._dashboardData = data;
    if (!data.has_data) {
      container.innerHTML = onboarding();
    } else if (!data.range_has_data) {
      container.innerHTML = rangeEmpty(data);
    } else {
      paint(data);
    }
    return () => {
      loadSequence += 1;
      if (trendCleanup) { try { trendCleanup(); } catch { /* ignore */ } trendCleanup = null; }
      if (donutCleanup) { try { donutCleanup(); } catch { /* ignore */ } donutCleanup = null; }
      container.removeEventListener('click', delegatedClick);
      container._dashboardData = null;
      if (currentContainer === container) currentContainer = null;
      void ctx;
    };
  } catch (error) {
    container.removeEventListener('click', delegatedClick);
    if (currentContainer === container) currentContainer = null;
    throw error;
  }
}
