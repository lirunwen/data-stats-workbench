/** UI 基础工具：格式化、图标、弹窗、Toast 与表单辅助。 */

const HTML_ESCAPES = {
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
};

export function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value).replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

export const e = escapeHtml;

export function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

export function icon(name, className = '') {
  return `<svg class="icon ${escapeAttr(className)}" aria-hidden="true"><use href="#i-${escapeAttr(name)}"></use></svg>`;
}

const number0 = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 });
const number2 = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const numberCompact = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 });

export function toNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function n0(value) {
  return number0.format(toNumber(value));
}

export function n2(value) {
  return number2.format(toNumber(value));
}

export function formatMoney(cents, { symbol = true, decimals = 2 } = {}) {
  const amount = toNumber(cents) / 100;
  const negative = amount < 0;
  const text = decimals === 0 ? number0.format(Math.abs(amount)) : number2.format(Math.abs(amount));
  return `${negative ? '−' : ''}${symbol ? '¥' : ''}${text}`;
}

export function moneyParts(cents) {
  const amount = toNumber(cents) / 100;
  const negative = amount < 0;
  const absolute = Math.abs(amount);
  const [integer, fraction] = number2.format(absolute).split('.');
  return { negative, integer, fraction, sign: negative ? '−' : '', currency: '¥' };
}

export function kpiMoneyHTML(cents) {
  const parts = moneyParts(cents);
  return `<span class="currency">${parts.currency}</span><span class="number">${parts.sign}${parts.integer}</span><span class="decimals">.${parts.fraction}</span>`;
}

export function compactMoney(cents, { unit = true } = {}) {
  const yuan = Math.abs(toNumber(cents) / 100);
  const sign = toNumber(cents) < 0 ? '−' : '';
  if (yuan >= 100000000) return `${sign}${numberCompact.format(yuan / 100000000)}${unit ? '亿' : ''}`;
  if (yuan >= 10000) return `${sign}${numberCompact.format(yuan / 10000)}${unit ? '万' : ''}`;
  return `${sign}${number0.format(yuan)}${unit ? '' : ''}`;
}

export function formatPct(value, { sign = true, digits = 1 } = {}) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const number = Number(value);
  const prefix = sign && number > 0 ? '+' : '';
  return `${prefix}${number.toFixed(digits)}%`;
}

export function formatCount(value) {
  return n0(toNumber(value));
}

export function formatDateCN(value, { withYear = true } = {}) {
  if (!value) return '—';
  const parts = String(value).slice(0, 10).split('-');
  if (parts.length !== 3) return String(value);
  const [year, month, day] = parts;
  return withYear ? `${year}年${Number(month)}月${Number(day)}日` : `${Number(month)}月${Number(day)}日`;
}

export function formatDateShort(value) {
  if (!value) return '—';
  const parts = String(value).slice(0, 10).split('-');
  if (parts.length !== 3) return String(value);
  return `${parts[0]}.${parts[1]}.${parts[2]}`;
}

export function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 19).replace('T', ' ');
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function weekdayCN(value) {
  const date = value ? new Date(`${String(value).slice(0, 10)}T00:00:00`) : new Date();
  if (Number.isNaN(date.getTime())) return '';
  return ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'][date.getDay()];
}

export function relativeTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} 天前`;
  return formatDateCN(value);
}

export function greeting() {
  const hour = new Date().getHours();
  if (hour < 6) return '夜深了';
  if (hour < 11) return '上午好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

export function formatFileSize(bytes) {
  const size = toNumber(bytes);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function fileKind(name = '') {
  const extension = String(name).split('.').pop().toLowerCase();
  if (['msg', 'eml', 'oft'].includes(extension)) return 'mail';
  if (['xlsx', 'xls', 'xlsm', 'csv', 'tsv'].includes(extension)) return 'file-xls';
  return 'file';
}

export function statusClass(status) {
  return {
    completed: 'status-completed',
    partial: 'status-partial',
    duplicate: 'status-duplicate',
    failed: 'status-failed',
    pending: 'status-pending',
    invalid: 'status-invalid',
    warning: 'status-warning',
    valid: 'status-valid',
  }[status] || 'status-pending';
}

export function statusPill(status, label) {
  return `<span class="status-pill ${statusClass(status)}">${escapeHtml(label || status)}</span>`;
}

export function debounce(fn, delay = 300) {
  let timer = null;
  const debounced = (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), delay);
  };
  debounced.cancel = () => window.clearTimeout(timer);
  return debounced;
}

export function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

export function setButtonLoading(button, loading, loadingText = '处理中…') {
  if (!button) return () => {};
  if (loading) {
    if (button.dataset.originalHtml) return () => restoreButton(button);
    button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="loading-spinner"></span><span>${escapeHtml(loadingText)}</span>`;
    return () => restoreButton(button);
  }
  return () => restoreButton(button);
}

function restoreButton(button) {
  if (button?.dataset.originalHtml) {
    button.innerHTML = button.dataset.originalHtml;
    delete button.dataset.originalHtml;
  }
  if (button) button.disabled = false;
}

export function toast(message, type = 'info', timeout = 3800) {
  const root = document.getElementById('toastRoot');
  if (!root) return;
  const allowed = ['success', 'error', 'warning', 'info'];
  const tone = allowed.includes(type) ? type : 'info';
  const iconName = { success: 'check-circle', error: 'alert', warning: 'alert', info: 'info' }[tone];
  const element = document.createElement('div');
  element.className = `toast toast-${tone}`;
  element.setAttribute('role', tone === 'error' ? 'alert' : 'status');
  const iconWrap = document.createElement('span');
  iconWrap.innerHTML = icon(iconName);
  const copy = document.createElement('span');
  copy.className = 'toast-copy';
  copy.textContent = String(message || '操作已完成');
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'toast-close';
  close.setAttribute('aria-label', '关闭提示');
  close.textContent = '×';
  element.append(iconWrap.firstChild, copy, close);
  root.appendChild(element);
  while (root.children.length > 4) root.firstElementChild.remove();
  const remove = () => {
    if (!element.isConnected) return;
    element.classList.add('leaving');
    window.setTimeout(() => element.remove(), 240);
  };
  close.addEventListener('click', remove);
  if (timeout > 0) window.setTimeout(remove, timeout);
}

function syncBodyModalState() {
  const root = document.getElementById('modalRoot');
  document.body.classList.toggle('modal-open', Boolean(root?.querySelector('dialog[open]')));
}

/**
 * 打开原生 dialog 弹窗。body / footer 由调用方提供已转义 HTML。
 */
export function openModal({
  title = '',
  subtitle = '',
  body = '',
  footer = '',
  size = '',
  className = '',
  onMount = null,
  onClose = null,
  closeOnBackdrop = true,
} = {}) {
  const root = document.getElementById('modalRoot');
  const dialog = document.createElement('dialog');
  dialog.className = `modal ${size ? `modal-${size}` : ''} ${className}`.trim();
  dialog.innerHTML = `
    <div class="modal-header">
      <div>
        <h2>${escapeHtml(title)}</h2>
        ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
      </div>
      <button type="button" class="modal-close" data-modal-close aria-label="关闭弹窗">${icon('x')}</button>
    </div>
    <div class="modal-body">${body}</div>
    ${footer ? `<div class="modal-footer">${footer}</div>` : ''}
  `;
  root.appendChild(dialog);
  let closed = false;
  let locked = false;
  const close = (reason = 'programmatic') => {
    if (closed) return;
    closed = true;
    if (dialog.open) dialog.close(reason);
    window.setTimeout(() => {
      dialog.remove();
      syncBodyModalState();
      onClose?.(reason);
    }, 0);
  };
  dialog.addEventListener('click', (event) => {
    if (event.target.closest('[data-modal-close]')) {
      close('button');
      return;
    }
    if (event.target === dialog && closeOnBackdrop) close('backdrop');
  });
  dialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    if (!locked) close('escape');
  });
  dialog.addEventListener('close', () => {
    if (!closed) {
      closed = true;
      window.setTimeout(() => {
        dialog.remove();
        syncBodyModalState();
        onClose?.('native');
      }, 0);
    }
  });
  try { dialog.showModal(); } catch { dialog.setAttribute('open', ''); }
  syncBodyModalState();
  document.body.classList.add('modal-open');
  window.requestAnimationFrame(() => {
    const field = dialog.querySelector('[autofocus]')
      || dialog.querySelector('.modal-body input:not([type="hidden"]), .modal-body select, .modal-body textarea')
      || dialog.querySelector('.modal-footer .btn-primary, .modal-footer .btn-secondary, .modal-footer .btn-danger')
      || dialog.querySelector('.modal-close');
    field?.focus?.({ preventScroll: true });
  });
  onMount?.(dialog, { close, setLocked: (value) => { locked = Boolean(value); } });
  return { dialog, close, setLocked: (value) => { locked = Boolean(value); } };
}

export function confirmDialog({
  title = '请确认操作',
  message = '',
  confirmText = '确认',
  cancelText = '取消',
  tone = 'danger',
  warning = '',
} = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const modal = openModal({
      title,
      size: '',
      body: `
        <div class="confirm-copy">${message}</div>
        ${warning ? `<div class="confirm-warning">${icon('alert')}<span>${escapeHtml(warning)}</span></div>` : ''}
      `,
      footer: `
        <button type="button" class="btn btn-ghost" data-confirm-cancel>${escapeHtml(cancelText)}</button>
        <button type="button" class="btn ${tone === 'danger' ? 'btn-danger' : 'btn-primary'}" data-confirm-ok>${escapeHtml(confirmText)}</button>
      `,
      onMount(dialog) {
        dialog.querySelector('[data-confirm-cancel]').addEventListener('click', () => {
          finish(false);
          modal.close();
        });
        dialog.querySelector('[data-confirm-ok]').addEventListener('click', () => {
          finish(true);
          modal.close();
        });
      },
      onClose() { finish(false); },
    });
  });
}

export function openHelpModal() {
  openModal({
    title: '使用帮助',
    subtitle: '三步把 Excel 或 Outlook 邮件变成可分析的经营数据',
    size: 'lg',
    body: `
      <div class="help-hero">
        <span class="brand-mark">衡</span>
        <div>
          <h3>先看数据，再做决定</h3>
          <p>工作台会自动完成字段识别、校验、去重与汇总。原始文件不会离开这台电脑。</p>
        </div>
      </div>
      <div class="help-steps">
        <div class="help-step">
          <span class="help-step-num">1</span>
          <div><strong>导入文件</strong><p>把 .xlsx / .xls / .csv 拖进导入页，或选择 Outlook 的 .msg / .oft / .eml 邮件。邮件附件中的 Excel 会自动展开。</p></div>
        </div>
        <div class="help-step">
          <span class="help-step-num">2</span>
          <div><strong>确认字段映射</strong><p>系统会根据表头自动匹配。带红色星号的是必填字段；收支类型可选择按列识别，或整表固定为收入 / 支出。</p></div>
        </div>
        <div class="help-step">
          <span class="help-step-num">3</span>
          <div><strong>校验后导入</strong><p>先看异常和重复提示，再确认导入。异常行不会写入；重复行可选择跳过或更新已有记录。</p></div>
        </div>
      </div>
      <div class="help-divider"></div>
      <div class="help-faq">
        <div><strong>${icon('shield')}重复数据怎么判断？</strong><p>有单据编号时按“年份 + 收支 + 单据编号 + 金额 + 类别”判重；没有编号时按日期、收支、金额、类别、部门、往来单位和备注综合判断。</p></div>
        <div><strong>${icon('info')}为什么有行显示“异常”？</strong><p>通常是日期、金额或收支类型无法识别，或必填内容为空。下载异常明细，在 Excel 里修正后重新导入即可。</p></div>
        <div><strong>${icon('mail')}Outlook 邮件怎么准备？</strong><p>在 Outlook 中把邮件拖到桌面会得到 .msg 文件，可直接拖入。若只有 .pst / .ost 邮箱文件，请先在 Outlook 中把目标邮件另存为 .msg。</p></div>
        <div><strong>${icon('database')}数据保存到哪里？</strong><p>保存到本机数据目录中的 SQLite 数据库。设置页可查看路径并一键备份；升级程序不会动数据目录。</p></div>
      </div>
      <div class="help-note">${icon('lock')}<span>所有页面资源均随程序分发，不连接 CDN、字体服务或任何外部接口。浏览器的数据仅用于本机 127.0.0.1 服务。</span></div>
    `,
    footer: `<button type="button" class="btn btn-primary" data-modal-close>知道了</button>`,
  });
}

export function emptyState({ iconName = 'database', title = '暂无数据', description = '', action = '' } = {}) {
  return `
    <div class="empty-state">
      <div class="empty-icon">${icon(iconName, 'icon-xl')}</div>
      <h3>${escapeHtml(title)}</h3>
      ${description ? `<p>${escapeHtml(description)}</p>` : ''}
      ${action}
    </div>
  `;
}

export function errorPanel(message, { detail = '', retry = true } = {}) {
  return `
    <div class="card error-panel">
      <div class="empty-icon">${icon('alert', 'icon-xl')}</div>
      <h2>页面没有加载成功</h2>
      <p>${escapeHtml(message)}</p>
      ${detail ? `<code>${escapeHtml(detail)}</code>` : ''}
      ${retry ? `<button type="button" class="btn btn-secondary" style="margin-top:18px" data-action="reload-page">${icon('refresh')}重新加载</button>` : ''}
    </div>
  `;
}

export function kpiSparkPlaceholder() {
  return `<div class="kpi-spark"><div class="skeleton" style="width:100%;height:100%"></div></div>`;
}

export function parseHash() {
  const raw = window.location.hash.replace(/^#/, '') || '/dashboard';
  const [path, queryString = ''] = raw.split('?');
  return { path: path || '/dashboard', params: new URLSearchParams(queryString) };
}

export function buildHash(path, params = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  });
  const query = search.toString();
  return `#${path}${query ? `?${query}` : ''}`;
}

export function dateRangeLabel(start, end) {
  if (!start && !end) return '全部日期';
  if (start && !end) return `${formatDateCN(start)} 起`;
  if (!start && end) return `截至 ${formatDateCN(end)}`;
  return `${formatDateCN(start)} — ${formatDateCN(end)}`;
}

export function pluralRecords(count) {
  return `${n0(count)} 笔`;
}

export function todayISO() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function addDaysISO(isoDate, days) {
  const date = new Date(`${isoDate}T00:00:00`);
  date.setDate(date.getDate() + Number(days));
  return todayISOFromDate(date);
}

export function todayISOFromDate(date) {
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function truncate(value, max = 24) {
  const text = String(value || '');
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
