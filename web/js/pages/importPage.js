/** 批量导入向导：文件/邮件接入 → 字段映射 → 校验导入 → 结果。 */
import { api, download, downloadWithPath, uploadFiles } from '../api.js';
import { refreshApp, state as store } from '../store.js';
import {
  confirmDialog, e, icon, n0, statusPill, toast, truncate,
} from '../ui.js';

const FIELD_META = [
  { key: 'date', label: '业务日期', required: true, hint: '交易或记账发生的日期', section: '核心字段' },
  { key: 'category', label: '经营类别', required: false, hint: '留空会归入“未分类”', section: '经营信息' },
  { key: 'department', label: '部门', required: false, hint: '用于部门经营分析', section: '经营信息' },
  { key: 'counterparty', label: '往来单位', required: false, hint: '客户、供应商或交易对方', section: '补充信息' },
  { key: 'payment_method', label: '结算方式', required: false, hint: '银行转账、现金、承兑等', section: '补充信息' },
  { key: 'doc_no', label: '单据编号', required: false, hint: '有编号时会结合金额与类别一起判重', section: '补充信息' },
  { key: 'note', label: '摘要 / 备注', required: false, hint: '用途、事由或说明', section: '补充信息' },
];

const ACCEPTED = ['xlsx', 'xlsm', 'xls', 'csv', 'tsv', 'txt', 'msg', 'eml', 'oft', 'pst', 'ost'];
const MAX_UPLOAD = 240 * 1024 * 1024;
const MAX_FILE = 80 * 1024 * 1024;

const STEP_LABELS = ['选择文件', '字段映射', '校验导入'];

function freshWizard() {
  return {
    step: 'start',
    session: null,
    sources: [],
    mappings: {},
    selected: new Set(),
    activeId: null,
    policy: store.settings?.default_duplicate_policy || 'skip',
    validation: null,
    batch: null,
    uploading: false,
    uploadProgress: 0,
    uploadNames: [],
    uploadError: '',
    revalidating: false,
    committing: false,
  };
}

let wizard = freshWizard();

function mappingTemplate() {
  return {
    version: 1,
    fields: {
      date: null, category: null, department: null, counterparty: null,
      payment_method: null, doc_no: null, note: null,
    },
    amount: { mode: 'single', column: null },
    direction: { mode: 'unset', column: null, value: null },
  };
}

function clone(value) {
  return value ? JSON.parse(JSON.stringify(value)) : value;
}

function normalizeMapping(mapping) {
  const base = mappingTemplate();
  if (!mapping) return base;
  const result = {
    version: 1,
    fields: { ...base.fields, ...(mapping.fields || {}) },
    amount: { ...base.amount, ...(mapping.amount || {}) },
    direction: { ...base.direction, ...(mapping.direction || {}) },
  };
  if (!['single', 'split'].includes(result.amount.mode)) result.amount.mode = 'single';
  if (result.amount.mode === 'split') {
    result.direction = { mode: 'split', column: null, value: null };
  }
  return result;
}

function stepNumber() {
  if (wizard.step === 'start') return 1;
  if (wizard.step === 'mapping') return 2;
  return 3;
}

function stepper() {
  const active = wizard.uploading || wizard.step === 'validating' ? stepNumber() : stepNumber();
  const done = (index) => (
    (index === 1 && active > 1) ||
    (index === 2 && (active > 2 || wizard.step === 'validation' || wizard.step === 'success')) ||
    (index === 3 && wizard.step === 'success')
  );
  return `
    <div class="stepper" aria-label="导入步骤">
      ${STEP_LABELS.map((label, index) => {
        const number = index + 1;
        const isActive = number === active && wizard.step !== 'success';
        return `
          ${index > 0 ? '<span class="step-line"></span>' : ''}
          <div class="step ${isActive ? 'active' : ''} ${done(number) ? 'done' : ''}" aria-current="${isActive ? 'step' : 'false'}">
            <span class="step-index">${done(number) ? '✓' : number}</span><span>${label}</span>
          </div>`;
      }).join('')}
    </div>`;
}

function pageHead(title, description, extra = '') {
  return `
    <div class="wizard-head">
      <div>
        <p class="eyebrow">导入向导</p>
        <h2>${e(title)}</h2>
        <p>${e(description)}</p>
      </div>
      ${extra}
    </div>`;
}

function errorsFromSession(session) {
  if (!session?.files) return [];
  return session.files
    .filter((file) => file.status === 'error' || (file.status === 'warning' && !file.source_count))
    .map((file) => ({ name: file.name, message: file.message || '未识别到可导入的数据表' }));
}

function fileFeedbackMarkup() {
  const errors = errorsFromSession(wizard.session);
  const lines = [...errors];
  if (wizard.uploadError) lines.unshift({ name: '上传未完成', message: wizard.uploadError });
  if (!lines.length) return '';
  return `
    <div class="upload-errors" role="alert">
      ${lines.slice(0, 5).map((item) => `
        <div class="upload-error">${icon('alert')}<span><strong>${e(item.name)}</strong>：${e(item.message)}</span></div>
      `).join('')}
      ${lines.length > 5 ? `<div class="upload-error">${icon('info')}<span>还有 ${lines.length - 5} 个文件提示未展示。</span></div>` : ''}
    </div>`;
}

function startStep() {
  return `
    <div class="import-page page-enter">
      <div class="wizard-top">
        ${stepper()}
        <a class="btn btn-ghost btn-sm" href="#/history">${icon('history')}导入记录</a>
      </div>
      ${pageHead('把表格交给工作台。', '支持 Excel 台账与 Outlook 邮件。字段识别、校验、去重和汇总会自动完成。')}
      <div class="import-start">
        <section class="card upload-card">
          <p class="eyebrow">第 1 步 · 共 3 步</p>
          <h2>拖入文件，开始整理。</h2>
          <p>可以一次选择多份文件或邮件。邮件中的 Excel 附件会被自动展开；多个工作表也会分别列出来供你确认。</p>
          ${wizard.uploading ? `
            <div class="dropzone is-uploading" id="dropzone">
              <div class="dropzone-icon"><span class="loading-spinner dark"></span></div>
              <h3>正在读取 ${wizard.uploadNames.length} 个文件</h3>
              <p>${e(wizard.uploadNames.slice(0, 3).join('、'))}${wizard.uploadNames.length > 3 ? ' 等' : ''}</p>
              <div class="upload-progress-wrap">
                <div class="upload-progress-label"><span id="uploadProgressLabel">上传中…</span><strong id="uploadProgressValue">${Number(wizard.uploadProgress || 0)}%</strong></div>
                <div class="progress"><span id="uploadProgressBar" style="width:${Number(wizard.uploadProgress || 0)}%"></span></div>
                <p class="upload-progress-hint">上传完成后会在本机解析，不会发送到任何外部服务。</p>
              </div>
            </div>
          ` : `
            <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="选择或拖入经营数据文件">
              <div class="dropzone-icon">${icon('upload')}</div>
              <h3>拖入文件到这里</h3>
              <p>或从电脑中选择 Excel / CSV / Outlook 邮件</p>
              <button type="button" class="btn btn-primary" data-action="choose-files">${icon('folder')}选择文件</button>
              <div class="file-types">
                ${['.xlsx', '.xls', '.csv', '.msg', '.eml', '.oft'].map((ext) => `<span class="file-type">${ext}</span>`).join('')}
              </div>
            </div>
          `}
          <input type="file" id="importFiles" class="hidden" multiple accept=".xlsx,.xlsm,.xls,.csv,.tsv,.txt,.msg,.eml,.oft,.pst,.ost">
          ${fileFeedbackMarkup()}
        </section>
        <aside class="import-side">
          <div class="card import-side-card">
            <h3>导入流程</h3>
            <p>先看一遍，再开始操作。</p>
            <div class="flow-list">
              <div class="flow-step"><span class="flow-num">1</span><div class="flow-copy"><strong>识别数据表</strong><span>自动找到表头行、工作表与邮件附件。</span></div></div>
              <div class="flow-step"><span class="flow-num">2</span><div class="flow-copy"><strong>确认字段映射</strong><span>日期、金额、收支等字段一一对应。</span></div></div>
              <div class="flow-step"><span class="flow-num">3</span><div class="flow-copy"><strong>校验后导入</strong><span>先看异常与重复，再写入本地数据库。</span></div></div>
            </div>
          </div>
          <div class="privacy-note">
            ${icon('shield')}
            <div><strong>全程在本机处理</strong><p>文件只交给本机 127.0.0.1 服务解析，不上传云端，也不需要联网。</p></div>
          </div>
          <div class="card import-side-card">
            <h3>第一次导入？</h3>
            <p>可以下载标准模板，按列填写后导入；也可以直接使用你现有的表头，系统会尝试自动匹配。</p>
            <a class="btn btn-secondary btn-sm btn-block" style="margin-top:12px" href="/api/import-template" download>${icon('download')}下载导入模板</a>
          </div>
        </aside>
      </div>
    </div>`;
}

function sourceGroups() {
  const groups = new Map();
  wizard.sources.forEach((source) => {
    const key = source.file_id || source.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(source);
  });
  return [...groups.values()];
}

function sourceItemTitle(source) {
  if (source.presence === 'body') return source.sheet_name || '邮件正文表格';
  if (source.presence === 'attachment') return source.file_name || source.sheet_name;
  return source.sheet_name || source.file_name;
}

function mappingErrors(source, mapping) {
  const errors = [];
  const fields = mapping?.fields || {};
  const headerCount = (source?.headers || []).length;
  const validColumn = (value) => Number.isInteger(value) && value >= 0 && value < headerCount;
  if (!validColumn(fields.date)) errors.push('请选择业务日期列');
  const amount = mapping?.amount || {};
  if (amount.mode === 'split') {
    if (!validColumn(amount.income_column)) errors.push('请选择收入金额列');
    if (!validColumn(amount.expense_column)) errors.push('请选择支出金额列');
    if (validColumn(amount.income_column) && amount.income_column === amount.expense_column) errors.push('收入与支出金额不能是同一列');
  } else {
    if (!validColumn(amount.column)) errors.push('请选择金额列');
    const direction = mapping?.direction || {};
    if (direction.mode === 'column' && !validColumn(direction.column)) errors.push('请选择收支类型列');
    if (direction.mode === 'fixed' && !['income', 'expense'].includes(direction.value)) errors.push('请选择整表收支类型');
    if (direction.mode === 'unset' || !direction.mode) errors.push('请选择收支类型的来源');
  }
  const used = new Map();
  Object.entries(fields).forEach(([key, column]) => {
    if (!validColumn(column)) return;
    const label = FIELD_META.find((item) => item.key === key)?.label || key;
    if (used.has(column)) errors.push(`“${used.get(column)}”与“${label}”使用了同一列`);
    else used.set(column, label);
  });
  const amountColumns = amount.mode === 'split'
    ? [[amount.income_column, '收入金额'], [amount.expense_column, '支出金额']]
    : [[amount.column, '金额']];
  amountColumns.forEach(([column, label]) => {
    if (!validColumn(column)) return;
    if (used.has(column)) errors.push(`“${used.get(column)}”与“${label}”使用了同一列`);
    else used.set(column, label);
  });
  if (mapping?.direction?.mode === 'column' && validColumn(mapping.direction.column)) {
    if (used.has(mapping.direction.column)) errors.push(`“${used.get(mapping.direction.column)}”与“收支类型”使用了同一列`);
  }
  return [...new Set(errors)];
}

function selectedSources() {
  return wizard.sources.filter((source) => wizard.selected.has(source.id));
}

function sourceStatus(source) {
  return wizard.selected.has(source.id) ? mappingErrors(source, wizard.mappings[source.id]) : [];
}

function sourcePanelMarkup() {
  const selectedCount = wizard.selected.size;
  const groups = sourceGroups();
  return `
    <div class="source-panel-head">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
        <div><h3>识别到的数据表</h3><p>勾选要导入的表，点击可编辑字段映射。</p></div>
        <span class="chip chip-green">${selectedCount}/${wizard.sources.length}</span>
      </div>
    </div>
    <div class="source-select-all">
      <input type="checkbox" class="check" id="sourceSelectAll" ${selectedCount === wizard.sources.length ? 'checked' : ''}>
      <label for="sourceSelectAll">全选</label>
      <span style="margin-left:auto">共 ${n0(wizard.sources.reduce((sum, source) => sum + Number(source.row_count || 0), 0))} 行</span>
    </div>
    <div class="source-list">
      ${groups.map((group) => {
        const first = group[0];
        const title = first.email?.subject || first.file_name || '数据文件';
        const isMail = Boolean(first.email);
        return `
          <div class="source-group">
            <div class="source-group-title">${icon(isMail ? 'mail' : 'file-xls')}<span title="${e(title)}">${e(truncate(title, 32))}</span></div>
            ${group.map((source) => {
              const checked = wizard.selected.has(source.id);
              const errors = sourceStatus(source);
              const active = wizard.activeId === source.id;
              return `
                <div class="source-item ${active ? 'active' : ''} ${checked ? '' : 'excluded'}" data-source-wrap="${e(source.id)}">
                  <input type="checkbox" class="check" data-source-check="${e(source.id)}" ${checked ? 'checked' : ''} aria-label="选择 ${e(source.display_name)}">
                  <button type="button" class="source-open" data-source-id="${e(source.id)}">
                    <span class="source-item-main">
                      <span class="source-item-name">${e(sourceItemTitle(source))}</span>
                      <span class="source-item-meta">${n0(source.row_count)} 行 · 表头第 ${source.header_row} 行${source.presence === 'attachment' && source.sheet_name && source.sheet_name !== 'CSV 数据' ? ` · ${e(source.sheet_name)}` : ''}</span>
                      ${source.email ? `<span class="source-item-email">${icon('mail')}${e(source.email.subject || source.email.sender || '来自 Outlook 邮件')}</span>` : ''}
                    </span>
                    <span class="source-state ${errors.length ? 'needs' : ''}" title="${errors.length ? e(errors[0]) : '映射已完整'}">${icon(errors.length ? 'alert' : 'check-circle')}</span>
                  </button>
                </div>`;
            }).join('')}
          </div>`;
      }).join('')}
    </div>`;
}

function headerOptions(headers, current, placeholder = '— 不映射 —') {
  return [
    `<option value="">${placeholder}</option>`,
    ...headers.map((header, index) => `<option value="${index}" ${index === current ? 'selected' : ''}>${e(header)}</option>`),
  ].join('');
}

function sampleValues(source, column) {
  if (!Number.isInteger(column)) return [];
  const values = [];
  for (const row of source.sample_rows || []) {
    const value = row.c?.[column];
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      const text = String(value).trim();
      if (!values.includes(text)) values.push(text);
    }
    if (values.length >= 2) break;
  }
  return values;
}

function sampleLine(source, column) {
  const values = sampleValues(source, column);
  if (!values.length) return '<span class="sample-values"><em>暂无样例值</em></span>';
  return `<span class="sample-values">示例：${values.map((value) => `<span class="sample-chip">${e(truncate(value, 16))}</span>`).join('')}</span>`;
}

function fieldRow(source, meta, mapping) {
  const column = mapping.fields?.[meta.key];
  return `
    <div class="map-row">
      <div class="map-label">
        <strong>${e(meta.label)}${meta.required ? ' <span class="req">*</span>' : ''}</strong>
        <small>${e(meta.hint)}</small>
      </div>
      <div class="map-control">
        <select class="select" data-map-field="${meta.key}" aria-label="${e(meta.label)}对应列">
          ${headerOptions(source.headers, Number.isInteger(column) ? column : null)}
        </select>
        ${sampleLine(source, Number.isInteger(column) ? column : null)}
      </div>
    </div>`;
}

function guessAmountColumns(headers) {
  let income = null;
  let expense = null;
  headers.forEach((header, index) => {
    const text = String(header).replace(/\s/g, '');
    if (income === null && /(收入|收款|贷方|进账).*(金额|额)?/.test(text)) income = index;
    if (expense === null && /(支出|付款|借方|出账).*(金额|额)?/.test(text)) expense = index;
  });
  return { income, expense };
}

function mappingPanelMarkup() {
  const source = wizard.sources.find((item) => item.id === wizard.activeId);
  if (!source) {
    return `<div class="card mapping-panel"><div class="empty-state" style="padding:80px 20px"><div class="empty-icon">${icon('layers')}</div><h3>选择一个数据表</h3><p>从左侧选择数据表后，在这里确认字段对应关系。</p></div></div>`;
  }
  const mapping = wizard.mappings[source.id] || normalizeMapping(source.suggested_mapping);
  const errors = mappingErrors(source, mapping);
  const checked = wizard.selected.has(source.id);
  const headerCandidates = source.header_candidates || [];
  const headerRowOptions = headerCandidates.map((row) => {
    const previewText = (row.c || []).slice(0, 3).filter(Boolean).join(' · ');
    return `<option value="${Number(row.n)}" ${Number(row.n) === Number(source.header_row) ? 'selected' : ''}>第 ${row.n} 行${previewText ? `：${e(truncate(previewText, 18))}` : ''}</option>`;
  }).join('');
  const currentHeaderNotInOptions = !headerCandidates.some((row) => Number(row.n) === Number(source.header_row));
  const amount = mapping.amount || { mode: 'single', column: null };
  const direction = mapping.direction || { mode: 'unset', column: null, value: null };
  const summaryFields = ['date', 'category', 'department', 'counterparty', 'payment_method', 'doc_no', 'note'];
  const sections = [
    { title: '核心字段', iconName: 'tag', fields: ['date'] },
    { title: '经营信息', iconName: 'building', fields: ['category', 'department'] },
    { title: '补充信息', iconName: 'note', fields: ['counterparty', 'payment_method', 'doc_no', 'note'] },
  ];
  const sampleLimit = 12;
  const headers = source.headers || [];
  const preview = source.sample_rows || [];
  return `
    <section class="card mapping-panel" id="mappingPanel">
      <div class="mapping-panel-head">
        <div>
          <h3>${e(sourceItemTitle(source))}</h3>
          <p>${n0(source.row_count)} 行 · ${headers.length} 列${checked ? '' : ' · 当前未勾选，不会导入'}</p>
        </div>
        <div class="mapping-actions">
          <button type="button" class="btn btn-ghost btn-sm" data-action="apply-same">${icon('copy')}套用到同结构表</button>
          <button type="button" class="btn btn-soft btn-sm" data-action="auto-map-all">${icon('sparkles')}全部智能匹配</button>
        </div>
      </div>
      <div class="provenance">
        <span class="provenance-file">${icon(source.presence === 'body' ? 'mail' : 'file-xls')}<span>${e(source.file_name || source.display_name)}${source.sheet_name ? ` · ${e(source.sheet_name)}` : ''}</span></span>
        ${source.email ? `<span class="chip chip-blue">${icon('mail')}${e(truncate(source.email.sender || source.email.subject || 'Outlook 邮件', 30))}</span>` : ''}
        <label class="header-select">表头行
          ${headerRowOptions && !currentHeaderNotInOptions
            ? `<select class="select" data-header-row="${e(source.id)}" aria-label="表头行">${headerRowOptions}</select>`
            : `<span class="chip">第 ${source.header_row} 行</span>`}
        </label>
      </div>
      <div class="mapping-body">
        ${sections.map((section) => `
          <div class="mapping-section">
            <div class="mapping-section-head">${icon(section.iconName)}${section.title}<span>${section.title === '核心字段' ? '必填' : '可选'}</span></div>
            ${section.fields.map((key) => fieldRow(source, FIELD_META.find((item) => item.key === key), mapping)).join('')}
          </div>
        `).join('')}
        <div class="mapping-section">
          <div class="mapping-section-head">${icon('wallet')}金额与收支<span>必填</span></div>
          <div class="map-row">
            <div class="map-label"><strong>金额列 <span class="req">*</span></strong><small>单列金额，或收入/支出两列</small></div>
            <div class="map-control">
              <select class="select" data-amount-mode aria-label="金额列模式">
                <option value="single" ${amount.mode !== 'split' ? 'selected' : ''}>单列金额（配合收支类型）</option>
                <option value="split" ${amount.mode === 'split' ? 'selected' : ''}>收入金额 / 支出金额双列</option>
              </select>
              ${amount.mode === 'split' ? `
                <div class="map-extra">
                  <select class="select" data-map-amount="income" aria-label="收入金额列">${headerOptions(headers, Number.isInteger(amount.income_column) ? amount.income_column : null, '— 收入金额列 —')}</select>
                  <select class="select" data-map-amount="expense" aria-label="支出金额列">${headerOptions(headers, Number.isInteger(amount.expense_column) ? amount.expense_column : null, '— 支出金额列 —')}</select>
                </div>
                ${sampleLine(source, Number.isInteger(amount.income_column) ? amount.income_column : null)}
                ${sampleLine(source, Number.isInteger(amount.expense_column) ? amount.expense_column : null)}
              ` : `
                <select class="select" data-map-amount="single" aria-label="金额对应列">${headerOptions(headers, Number.isInteger(amount.column) ? amount.column : null)}</select>
                ${sampleLine(source, Number.isInteger(amount.column) ? amount.column : null)}
              `}
            </div>
          </div>
          ${amount.mode === 'split' ? `
            <div class="notice notice-info" style="margin:6px 0 2px">${icon('info')}<span>系统会按两个金额列自动拆分为收入/支出记录，不需要再指定收支类型列。</span></div>
          ` : `
            <div class="map-row">
              <div class="map-label"><strong>收支类型 <span class="req">*</span></strong><small>收入或支出的判断方式</small></div>
              <div class="map-control">
                <div class="segmented segmented-sm" role="group" aria-label="收支类型来源">
                  <button type="button" data-dir-mode="column" class="${direction.mode === 'column' ? 'active' : ''}">按列识别</button>
                  <button type="button" data-dir-mode="income" class="${direction.mode === 'fixed' && direction.value === 'income' ? 'active' : ''}">整表收入</button>
                  <button type="button" data-dir-mode="expense" class="${direction.mode === 'fixed' && direction.value === 'expense' ? 'active' : ''}">整表支出</button>
                </div>
                ${direction.mode === 'column' ? `
                  <select class="select" data-map-direction-column aria-label="收支类型对应列">${headerOptions(headers, Number.isInteger(direction.column) ? direction.column : null, '— 选择收支类型列 —')}</select>
                  ${sampleLine(source, Number.isInteger(direction.column) ? direction.column : null)}
                ` : direction.mode === 'fixed' ? `
                  <span class="field-hint">整张表将按“${direction.value === 'income' ? '收入' : '支出'}”导入。</span>
                ` : `<span class="field-hint" style="color:var(--gold-600)">尚未选择收支类型来源。</span>`}
              </div>
            </div>
          `}
        </div>
        <details class="source-preview">
          <summary>${icon('eye')}查看源数据样例（前 ${Math.min(preview.length, 5)} 行，${Math.min(headers.length, sampleLimit)} 列）</summary>
          <div class="mini-table-wrap">
            <table class="mini-table">
              <thead><tr><th class="mini-row-num">行</th>${headers.slice(0, sampleLimit).map((header) => `<th>${e(header)}</th>`).join('')}${headers.length > sampleLimit ? '<th>…</th>' : ''}</tr></thead>
              <tbody>
                ${preview.slice(0, 5).map((row) => `
                  <tr><td class="mini-row-num">${row.n}</td>${headers.slice(0, sampleLimit).map((_, index) => `<td>${e(truncate(row.c?.[index] ?? '', 18))}</td>`).join('')}${headers.length > sampleLimit ? '<td>…</td>' : ''}</tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </details>
      </div>
      <div class="mapping-status ${errors.length ? 'attention' : 'ok'}">
        ${icon(errors.length ? 'alert' : 'check-circle')}
        <span>${errors.length ? errors.slice(0, 2).join('；') : `字段映射完整${source.mapping_source === 'profile' ? '，已套用历史映射' : ''}，可以开始校验。`}</span>
        ${errors.length ? '<span style="margin-left:auto">请补全必填项</span>' : ''}
      </div>
      <div class="mapping-foot">
        <span class="hint">已勾选 ${wizard.selected.size} 个表 · 映射会在导入后自动记住</span>
        <div class="actions">
          <button type="button" class="btn btn-ghost btn-sm" data-action="discard-session">重新选择</button>
          <button type="button" class="btn btn-primary" data-action="continue-validation" ${wizard.selected.size ? '' : 'disabled'}>${icon('arrow-right')}校验数据</button>
        </div>
      </div>
    </section>`;
}

function mappingStep() {
  const failed = errorsFromSession(wizard.session);
  const selected = wizard.selected.size;
  const incomplete = selectedSources().filter((source) => mappingErrors(source, wizard.mappings[source.id]).length).length;
  return `
    <div class="import-page page-enter">
      <div class="wizard-top">
        ${stepper()}
        <a class="btn btn-ghost btn-sm" href="#/history">${icon('history')}导入记录</a>
      </div>
      ${pageHead('确认字段映射。', '系统已经自动匹配，请快速核对业务日期、金额和收支类型。红色星号是必填字段。', `
        <div class="toolbar-actions">
          <span class="chip ${incomplete ? 'chip-gold' : 'chip-green'}">${incomplete ? `${incomplete} 个表待补全` : '映射已就绪'}</span>
        </div>`)}
      ${failed.length ? `<div class="notice notice-warning">${icon('alert')}<span>有 ${failed.length} 个文件未识别到可导入数据：${e(failed.slice(0, 2).map((item) => item.name).join('、'))}${failed.length > 2 ? ' 等' : ''}。其余数据表可以继续导入。</span></div>` : ''}
      <div class="import-layout">
        <aside class="card source-panel" id="sourcePanel">${sourcePanelMarkup()}</aside>
        ${mappingPanelMarkup()}
      </div>
    </div>`;
}

function sourceMap() {
  return new Map(wizard.sources.map((source) => [source.id, source]));
}

function combinedSamples(validation) {
  const map = sourceMap();
  const rows = [];
  for (const source of validation.sources || []) {
    const name = map.get(source.source_id)?.display_name || source.display_name || '数据表';
    for (const sample of source.samples || []) {
      rows.push({ ...sample, sourceName: name, sourceId: source.source_id });
    }
  }
  return rows;
}

function combinedIssues(validation) {
  const map = sourceMap();
  const rows = [];
  for (const source of validation.sources || []) {
    const name = map.get(source.source_id)?.display_name || source.display_name || '数据表';
    for (const issue of source.issues || []) {
      rows.push({ ...issue, sourceName: name, kind: 'issue' });
    }
    for (const warning of source.warnings || []) {
      rows.push({ status: 'warning', status_label: '提示', message: warning.message, row: warning.row, field: '', sourceName: name, kind: 'warning' });
    }
  }
  return rows;
}

function validationRowsMarkup(validation) {
  const rows = combinedSamples(validation);
  if (!rows.length) return '<div class="validation-empty">没有可预览的数据。</div>';
  return `
    <div class="validation-table-wrap">
      <table class="validation-table">
        <thead><tr><th>数据表</th><th>行</th><th>日期</th><th>收支</th><th>类别</th><th>部门</th><th style="text-align:right">金额</th><th>状态</th><th>提示</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td><span class="truncate" title="${e(row.sourceName)}" style="display:block;max-width:150px">${e(row.sourceName)}</span></td>
              <td class="num">${row.row}</td>
              <td class="num">${e(row.business_date || '—')}</td>
              <td>${row.direction_label ? `<span class="type-pill ${row.direction === 'income' ? 'income' : 'expense'}">${e(row.direction_label)}</span>` : '—'}</td>
              <td><span class="truncate" title="${e(row.category || '')}" style="display:block;max-width:120px">${e(row.category || '—')}</span></td>
              <td><span class="truncate" title="${e(row.department || '')}" style="display:block;max-width:94px">${e(row.department || '—')}</span></td>
              <td class="money">${row.amount ? `¥${e(row.amount)}` : '—'}</td>
              <td>${statusPill(row.status, row.status_label)}</td>
              <td><span class="issue-text ${row.status === 'warning' || row.status === 'duplicate' ? 'warning-text' : ''}" title="${e(row.message || '')}">${e(truncate(row.message || '—', 38))}</span></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

function issueRowsMarkup(validation) {
  const rows = combinedIssues(validation);
  if (!rows.length) return '<div class="validation-empty">没有发现异常或需要提示的数据。</div>';
  return `
    <div class="validation-table-wrap">
      <table class="validation-table" style="min-width:620px">
        <thead><tr><th>数据表</th><th>行</th><th>类型</th><th>字段</th><th>说明</th></tr></thead>
        <tbody>
          ${rows.slice(0, 500).map((row) => `
            <tr>
              <td><span class="truncate" title="${e(row.sourceName)}" style="display:block;max-width:160px">${e(row.sourceName)}</span></td>
              <td class="num">${row.row}</td>
              <td>${statusPill(row.status, row.status_label)}</td>
              <td>${e(row.field || '—')}</td>
              <td><span class="issue-text ${row.status === 'warning' ? 'warning-text' : ''}">${e(row.message || '—')}</span></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

function validationStep() {
  const validation = wizard.validation;
  if (!validation) return '';
  const summary = validation.summary;
  const committing = wizard.committing;
  const selectedCount = wizard.selected.size;
  const canCommit = summary.importable > 0 && !wizard.revalidating && !committing && validation.duplicate_policy === wizard.policy;
  const issueCount = summary.invalid + summary.duplicate + summary.warning;
  return `
    <div class="import-page page-enter">
      <div class="wizard-top">
        ${stepper()}
        <a class="btn btn-ghost btn-sm" href="#/history">${icon('history')}导入记录</a>
      </div>
      ${pageHead('检查数据，再落账。', `已校验 ${selectedCount} 个数据表。异常行不会被写入；重复行按你的选择处理。`)}
      <div class="validation-summary">
        <div class="validation-stat neutral"><span>${icon('layers')}数据行</span><strong>${n0(summary.record_count)}</strong></div>
        <div class="validation-stat good"><span>${icon('check-circle')}可导入</span><strong>${n0(summary.importable)}</strong></div>
        <div class="validation-stat warn"><span>${icon('info')}重复</span><strong>${n0(summary.duplicate)}</strong></div>
        <div class="validation-stat bad"><span>${icon('alert')}异常</span><strong>${n0(summary.invalid)}</strong></div>
        <div class="validation-stat warn"><span>${icon('info')}提示</span><strong>${n0(summary.warning)}</strong></div>
      </div>
      <div class="validation-layout">
        <section class="card validation-main">
          <div class="policy-bar">
            <div class="policy-copy">${icon('shield')}<span><strong>重复数据处理</strong> · 判重会同时检查本批次和已有流水</span></div>
            <div class="segmented segmented-sm" role="group" aria-label="重复数据处理方式">
              <button type="button" data-policy="skip" class="${wizard.policy === 'skip' ? 'active' : ''}" ${wizard.revalidating || committing ? 'disabled' : ''}>跳过重复</button>
              <button type="button" data-policy="update" class="${wizard.policy === 'update' ? 'active' : ''}" ${wizard.revalidating || committing ? 'disabled' : ''}>更新已有</button>
            </div>
          </div>
          <div class="validation-tabs" role="tablist">
            <button type="button" class="validation-tab active" data-validation-tab="data">预览数据 <span class="count">${n0(summary.record_count)}</span></button>
            <button type="button" class="validation-tab" data-validation-tab="issues">异常与提示 <span class="count">${n0(issueCount)}</span></button>
          </div>
          ${wizard.revalidating ? `<div class="loading-block"><span class="loading-spinner dark"></span>正在按新规则重新校验…</div>` : `
            <div id="validationTableView">${validationRowsMarkup(validation)}</div>
            <div id="validationIssuesView" class="hidden">${issueRowsMarkup(validation)}</div>
          `}
          <div class="notice notice-info" style="margin:13px 17px 15px">${icon('info')}<span>${summary.invalid ? `有 ${n0(summary.invalid)} 行异常不会导入，可下载校验明细修正源文件后重新导入。` : '校验通过。' }${wizard.policy === 'update' && summary.updateable ? ` 有 ${n0(summary.updateable)} 条重复记录将更新已有数据。` : ''}${summary.warning ? ` ${n0(summary.warning)} 行带有提示，但可以正常导入。` : ''}</span></div>
        </section>
        <aside class="card validation-side">
          <h3>本次数据表</h3>
          <div class="validation-side-list">
            ${(validation.sources || []).map((source) => {
              const full = sourceMap().get(source.source_id);
              return `
                <div class="validation-source">
                  <div class="validation-source-top"><strong title="${e(full?.display_name || source.display_name || '')}">${e(full?.display_name || source.display_name || '数据表')}</strong>${source.invalid_count ? `<span class="status-pill status-invalid">${n0(source.invalid_count)} 异常</span>` : '<span class="status-pill status-valid">通过</span>'}</div>
                  <div class="validation-source-stats"><span>可导入 <b>${n0(source.stats.importable)}</b></span><span>重复 <i>${n0(source.stats.duplicate)}</i></span><span>${n0(source.row_count)} 行</span></div>
                </div>`;
            }).join('')}
          </div>
          <div class="validation-side-note">${icon('shield')}<span>点击“确认导入”后才会写入本地数据库。之前的步骤不会修改任何流水。</span></div>
          ${(summary.invalid || summary.duplicate || summary.warning) ? `<button type="button" class="btn btn-secondary btn-sm btn-block" data-action="download-validation">${icon('download')}下载校验明细</button>` : ''}
        </aside>
      </div>
      <div class="validation-actions">
        <button type="button" class="btn btn-ghost" data-action="back-mapping" ${committing ? 'disabled' : ''}>${icon('chevron-left')}返回映射</button>
        <span class="summary-label">将写入 <strong>${n0(summary.importable)}</strong> 条${summary.updateable ? `，其中更新 ${n0(summary.updateable)} 条` : ''}</span>
        <span class="spacer"></span>
        <button type="button" class="btn btn-primary btn-lg" data-action="commit-import" ${canCommit ? '' : 'disabled'}>
          ${committing ? '<span class="loading-spinner"></span>正在写入…' : `${icon('check')}确认导入${summary.importable ? ` ${n0(summary.importable)} 条` : ''}`}
        </button>
      </div>
    </div>`;
}

function successStep() {
  const batch = wizard.batch;
  if (!batch) return startStep();
  const sourceCount = batch.source_count || batch.sources?.length || 0;
  const errorSources = (batch.sources || []).filter((source) => source.error_report);
  return `
    <div class="import-page page-enter">
      <div class="wizard-top">${stepper()}<a class="btn btn-ghost btn-sm" href="#/history">${icon('history')}查看全部导入记录</a></div>
      <section class="card import-success">
        <div class="success-mark">${icon('check')}</div>
        <p class="eyebrow">导入完成</p>
        <h2>数据已经进入工作台。</h2>
        <p>${n0(sourceCount)} 个数据表已处理，经营总览与明细已同步更新。</p>
        <div class="success-stats">
          <div class="success-stat good"><span>新增流水</span><strong>${n0(batch.imported_rows)}</strong></div>
          <div class="success-stat"><span>更新已有</span><strong>${n0(batch.updated_rows)}</strong></div>
          <div class="success-stat"><span>重复跳过</span><strong>${n0(batch.duplicate_rows)}</strong></div>
          <div class="success-stat ${batch.invalid_rows ? 'bad' : ''}"><span>异常未导入</span><strong>${n0(batch.invalid_rows)}</strong></div>
        </div>
        <div class="success-actions">
          <a class="btn btn-primary btn-lg" href="#/dashboard">${icon('grid')}查看经营总览</a>
          <a class="btn btn-secondary btn-lg" href="#/records?batch_id=${encodeURIComponent(batch.id)}">${icon('list')}查看本次流水</a>
          <button type="button" class="btn btn-ghost btn-lg" data-action="start-import">${icon('plus')}继续导入</button>
        </div>
        <div class="success-batch">
          <h3>本次数据表</h3>
          ${(batch.sources || []).map((source) => `
            <div class="success-source">
              ${icon(source.error_report ? 'alert' : 'check-circle')}
              <span class="name" title="${e(source.file_name)}">${e(source.file_name)}${source.sheet_name ? ` · ${e(source.sheet_name)}` : ''}</span>
              <span class="meta">新增 ${n0(source.imported_rows)} · 重复 ${n0(source.duplicate_rows)} · 异常 ${n0(source.invalid_rows)}</span>
              ${source.error_report ? `<button type="button" class="btn btn-ghost btn-sm" data-download-errors data-batch="${e(batch.id)}" data-source="${e(source.source_id)}" title="下载异常明细">${icon('download')}</button>` : ''}
            </div>`).join('')}
        </div>
        ${errorSources.length ? `<div class="notice notice-warning modal-note" style="text-align:left">${icon('alert')}<span>部分数据表存在异常行，已保留完整异常明细。可下载后在 Excel 中修正，再重新导入。</span></div>` : ''}
      </section>
    </div>`;
}

function renderWizard({ scroll = false } = {}) {
  if (!currentContainer) return;
  let content = startStep();
  if (wizard.step === 'mapping') content = mappingStep();
  else if (wizard.step === 'validating') content = validatingStep();
  else if (wizard.step === 'validation') content = validationStep();
  else if (wizard.step === 'success') content = successStep();
  currentContainer.innerHTML = content;
  if (scroll) window.scrollTo({ top: 0, behavior: 'smooth' });
}

function validatingStep() {
  return `
    <div class="import-page page-enter">
      <div class="wizard-top">${stepper()}<a class="btn btn-ghost btn-sm" href="#/history">${icon('history')}导入记录</a></div>
      ${pageHead('正在校验数据…', '检查日期、金额、收支类型与重复记录。源文件不会被改动。')}
      <section class="card" style="padding:20px">
        <div class="loading-block" style="min-height:320px;flex-direction:column">
          <span class="loading-spinner dark" style="width:24px;height:24px;border-width:3px"></span>
          <div style="text-align:center"><strong style="display:block;color:var(--ink-soft);font-size:12px">正在逐行核对 ${n0(wizard.selected.size)} 个数据表</strong><span style="font-size:10px;color:var(--faint)">通常只需要几秒钟，请不要关闭窗口。</span></div>
        </div>
      </section>
    </div>`;
}

let currentContainer = null;
let previewSequence = 0;

function collectSelections() {
  return selectedSources().map((source) => ({
    source_id: source.id,
    mapping: wizard.mappings[source.id],
  }));
}

function validateAllWithToast() {
  const invalid = selectedSources().filter((source) => mappingErrors(source, wizard.mappings[source.id]).length);
  if (!wizard.selected.size) {
    toast('请至少勾选一个数据表', 'warning');
    return false;
  }
  if (invalid.length) {
    wizard.activeId = invalid[0].id;
    renderWizard();
    toast(`还有 ${invalid.length} 个数据表未完成字段映射`, 'warning');
    return false;
  }
  return true;
}

async function runPreview() {
  if (!validateAllWithToast()) return;
  const sequence = ++previewSequence;
  wizard.step = 'validating';
  wizard.revalidating = false;
  renderWizard({ scroll: true });
  try {
    const result = await api.previewImport(wizard.session.session_id, {
      sources: collectSelections(),
      duplicate_policy: wizard.policy,
    });
    if (sequence !== previewSequence || !pageAlive) return;
    wizard.validation = result;
    wizard.step = 'validation';
    renderWizard({ scroll: true });
  } catch (error) {
    if (sequence !== previewSequence || !pageAlive) return;
    wizard.step = 'mapping';
    renderWizard();
    if (error.code === 'session_expired' || error.status === 410) {
      resetWizard();
      toast('这个导入会话已经过期，请重新选择文件', 'error');
    } else {
      toast(error.message, 'error');
    }
  }
}

async function revalidatePolicy(policy) {
  if (wizard.policy === policy || wizard.revalidating) return;
  const previousPolicy = wizard.policy;
  wizard.policy = policy;
  wizard.revalidating = true;
  renderWizard();
  const sequence = ++previewSequence;
  try {
    const result = await api.previewImport(wizard.session.session_id, {
      sources: collectSelections(),
      duplicate_policy: wizard.policy,
    });
    if (sequence !== previewSequence || !pageAlive) return;
    wizard.validation = result;
    wizard.revalidating = false;
    renderWizard();
  } catch (error) {
    if (sequence !== previewSequence || !pageAlive) return;
    wizard.policy = previousPolicy;
    wizard.revalidating = false;
    renderWizard();
    toast(error.message, 'error');
  }
}

async function commitImport() {
  if (wizard.committing || !wizard.validation || wizard.validation.summary.importable === 0) return;
  if (wizard.validation.duplicate_policy !== wizard.policy) {
    toast('重复处理规则已变化，请等待重新校验完成', 'warning');
    return;
  }
  wizard.committing = true;
  renderWizard();
  try {
    const result = await api.commitImport(wizard.session.session_id, {
      sources: collectSelections(),
      duplicate_policy: wizard.policy,
    });
    wizard.batch = result.batch;
    wizard.step = 'success';
    wizard.committing = false;
    if (pageAlive) {
      renderWizard({ scroll: true });
      toast(`已导入 ${n0(result.batch.imported_rows)} 条经营流水`, 'success');
    }
    refreshApp().catch(() => {});
  } catch (error) {
    wizard.committing = false;
    if (!pageAlive) return;
    renderWizard();
    toast(error.message, 'error');
  }
}

async function changeHeader(sourceId, headerRow) {
  const source = wizard.sources.find((item) => item.id === sourceId);
  if (!source || Number(source.header_row) === Number(headerRow)) return;
  try {
    const result = await api.changeHeader(wizard.session.session_id, sourceId, Number(headerRow));
    if (!pageAlive) return;
    const index = wizard.sources.findIndex((item) => item.id === sourceId);
    wizard.sources[index] = result.source;
    wizard.mappings[sourceId] = normalizeMapping(result.source.suggested_mapping);
    renderWizard();
    toast(`已使用第 ${headerRow} 行作为表头，请再确认字段映射`, 'success');
  } catch (error) {
    toast(error.message, 'error');
    renderWizard();
  }
}

function resetWizard() {
  previewSequence += 1;
  wizard = freshWizard();
  wizard.step = 'start';
  if (currentContainer) renderWizard({ scroll: true });
}

async function startNewImport() {
  wizard = freshWizard();
  renderWizard({ scroll: true });
}

async function discardSession() {
  if (wizard.session) {
    const confirmed = await confirmDialog({
      title: '重新选择文件？',
      message: '当前已识别和调整的字段映射会丢失，但还没有任何数据写入数据库。',
      confirmText: '重新选择',
      cancelText: '继续编辑',
      tone: 'danger',
    });
    if (!confirmed) return;
    try { await api.discardSession(wizard.session.session_id); } catch { /* 会话可能已过期 */ }
  }
  resetWizard();
}

function adoptSession(payload) {
  wizard.session = payload;
  wizard.sources = payload.sources || [];
  wizard.mappings = {};
  wizard.step = wizard.sources.length ? 'mapping' : 'start';
  wizard.validation = null;
  wizard.batch = null;
  wizard.policy = store.settings?.default_duplicate_policy || 'skip';
  wizard.uploading = false;
  wizard.revalidating = false;
  wizard.committing = false;
  wizard.uploadError = '';
  for (const source of wizard.sources) {
    wizard.mappings[source.id] = normalizeMapping(source.suggested_mapping);
  }
  // 邮件正文表格常是签名或通知表：默认只勾选附件/文件，正文表只有在映射完整时勾选。
  wizard.selected = new Set(
    wizard.sources
      .filter((source) => source.presence !== 'body' || mappingErrors(source, wizard.mappings[source.id]).length === 0)
      .map((source) => source.id),
  );
  if (!wizard.selected.size) wizard.selected = new Set(wizard.sources.map((source) => source.id));
  wizard.activeId = wizard.sources.find((source) => wizard.selected.has(source.id))?.id || wizard.sources[0]?.id || null;
}

function allowedFile(file) {
  const extension = String(file.name).split('.').pop().toLowerCase();
  return ACCEPTED.includes(extension);
}

async function uploadSelectedFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const unsupported = files.filter((file) => !allowedFile(file));
  if (unsupported.length) {
    toast(`暂不支持：${unsupported.map((file) => file.name).join('、')}`, 'warning');
  }
  const supported = files.filter(allowedFile);
  if (!supported.length) return;
  if (supported.length > 120) {
    toast('一次最多选择 120 个文件，请分批导入', 'warning');
    return;
  }
  const tooLarge = supported.find((file) => file.size > MAX_FILE);
  if (tooLarge) {
    toast(`文件“${tooLarge.name}”超过 80 MB，请拆分后再导入`, 'error');
    return;
  }
  if (supported.reduce((sum, file) => sum + file.size, 0) > MAX_UPLOAD) {
    toast('本次上传总大小超过 240 MB，请分批导入', 'error');
    return;
  }
  wizard.uploading = true;
  wizard.uploadProgress = 2;
  wizard.uploadNames = supported.map((file) => file.name);
  wizard.uploadError = '';
  wizard.session = null;
  renderWizard({ scroll: true });
  try {
    const payload = await uploadFiles(supported, (progress) => {
      wizard.uploadProgress = progress;
      const bar = currentContainer?.querySelector('#uploadProgressBar');
      const value = currentContainer?.querySelector('#uploadProgressValue');
      const label = currentContainer?.querySelector('#uploadProgressLabel');
      if (bar) bar.style.width = `${progress}%`;
      if (value) value.textContent = `${progress}%`;
      if (label && progress >= 100) label.textContent = '上传完成，正在识别表格…';
    });
    if (!pageAlive) return;
    wizard.uploading = false;
    adoptSession(payload);
    if (!wizard.sources.length) {
      wizard.uploadError = '没有识别到可导入的数据表。请检查文件是否包含表头行和至少一行数据。';
      renderWizard({ scroll: true });
      toast('没有识别到可导入的数据表', 'error');
      return;
    }
    renderWizard({ scroll: true });
    const failed = errorsFromSession(payload);
    if (failed.length) toast(`已识别 ${wizard.sources.length} 个数据表，${failed.length} 个文件需要留意`, 'warning', 5000);
    else toast(`已识别 ${wizard.sources.length} 个数据表，请确认字段映射`, 'success');
  } catch (error) {
    if (!pageAlive) return;
    wizard.uploading = false;
    wizard.uploadError = error.message || '上传失败';
    renderWizard();
    toast(wizard.uploadError, 'error');
  }
}

let pageAlive = false;

function handleClick(event) {
  const target = event.target;
  const actionButton = target.closest('[data-action]');
  if (actionButton) {
    const action = actionButton.dataset.action;
    if (action === 'choose-files') {
      currentContainer.querySelector('#importFiles')?.click();
      return;
    }
    if (action === 'auto-map-all') {
      wizard.sources.forEach((source) => {
        wizard.mappings[source.id] = normalizeMapping(source.suggested_mapping);
      });
      renderWizard();
      toast('已重新应用智能匹配，请确认结果', 'success');
      return;
    }
    if (action === 'apply-same') {
      const active = wizard.sources.find((source) => source.id === wizard.activeId);
      if (!active) return;
      const signature = JSON.stringify(active.headers);
      let applied = 0;
      wizard.sources.forEach((source) => {
        if (source.id !== active.id && JSON.stringify(source.headers) === signature) {
          wizard.mappings[source.id] = clone(wizard.mappings[active.id]);
          applied += 1;
        }
      });
      renderWizard();
      toast(applied ? `已套用到 ${applied} 个同结构数据表` : '没有找到相同表头的数据表', applied ? 'success' : 'info');
      return;
    }
    if (action === 'continue-validation') {
      runPreview();
      return;
    }
    if (action === 'discard-session') {
      discardSession();
      return;
    }
    if (action === 'back-mapping') {
      wizard.step = 'mapping';
      renderWizard({ scroll: true });
      return;
    }
    if (action === 'commit-import') {
      commitImport();
      return;
    }
    if (action === 'download-validation') {
      download('/api/imports/' + encodeURIComponent(wizard.session.session_id) + '/preview-export', {
        method: 'POST',
        body: { sources: collectSelections(), duplicate_policy: wizard.policy },
        filename: '导入校验结果.csv',
      }).catch((error) => toast(error.message, 'error'));
      return;
    }
    if (action === 'start-import') {
      startNewImport();
      return;
    }
    if (action === 'download-errors') {
      const { batch, source } = actionButton.dataset;
      if (batch && source) {
        downloadWithPath(`/api/batches/${encodeURIComponent(batch)}/sources/${encodeURIComponent(source)}/errors.csv`)
          .catch((error) => toast(error.message, 'error'));
      }
      return;
    }
  }
  const policyButton = target.closest('[data-policy]');
  if (policyButton) {
    revalidatePolicy(policyButton.dataset.policy);
    return;
  }
  const sourceButton = target.closest('[data-source-id]');
  if (sourceButton) {
    const id = sourceButton.dataset.sourceId;
    if (wizard.activeId !== id) {
      wizard.activeId = id;
      renderWizard();
    }
    return;
  }
  const dirMode = target.closest('[data-dir-mode]');
  if (dirMode) {
    const source = wizard.sources.find((item) => item.id === wizard.activeId);
    if (!source) return;
    const mapping = wizard.mappings[source.id];
    const mode = dirMode.dataset.dirMode;
    if (mode === 'column') {
      mapping.direction = { mode: 'column', column: mapping.direction?.column ?? null, value: null };
    } else {
      mapping.direction = { mode: 'fixed', column: null, value: mode };
    }
    renderWizard();
    return;
  }
  const tab = target.closest('[data-validation-tab]');
  if (tab) {
    currentContainer.querySelectorAll('[data-validation-tab]').forEach((button) => button.classList.toggle('active', button === tab));
    const isIssues = tab.dataset.validationTab === 'issues';
    currentContainer.querySelector('#validationTableView')?.classList.toggle('hidden', isIssues);
    currentContainer.querySelector('#validationIssuesView')?.classList.toggle('hidden', !isIssues);
    return;
  }
  const dropzone = target.closest('#dropzone');
  if (dropzone && !wizard.uploading && !target.closest('button') && !target.closest('a')) {
    currentContainer.querySelector('#importFiles')?.click();
  }
}

function handleChange(event) {
  const target = event.target;
  if (target.id === 'importFiles') {
    uploadSelectedFiles(target.files);
    return;
  }
  if (target.id === 'sourceSelectAll') {
    if (target.checked) wizard.selected = new Set(wizard.sources.map((source) => source.id));
    else wizard.selected = new Set();
    renderWizard();
    return;
  }
  if (target.matches('[data-source-check]')) {
    const id = target.dataset.sourceCheck;
    if (target.checked) wizard.selected.add(id);
    else wizard.selected.delete(id);
    renderWizard();
    return;
  }
  if (target.matches('[data-header-row]')) {
    changeHeader(target.dataset.headerRow, target.value);
    return;
  }
  if (target.matches('[data-map-field]')) {
    const source = wizard.sources.find((item) => item.id === wizard.activeId);
    if (!source) return;
    const mapping = wizard.mappings[source.id];
    const column = target.value === '' ? null : Number(target.value);
    mapping.fields[target.dataset.mapField] = Number.isInteger(column) ? column : null;
    renderWizard();
    return;
  }
  if (target.matches('[data-amount-mode]')) {
    const source = wizard.sources.find((item) => item.id === wizard.activeId);
    if (!source) return;
    const mapping = wizard.mappings[source.id];
    if (target.value === 'split') {
      const guessed = guessAmountColumns(source.headers || []);
      mapping.amount = { mode: 'split', income_column: guessed.income, expense_column: guessed.expense };
      mapping.direction = { mode: 'split', column: null, value: null };
    } else {
      mapping.amount = { mode: 'single', column: mapping.amount?.income_column ?? mapping.amount?.column ?? null };
      mapping.direction = { mode: 'unset', column: null, value: null };
    }
    renderWizard();
    return;
  }
  if (target.matches('[data-map-amount]')) {
    const source = wizard.sources.find((item) => item.id === wizard.activeId);
    if (!source) return;
    const mapping = wizard.mappings[source.id];
    const column = target.value === '' ? null : Number(target.value);
    const kind = target.dataset.mapAmount;
    if (kind === 'single') mapping.amount = { mode: 'single', column: Number.isInteger(column) ? column : null };
    else mapping.amount[kind === 'income' ? 'income_column' : 'expense_column'] = Number.isInteger(column) ? column : null;
    renderWizard();
    return;
  }
  if (target.matches('[data-map-direction-column]')) {
    const source = wizard.sources.find((item) => item.id === wizard.activeId);
    if (!source) return;
    const mapping = wizard.mappings[source.id];
    const column = target.value === '' ? null : Number(target.value);
    mapping.direction = { mode: 'column', column: Number.isInteger(column) ? column : null, value: null };
    renderWizard();
    return;
  }
}

function handleDragOver(event) {
  if (!pageAlive || wizard.step !== 'start' || wizard.uploading) return;
  const zone = event.target.closest('#dropzone');
  if (!zone) return;
  event.preventDefault();
  zone.classList.add('dragover');
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
}

function handleDragLeave(event) {
  const zone = event.target.closest('#dropzone');
  if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove('dragover');
}

function handleDrop(event) {
  if (!pageAlive || wizard.step !== 'start' || wizard.uploading) return;
  const zone = event.target.closest('#dropzone');
  if (!zone) return;
  event.preventDefault();
  zone.classList.remove('dragover');
  uploadSelectedFiles(event.dataTransfer?.files);
}

function handleKeydown(event) {
  if ((event.key === 'Enter' || event.key === ' ') && event.target.id === 'dropzone' && !wizard.uploading) {
    event.preventDefault();
    currentContainer?.querySelector('#importFiles')?.click();
  }
}

export function render(container, ctx) {
  if (wizard.step === 'success') wizard = freshWizard();
  if (!wizard.session && wizard.step !== 'start') wizard = freshWizard();
  currentContainer = container;
  pageAlive = true;
  wizard.uploadError = '';
  container.addEventListener('click', handleClick);
  container.addEventListener('change', handleChange);
  container.addEventListener('dragover', handleDragOver);
  container.addEventListener('dragleave', handleDragLeave);
  container.addEventListener('drop', handleDrop);
  container.addEventListener('keydown', handleKeydown);
  renderWizard();
  return () => {
    pageAlive = false;
    previewSequence += 1;
    if (wizard.step === 'validating') wizard.step = 'mapping';
    container.removeEventListener('click', handleClick);
    container.removeEventListener('change', handleChange);
    container.removeEventListener('dragover', handleDragOver);
    container.removeEventListener('dragleave', handleDragLeave);
    container.removeEventListener('drop', handleDrop);
    container.removeEventListener('keydown', handleKeydown);
    if (currentContainer === container) currentContainer = null;
    void ctx;
  };
}
