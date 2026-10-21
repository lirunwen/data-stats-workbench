/** 经营流水台账：筛选、分页、搜索、增删改查与导出。 */
import { api, buildQuery, download } from '../api.js';
import { refreshOptions, state as store } from '../store.js';
import {
  buildHash, confirmDialog, debounce, e, emptyState, formatDateShort, formatMoney,
  formatPct, icon, n0, openModal, setButtonLoading, toast, todayISO,
} from '../ui.js';

const SORT_LABELS = {
  date_desc: '日期：最新在前',
  date_asc: '日期：最早在前',
  amount_desc: '金额：从高到低',
  amount_asc: '金额：从低到高',
  updated_desc: '最近修改',
};

function parsePageParams(params) {
  const read = (key) => (params.get(key) || '').trim();
  const pageSize = Number(read('page_size')) || 20;
  return {
    filters: {
      q: read('q'),
      start: read('start'),
      end: read('end'),
      direction: ['income', 'expense'].includes(read('direction')) ? read('direction') : '',
      category: read('category'),
      department: read('department'),
      source: ['manual', 'import'].includes(read('source')) ? read('source') : '',
      batch_id: read('batch_id'),
    },
    page: Math.max(1, Number(read('page')) || 1),
    pageSize: [10, 20, 50, 100].includes(pageSize) ? pageSize : 20,
    sort: SORT_LABELS[read('sort')] ? read('sort') : 'date_desc',
  };
}

function activeFilterCount(filters) {
  return ['q', 'start', 'end', 'direction', 'category', 'department', 'source', 'batch_id']
    .filter((key) => filters[key]).length;
}

function selectOptions(items, current, placeholder = '全部') {
  const names = items.map((item) => (typeof item === 'string' ? item : item.name));
  if (current && !names.includes(current)) names.unshift(current);
  return [
    `<option value="">${e(placeholder)}</option>`,
    ...names.map((name) => `<option value="${e(name)}" ${name === current ? 'selected' : ''}>${e(name)}</option>`),
  ].join('');
}

function filterMarkup(filters) {
  const categories = store.options?.categories || [];
  const departments = store.options?.departments || [];
  const count = activeFilterCount(filters);
  return `
    <div class="filter-field filter-search">
      <label for="ledgerSearch">搜索</label>
      ${icon('search')}
      <input class="input" id="ledgerSearch" type="search" autocomplete="off"
        placeholder="类别、单位、单号或备注" value="${e(filters.q)}">
    </div>
    <div class="filter-field filter-direction">
      <label for="filterDirection">收支</label>
      <select class="select" id="filterDirection" data-filter="direction">
        <option value="" ${!filters.direction ? 'selected' : ''}>全部</option>
        <option value="income" ${filters.direction === 'income' ? 'selected' : ''}>收入</option>
        <option value="expense" ${filters.direction === 'expense' ? 'selected' : ''}>支出</option>
      </select>
    </div>
    <div class="filter-field filter-category">
      <label for="filterCategory">类别</label>
      <select class="select" id="filterCategory" data-filter="category">
        ${selectOptions(categories, filters.category, '全部类别')}
      </select>
    </div>
    <div class="filter-field filter-department">
      <label for="filterDepartment">部门</label>
      <select class="select" id="filterDepartment" data-filter="department">
        ${selectOptions(departments, filters.department, '全部部门')}
      </select>
    </div>
    <div class="filter-field filter-date">
      <label>日期范围</label>
      <div class="date-range">
        <input class="input" type="date" aria-label="开始日期" data-filter="start" value="${e(filters.start)}" max="${todayISO()}">
        <span>—</span>
        <input class="input" type="date" aria-label="结束日期" data-filter="end" value="${e(filters.end)}" max="${todayISO()}">
      </div>
    </div>
    <div class="filter-field filter-source">
      <label for="filterSource">来源</label>
      <select class="select" id="filterSource" data-filter="source">
        <option value="" ${!filters.source ? 'selected' : ''}>全部来源</option>
        <option value="import" ${filters.source === 'import' ? 'selected' : ''}>批量导入</option>
        <option value="manual" ${filters.source === 'manual' ? 'selected' : ''}>手工录入</option>
      </select>
    </div>
    <div class="filter-actions">
      ${filters.batch_id ? `<span class="chip chip-green batch-filter-chip">${icon('file-xls')}仅看本次导入<button type="button" data-action="clear-batch" aria-label="取消本次导入筛选">${icon('x')}</button></span>` : ''}
      <button type="button" class="btn btn-ghost btn-sm" data-action="reset-filters">
        ${icon('refresh')}重置${count ? ` <span class="active-filter-count">${count}</span>` : ''}
      </button>
    </div>
  `;
}

function summaryMarkup(data) {
  const summary = data.summary;
  return `
    <div class="summary-cell"><span>当前结果</span><strong>${n0(data.total)}<small style="font-family:var(--font-sans);font-size:10px;font-weight:400;color:var(--faint)"> 笔</small></strong></div>
    <div class="summary-cell"><span>收入合计</span><strong class="income">${formatMoney(summary.income_cents)}</strong></div>
    <div class="summary-cell"><span>支出合计</span><strong class="expense">${formatMoney(summary.expense_cents)}</strong></div>
    <div class="summary-cell"><span>经营净额</span><strong>${formatMoney(summary.net_cents)}</strong></div>
  `;
}

function rowMarkup(record) {
  const direction = record.direction === 'income' ? 'income' : 'expense';
  const sign = direction === 'income' ? '+' : '−';
  const sourceIcon = record.source_type === 'import' ? 'file-xls' : 'edit';
  return `
    <tr data-record-id="${e(record.id)}" class="${record.is_demo ? 'is-demo-row' : ''}">
      <td><input type="checkbox" class="check row-check" data-select-id="${e(record.id)}" aria-label="选择 ${e(record.business_date)} 的流水"></td>
      <td><span class="date-cell">${formatDateShort(record.business_date)}</span></td>
      <td><span class="type-pill ${direction}">${e(record.direction_label)}</span></td>
      <td>
        <div class="category-cell">
          <span class="category-dot"></span>
          <span class="category-name" title="${e(record.category)}">${e(record.category)}</span>
        </div>
      </td>
      <td><span class="text-muted">${e(record.department || '未指定')}</span></td>
      <td><span class="party-cell" title="${e(record.counterparty || '')}">${e(record.counterparty || '—')}</span></td>
      <td class="col-amount"><span class="amount-cell ${direction}"><span class="sign">${sign}</span>${formatMoney(record.amount_cents, { symbol: false })}</span></td>
      <td><span class="doc-cell" title="${e(record.doc_no || '')}">${e(record.doc_no || '—')}</span></td>
      <td>
        <div class="source-cell">
          <span class="source-badge">${icon(sourceIcon)}${record.source_type === 'import' ? '导入' : '手工'}</span>
          ${record.is_demo ? '<span class="demo-mark">演示数据</span>' : ''}
        </div>
      </td>
      <td>
        <div class="row-actions">
          <button type="button" class="icon-button icon-button-sm" data-row-edit="${e(record.id)}" aria-label="编辑这条流水" title="编辑">${icon('edit')}</button>
          <button type="button" class="icon-button icon-button-sm danger" data-row-delete="${e(record.id)}" aria-label="删除这条流水" title="删除">${icon('trash')}</button>
        </div>
      </td>
    </tr>
  `;
}

function tableMarkup(data, filters) {
  if (!data.items.length) {
    const filtered = activeFilterCount(filters) > 0;
    return `
      <tr><td colspan="10" class="table-empty">
        ${emptyState({
          iconName: filtered ? 'search' : 'list',
          title: filtered ? '没有找到符合条件的流水' : '还没有经营流水',
          description: filtered
            ? '可以调整关键字、日期或筛选条件后再试。'
            : '导入 Excel 台账，或先手工补录一笔，经营总览会自动生成汇总。',
          action: filtered
            ? `<button type="button" class="btn btn-secondary btn-sm" data-action="reset-filters">${icon('refresh')}清除筛选</button>`
            : `<a class="btn btn-primary btn-sm" href="#/import">${icon('upload')}导入数据</a>`,
        })}
      </td></tr>`;
  }
  return data.items.map(rowMarkup).join('');
}

function paginationMarkup(data) {
  const pages = data.pages;
  const current = data.page;
  const numbers = [];
  if (pages <= 7) {
    for (let index = 1; index <= pages; index += 1) numbers.push(index);
  } else if (current <= 4) {
    numbers.push(1, 2, 3, 4, 5, '...', pages);
  } else if (current >= pages - 3) {
    numbers.push(1, '...', pages - 4, pages - 3, pages - 2, pages - 1, pages);
  } else {
    numbers.push(1, '...', current - 1, current, current + 1, '...', pages);
  }
  const from = data.total === 0 ? 0 : (current - 1) * data.page_size + 1;
  const to = Math.min(current * data.page_size, data.total);
  return `
    <span class="page-info">显示 <strong>${n0(from)}–${n0(to)}</strong>，共 <strong>${n0(data.total)}</strong> 笔</span>
    <div class="pager">
      <button type="button" data-page="${current - 1}" ${current <= 1 ? 'disabled' : ''} aria-label="上一页">${icon('chevron-left')}</button>
      ${numbers.map((number) => number === '...'
        ? '<button type="button" disabled>…</button>'
        : `<button type="button" data-page="${number}" class="${number === current ? 'active' : ''}" ${number === current ? 'aria-current="page"' : ''}>${number}</button>`).join('')}
      <button type="button" data-page="${current + 1}" ${current >= pages ? 'disabled' : ''} aria-label="下一页">${icon('chevron-right')}</button>
    </div>
    <div class="page-size">
      <span>每页</span>
      <select class="select" id="pageSize" aria-label="每页显示条数">
        ${[10, 20, 50, 100].map((size) => `<option value="${size}" ${data.page_size === size ? 'selected' : ''}>${size}</option>`).join('')}
      </select>
      <span>条</span>
    </div>
  `;
}

export function render(container, ctx) {
  const initial = parsePageParams(ctx.params);
  let filters = { ...initial.filters };
  let page = initial.page;
  let pageSize = initial.pageSize;
  let sort = initial.sort;
  let selected = new Set();
  let currentData = null;
  let currentItems = new Map();
  let disposed = false;
  let abortController = null;
  let loadSequence = 0;
  const searchDebounced = debounce(() => {
    page = 1;
    syncUrl();
    updateFilterChrome();
    load();
  }, 330);

  container.innerHTML = `
    <div class="records-page">
      <div class="records-summary" id="recordsSummary">
        <div class="summary-cell"><span>当前结果</span><strong>—</strong></div>
        <div class="summary-cell"><span>收入合计</span><strong class="income">—</strong></div>
        <div class="summary-cell"><span>支出合计</span><strong class="expense">—</strong></div>
        <div class="summary-cell"><span>经营净额</span><strong>—</strong></div>
      </div>
      <section class="card ledger-card">
        <header class="ledger-head">
          <div>
            <h2>流水明细</h2>
            <p id="ledgerSubtitle">正在读取经营流水…</p>
          </div>
          <div class="ledger-actions">
            <select class="select select-sm" id="ledgerSort" aria-label="排序方式" style="width:142px">
              ${Object.entries(SORT_LABELS).map(([value, label]) => `<option value="${value}" ${sort === value ? 'selected' : ''}>${label}</option>`).join('')}
            </select>
            <div class="export-menu-wrap">
              <button type="button" class="btn btn-secondary btn-sm" data-action="toggle-export" aria-expanded="false">
                ${icon('download')}导出${icon('chevron-down')}
              </button>
              <div class="menu hidden" id="exportMenu">
                <button type="button" data-export="xlsx">${icon('file-xls')}导出为 Excel</button>
                <button type="button" data-export="csv">${icon('file')}导出为 CSV</button>
              </div>
            </div>
            <button type="button" class="btn btn-primary btn-sm" data-action="add-record">${icon('plus')}新增记录</button>
          </div>
        </header>
        <div class="filter-bar" id="filterHost">${filterMarkup(filters)}</div>
        <div class="bulk-bar hidden" id="bulkBar">
          <span>已选择 <strong id="selectedCount">0</strong> 笔</span>
          <button type="button" class="btn btn-danger-soft btn-sm" data-action="bulk-delete">${icon('trash')}批量删除</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="clear-selection">取消选择</button>
        </div>
        <div class="table-wrap" id="tableWrap">
          <table class="ledger-table" aria-label="经营流水列表">
            <thead>
              <tr>
                <th><input type="checkbox" class="check" id="selectAll" aria-label="选择本页全部流水"></th>
                <th class="col-date">业务日期</th>
                <th class="col-type">收支</th>
                <th class="col-category">经营类别</th>
                <th class="col-dept">部门</th>
                <th class="col-party">往来单位</th>
                <th class="col-amount">金额（元）</th>
                <th class="col-doc">单据编号</th>
                <th class="col-source">来源</th>
                <th class="col-actions">操作</th>
              </tr>
            </thead>
            <tbody id="ledgerBody">
              ${Array.from({ length: 7 }).map(() => `<tr><td colspan="10" style="padding:8px 20px"><div class="skeleton" style="height:28px"></div></td></tr>`).join('')}
            </tbody>
          </table>
        </div>
        <footer class="pagination" id="paginationHost"></footer>
      </section>
    </div>
  `;

  const summaryHost = container.querySelector('#recordsSummary');
  const tableHost = container.querySelector('#tableWrap');
  const bodyHost = container.querySelector('#ledgerBody');
  const paginationHost = container.querySelector('#paginationHost');
  const filterHost = container.querySelector('#filterHost');
  const subtitle = container.querySelector('#ledgerSubtitle');
  const bulkBar = container.querySelector('#bulkBar');
  const selectedCount = container.querySelector('#selectedCount');
  const exportButton = container.querySelector('[data-action="toggle-export"]');
  const exportMenu = container.querySelector('#exportMenu');
  const sortSelect = container.querySelector('#ledgerSort');
  const platform = container.querySelector('#selectAll');

  function syncUrl() {
    const params = { ...filters };
    if (page > 1) params.page = page;
    if (pageSize !== 20) params.page_size = pageSize;
    if (sort !== 'date_desc') params.sort = sort;
    window.history.replaceState(null, '', buildHash('/records', params));
  }

  function updateFilterChrome() {
    const count = activeFilterCount(filters);
    const reset = filterHost.querySelector('[data-action="reset-filters"]');
    if (reset) {
      reset.innerHTML = `${icon('refresh')}重置${count ? ` <span class="active-filter-count">${count}</span>` : ''}`;
    }
  }

  function syncFilterInputs() {
    filterHost.querySelectorAll('[data-filter]').forEach((control) => {
      control.value = filters[control.dataset.filter] || '';
    });
    const search = filterHost.querySelector('#ledgerSearch');
    if (search && search.value !== filters.q) search.value = filters.q;
  }

  function selectionIds() {
    return Array.from(selected);
  }

  function updateSelectionUI() {
    if (!currentData) return;
    const pageIds = currentData.items.map((item) => item.id);
    const selectedOnPage = pageIds.filter((id) => selected.has(id)).length;
    if (platform) {
      platform.checked = pageIds.length > 0 && selectedOnPage === pageIds.length;
      platform.indeterminate = selectedOnPage > 0 && selectedOnPage < pageIds.length;
    }
    currentData.items.forEach((item) => {
      const checkbox = bodyHost.querySelector(`[data-select-id="${CSS.escape(item.id)}"]`);
      if (checkbox) checkbox.checked = selected.has(item.id);
      const row = checkbox?.closest('tr');
      if (row) row.classList.toggle('is-selected', selected.has(item.id));
    });
    bulkBar.classList.toggle('hidden', selected.size === 0);
    selectedCount.textContent = n0(selected.size);
  }

  function setBusy() {
    bodyHost.innerHTML = `<tr><td colspan="10" style="padding:14px 20px"><div class="loading-block" style="min-height:180px"><span class="loading-spinner dark"></span>正在读取流水…</div></td></tr>`;
  }

  function renderError(message) {
    bodyHost.innerHTML = `<tr><td colspan="10" class="table-empty"><div class="empty-state" style="padding:12px">
      <div class="empty-icon">${icon('alert', 'icon-xl')}</div><h3>数据读取失败</h3><p>${e(message)}</p>
      <button class="btn btn-secondary btn-sm" type="button" data-action="retry-load">${icon('refresh')}重试</button>
    </div></td></tr>`;
    paginationHost.innerHTML = '';
  }

  async function load() {
    const sequence = ++loadSequence;
    setBusy();
    abortController?.abort();
    abortController = new AbortController();
    const params = {
      ...filters,
      sort,
      page,
      page_size: pageSize,
    };
    try {
      const result = await api.records(params, abortController.signal);
      if (disposed || sequence !== loadSequence) return;
      currentData = result;
      currentItems = new Map(result.items.map((item) => [item.id, item]));
      if (page !== result.page) {
        page = result.page;
      }
      selected = new Set();
      summaryHost.innerHTML = summaryMarkup(result);
      subtitle.textContent = `共 ${n0(result.total)} 笔 · 统计范围与当前筛选一致`;
      bodyHost.innerHTML = tableMarkup(result, filters);
      paginationHost.innerHTML = paginationMarkup(result);
      updateSelectionUI();
      syncUrl();
      const pageSizeSelect = paginationHost.querySelector('#pageSize');
      if (pageSizeSelect) {
        pageSizeSelect.addEventListener('change', () => {
          pageSize = Number(pageSizeSelect.value) || 20;
          page = 1;
          syncUrl();
          load();
        });
      }
      exportButton.disabled = result.total === 0;
      updateFilterChrome();
    } catch (error) {
      if (error.name === 'AbortError' || disposed || sequence !== loadSequence) return;
      renderError(error.message);
      toast(error.message, 'error');
    }
  }

  function closeExportMenu() {
    exportMenu.classList.add('hidden');
    exportButton?.setAttribute('aria-expanded', 'false');
  }

  function openRecordForm(record = null) {
    const editing = Boolean(record);
    const defaultDirection = record?.direction || 'expense';
    const categoryOptions = (store.options?.categories || []).map((item) => item.name);
    const departmentOptions = (store.options?.departments || []).map((item) => item.name);
    const categoryList = [...new Set([...categoryOptions, record?.category].filter(Boolean))];
    const departmentList = [...new Set([...departmentOptions, record?.department].filter(Boolean))];
    let saving = false;
    const modal = openModal({
      title: editing ? '编辑经营流水' : '新增经营流水',
      subtitle: editing ? '修改后会同步更新经营总览与筛选结果。' : '补录一笔收入或支出，经营总览会自动重新汇总。',
      size: '',
      body: `
        <form id="recordForm" novalidate>
          <div class="form-grid">
            <div class="field">
              <label class="field-label" for="recordDate">业务日期 <span class="required">*</span></label>
              <input class="input" type="date" id="recordDate" name="business_date" value="${e(record?.business_date || todayISO())}" required>
              <div class="field-error" data-error-for="business_date"></div>
            </div>
            <div class="field">
              <label class="field-label">收支类型 <span class="required">*</span></label>
              <div class="direction-choice" role="radiogroup" aria-label="收支类型">
                <label class="direction-option income ${defaultDirection === 'income' ? 'active' : ''}">
                  <input type="radio" name="direction" value="income" ${defaultDirection === 'income' ? 'checked' : ''}><span>${icon('wallet')}收入</span>
                </label>
                <label class="direction-option expense ${defaultDirection === 'expense' ? 'active' : ''}">
                  <input type="radio" name="direction" value="expense" ${defaultDirection === 'expense' ? 'checked' : ''}><span>${icon('receipt')}支出</span>
                </label>
              </div>
              <div class="field-error" data-error-for="direction"></div>
            </div>
            <div class="field">
              <label class="field-label" for="recordAmount">金额（元） <span class="required">*</span></label>
              <div class="input-affix">
                <span class="prefix">¥</span>
                <input class="input" type="number" min="0.01" step="0.01" inputmode="decimal" id="recordAmount" name="amount" placeholder="0.00" value="${record?.amount ?? ''}" required>
              </div>
              <div class="field-error" data-error-for="amount"></div>
            </div>
            <div class="field">
              <label class="field-label" for="recordDoc">单据编号</label>
              <input class="input" id="recordDoc" name="doc_no" maxlength="120" placeholder="如 YS-2026-0088" value="${e(record?.doc_no || '')}">
              <div class="field-hint">有编号时，判重会结合编号、金额与类别。</div>
            </div>
            <div class="field">
              <label class="field-label" for="recordCategory">经营类别</label>
              <input class="input" id="recordCategory" name="category" maxlength="80" list="categoryList" placeholder="如 产品销售收入" value="${e(record?.category || '')}">
              <datalist id="categoryList">${categoryList.map((name) => `<option value="${e(name)}"></option>`).join('')}</datalist>
            </div>
            <div class="field">
              <label class="field-label" for="recordDept">部门</label>
              <input class="input" id="recordDept" name="department" maxlength="80" list="departmentList" placeholder="如 销售部" value="${e(record?.department || '')}">
              <datalist id="departmentList">${departmentList.map((name) => `<option value="${e(name)}"></option>`).join('')}</datalist>
            </div>
            <div class="field">
              <label class="field-label" for="recordParty">往来单位</label>
              <input class="input" id="recordParty" name="counterparty" maxlength="160" placeholder="客户、供应商或交易对方" value="${e(record?.counterparty || '')}">
            </div>
            <div class="field">
              <label class="field-label" for="recordPayment">结算方式</label>
              <input class="input" id="recordPayment" name="payment_method" maxlength="80" placeholder="如 银行转账" value="${e(record?.payment_method || '')}">
            </div>
            <div class="field full">
              <label class="field-label" for="recordNote">摘要 / 备注</label>
              <textarea class="textarea" id="recordNote" name="note" maxlength="1000" placeholder="这笔收支的用途、事由或补充说明">${e(record?.note || '')}</textarea>
            </div>
          </div>
          <div class="notice notice-danger hidden modal-note" id="recordFormError">${icon('alert')}<span></span></div>
        </form>
      `,
      footer: `
        <span class="left">${editing ? '创建时间：' + e(record.created_at?.slice(0, 19).replace('T', ' ') || '—') : '所有数据仅保存在本机'}</span>
        <button type="button" class="btn btn-ghost" data-modal-close>取消</button>
        <button type="button" class="btn btn-primary" data-save-record>${editing ? '保存修改' : '保存记录'}</button>
      `,
      onMount(dialog, controls) {
        const form = dialog.querySelector('#recordForm');
        const saveButton = dialog.querySelector('[data-save-record]');
        const errorBox = dialog.querySelector('#recordFormError');
        form.querySelectorAll('input[name="direction"]').forEach((radio) => {
          radio.addEventListener('change', () => {
            form.querySelectorAll('.direction-option').forEach((label) => {
              label.classList.toggle('active', label.querySelector('input').checked);
            });
          });
        });
        const showFormError = (message, fields = {}) => {
          errorBox.querySelectorAll('.btn').forEach((node) => node.remove());
          errorBox.classList.remove('hidden');
          errorBox.querySelector('span').textContent = message;
          form.querySelectorAll('[data-error-for]').forEach((node) => { node.textContent = ''; });
          form.querySelectorAll('.has-error').forEach((node) => node.classList.remove('has-error'));
          Object.entries(fields || {}).forEach(([key, text]) => {
            const node = form.querySelector(`[data-error-for="${CSS.escape(key)}"]`);
            if (node) node.textContent = text;
            const input = form.querySelector(`[name="${CSS.escape(key)}"]`);
            if (input) input.classList.add('has-error');
          });
        };
        const submit = async () => {
          if (saving) return;
          errorBox.classList.add('hidden');
          form.querySelectorAll('.field-error').forEach((node) => { node.textContent = ''; });
          form.querySelectorAll('.has-error').forEach((node) => node.classList.remove('has-error'));
          if (!form.reportValidity()) return;
          const payload = {
            business_date: form.elements.business_date.value,
            direction: form.querySelector('input[name="direction"]:checked')?.value || '',
            amount: form.elements.amount.value,
            category: form.elements.category.value,
            department: form.elements.department.value,
            counterparty: form.elements.counterparty.value,
            payment_method: form.elements.payment_method.value,
            doc_no: form.elements.doc_no.value,
            note: form.elements.note.value,
          };
          saving = true;
          controls.setLocked(true);
          const restore = setButtonLoading(saveButton, true, '保存中…');
          try {
            if (editing) await api.updateRecord(record.id, payload);
            else await api.createRecord(payload);
            toast(editing ? '记录已更新' : '记录已新增', 'success');
            modal.close();
            await refreshOptions();
            filterHost.innerHTML = filterMarkup(filters);
            syncFilterInputs();
            updateFilterChrome();
            await load();
          } catch (error) {
            saving = false;
            controls.setLocked(false);
            restore();
            const fields = error.details?.fields || {};
            showFormError(error.message || '保存失败，请检查后重试', fields);
            if (error.code === 'duplicate_record') {
              const existing = error.details?.existing_id;
              if (existing) {
                const jump = document.createElement('button');
                jump.type = 'button';
                jump.className = 'btn btn-secondary btn-sm';
                jump.style.marginTop = '10px';
                jump.textContent = '打开已有记录';
                jump.addEventListener('click', async () => {
                  modal.close();
                  const item = currentItems.get(existing);
                  if (item) {
                    openRecordForm(item);
                    return;
                  }
                  try {
                    const result = await api.record(existing);
                    openRecordForm(result.record);
                  } catch (fetchError) {
                    toast(fetchError.message, 'error');
                  }
                });
                errorBox.appendChild(jump);
              }
            }
          }
        };
        saveButton.addEventListener('click', submit);
        form.addEventListener('submit', (event) => { event.preventDefault(); submit(); });
      },
    });
    void modal;
  }

  async function deleteOne(record) {
    const confirmed = await confirmDialog({
      title: '删除这条流水？',
      message: `将删除 <strong>${e(record.business_date)}</strong> 的 <strong>${e(record.category)}</strong>，金额 ${e(formatMoney(record.amount_cents))}。删除后经营汇总会立即更新。`,
      confirmText: '删除记录',
      tone: 'danger',
      warning: '删除不可撤销，建议先确认是否已做好数据备份。',
    });
    if (!confirmed) return;
    try {
      await api.deleteRecord(record.id);
      toast('记录已删除', 'success');
      await refreshOptions();
      filterHost.innerHTML = filterMarkup(filters);
      syncFilterInputs();
      await load();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function bulkDelete() {
    if (!selected.size) return;
    const count = selected.size;
    const confirmed = await confirmDialog({
      title: `删除选中的 ${count} 笔流水？`,
      message: `此操作会永久删除当前选中的 <strong>${count}</strong> 笔流水，经营总览会立即更新。`,
      confirmText: `删除 ${count} 笔`,
      tone: 'danger',
      warning: '删除不可撤销。',
    });
    if (!confirmed) return;
    try {
      const result = await api.bulkDelete(selectionIds());
      selected = new Set();
      toast(`已删除 ${result.deleted || count} 笔流水`, 'success');
      await refreshOptions();
      filterHost.innerHTML = filterMarkup(filters);
      syncFilterInputs();
      await load();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function handleExport(format) {
    closeExportMenu();
    const params = { ...filters, sort, format };
    const path = `/api/records/export?${buildQuery(params)}`;
    const original = exportButton.innerHTML;
    exportButton.disabled = true;
    exportButton.innerHTML = `<span class="loading-spinner dark"></span>正在导出…`;
    try {
      await download(path, { filename: `经营流水.${format === 'csv' ? 'csv' : 'xlsx'}` });
      toast(`已生成 ${format === 'csv' ? 'CSV' : 'Excel'} 文件`, 'success');
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      exportButton.disabled = currentData?.total === 0;
      exportButton.innerHTML = original;
    }
  }

  container.addEventListener('input', (event) => {
    if (event.target.id === 'ledgerSearch') {
      filters.q = event.target.value.trim();
      searchDebounced();
    }
  });

  container.addEventListener('change', (event) => {
    const target = event.target;
    if (target.matches('[data-filter]')) {
      const key = target.dataset.filter;
      const previous = filters[key];
      filters[key] = target.value;
      if (filters.start && filters.end && filters.start > filters.end) {
        filters[key] = previous;
        target.value = previous;
        toast('开始日期不能晚于结束日期', 'warning');
        return;
      }
      page = 1;
      syncUrl();
      updateFilterChrome();
      load();
      return;
    }
    if (target.id === 'ledgerSort') {
      sort = target.value;
      page = 1;
      syncUrl();
      load();
      return;
    }
    if (target.id === 'selectAll') {
      if (target.checked) {
        currentData?.items.forEach((item) => selected.add(item.id));
      } else {
        selected = new Set();
      }
      updateSelectionUI();
      return;
    }
    if (target.matches('[data-select-id]')) {
      if (target.checked) selected.add(target.dataset.selectId);
      else selected.delete(target.dataset.selectId);
      updateSelectionUI();
    }
  });

  container.addEventListener('click', (event) => {
    const target = event.target;
    if (target.closest('[data-action="toggle-export"], #exportMenu')) {
      event.stopPropagation();
    }
    const action = target.closest('[data-action]')?.dataset.action;
    if (action === 'toggle-export') {
      const open = exportMenu.classList.toggle('hidden');
      exportButton.setAttribute('aria-expanded', String(!open));
      return;
    }
    if (target.closest('[data-export]')) {
      handleExport(target.closest('[data-export]').dataset.export);
      return;
    }
    if (action === 'reset-filters') {
      filters = { q: '', start: '', end: '', direction: '', category: '', department: '', source: '', batch_id: '' };
      page = 1;
      filterHost.innerHTML = filterMarkup(filters);
      syncUrl();
      load();
      return;
    }
    if (action === 'clear-batch') {
      filters.batch_id = '';
      page = 1;
      filterHost.innerHTML = filterMarkup(filters);
      syncFilterInputs();
      syncUrl();
      load();
      return;
    }
    if (action === 'add-record') {
      openRecordForm();
      return;
    }
    if (action === 'clear-selection') {
      selected = new Set();
      updateSelectionUI();
      return;
    }
    if (action === 'bulk-delete') {
      bulkDelete();
      return;
    }
    if (action === 'retry-load') {
      load();
      return;
    }
    const pageButton = target.closest('button[data-page]');
    if (pageButton && !pageButton.disabled) {
      const next = Number(pageButton.dataset.page);
      if (Number.isFinite(next) && next >= 1) {
        page = next;
        syncUrl();
        load();
      }
      return;
    }
    const editButton = target.closest('[data-row-edit]');
    if (editButton) {
      const item = currentItems.get(editButton.dataset.rowEdit);
      if (item) openRecordForm(item);
      return;
    }
    const deleteButton = target.closest('[data-row-delete]');
    if (deleteButton) {
      const item = currentItems.get(deleteButton.dataset.rowDelete);
      if (item) deleteOne(item);
      return;
    }
    const row = target.closest('tr[data-record-id]');
    if (row && !target.closest('button, input, a, select, label')) {
      const item = currentItems.get(row.dataset.recordId);
      if (item) openRecordForm(item);
    }
  });

  const onDocumentClick = (event) => {
    if (!event.target.closest('.export-menu-wrap')) closeExportMenu();
  };
  document.addEventListener('click', onDocumentClick);

  load();

  return () => {
    disposed = true;
    loadSequence += 1;
    abortController?.abort();
    searchDebounced.cancel();
    document.removeEventListener('click', onDocumentClick);
  };
}
