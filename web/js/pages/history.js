/** 导入记录：每次导入的来源、统计、异常明细与可追溯信息。 */
import { api, downloadWithPath } from '../api.js';
import {
  debounce, e, formatDateTime, icon, n0, openModal, relativeTime,
  statusPill, toast,
} from '../ui.js';

function fileKind(name = '') {
  return /\.(msg|eml|oft)$/i.test(name) ? 'mail' : 'file-xls';
}

async function openBatchDetail(batchId) {
  let batch;
  try {
    const result = await api.batch(batchId);
    batch = result.batch;
  } catch (error) {
    toast(error.message, 'error');
    return;
  }
  const labels = {
    completed: '已完成', partial: '部分导入', duplicate: '全部重复',
    failed: '未导入', pending: '处理中',
  };
  const invalidCount = Number(batch.invalid_rows || 0);
  const hasReports = (batch.sources || []).some((source) => source.error_report);
  const sourceMarkup = (batch.sources || []).map((source) => {
    const issues = source.issues || [];
    const warnings = source.warnings || [];
    return `
      <div class="batch-source-card">
        <div class="batch-source-head">
          ${icon(fileKind(source.file_name))}
          <strong title="${e(source.file_name)}">${e(source.file_name)}${source.sheet_name ? ` · ${e(source.sheet_name)}` : ''}</strong>
          ${source.error_report ? `<button type="button" class="btn btn-secondary btn-sm" data-download-source="${e(batch.id)}|${e(source.source_id)}">${icon('download')}异常明细</button>` : ''}
        </div>
        <div class="batch-source-meta">
          ${source.email_subject ? `${icon('mail')} ${e(source.email_subject)} · ` : ''}
          表头第 ${source.header_row} 行 · 共 ${n0(source.total_rows)} 行
        </div>
        <div class="batch-source-stats">
          <span class="ok">新增 ${n0(source.imported_rows)}</span>
          <span>更新 ${n0(source.updated_rows)}</span>
          <span>重复 ${n0(source.duplicate_rows)}</span>
          <span class="bad">异常 ${n0(source.invalid_rows)}</span>
          <span>提示 ${n0(source.warning_rows)}</span>
        </div>
        ${(issues.length || warnings.length) ? `
          <details class="batch-errors">
            <summary>查看异常与提示（显示前 ${Math.min(issues.length + warnings.length, 200)} 条）</summary>
            ${issues.slice(0, 100).map((issue) => `
              <div class="batch-error-row"><b>第 ${issue.row} 行</b><span>${e(issue.message || '异常')}</span></div>
            `).join('')}
            ${warnings.slice(0, 100).map((warning) => `
              <div class="batch-error-row"><b>第 ${warning.row} 行</b><span>${e(warning.message || '提示')}</span></div>
            `).join('')}
          </details>` : ''}
      </div>`;
  }).join('');
  const modal = openModal({
    title: '导入详情',
    subtitle: `${batch.id} · ${formatDateTime(batch.created_at)}`,
    size: 'lg',
    body: `
      <div class="batch-detail-meta">
        <div class="batch-meta-item"><span>导入状态</span><strong>${statusPill(batch.status, labels[batch.status] || batch.status_label)}</strong></div>
        <div class="batch-meta-item"><span>数据表</span><strong>${n0(batch.source_count)} 个 · ${n0(batch.total_rows)} 行</strong></div>
        <div class="batch-meta-item"><span>处理结果</span><strong>新增 ${n0(batch.imported_rows)} · 更新 ${n0(batch.updated_rows)}</strong></div>
        <div class="batch-meta-item"><span>判重策略</span><strong>${batch.duplicate_policy === 'update' ? '更新已有记录' : '跳过重复记录'}</strong></div>
      </div>
      ${invalidCount ? `<div class="notice notice-warning modal-note">${icon('alert')}<span>本次有 ${n0(invalidCount)} 行异常数据未写入。异常不会影响其他已导入记录；可下载异常明细后在 Excel 中修正。</span></div>` : ''}
      <div class="batch-source-list">${sourceMarkup || '<div class="validation-empty">没有数据表明细。</div>'}</div>
      <div class="batch-detail-extra">
        数据来源：${e((batch.sources || []).map((source) => source.file_name).filter(Boolean).join('、') || '—')}<br>
        导入耗时：${batch.duration_ms < 1000 ? '少于 1 秒' : `${n0(batch.duration_ms)} 毫秒`} · 处理规则：${batch.duplicate_policy === 'update' ? '重复时更新已有' : '重复时跳过'}<br>
        删除流水不会回溯修改本记录中的历史统计，但相应的流水会从经营汇总中移除。
      </div>
    `,
    footer: `
      <span class="left">记录编号：${e(batch.id)}</span>
      ${hasReports ? `<button type="button" class="btn btn-secondary" data-download-batch>${icon('download')}下载全部异常明细</button>` : ''}
      <button type="button" class="btn btn-primary" data-modal-close>关闭</button>
    `,
    onMount(dialog) {
      dialog.addEventListener('click', (event) => {
        const downloadSource = event.target.closest('[data-download-source]');
        if (downloadSource) {
          const [batchIdValue, sourceId] = downloadSource.dataset.downloadSource.split('|');
          downloadWithPath(`/api/batches/${encodeURIComponent(batchIdValue)}/sources/${encodeURIComponent(sourceId)}/errors.csv`)
            .catch((error) => toast(error.message, 'error'));
          return;
        }
        if (event.target.closest('[data-download-batch]')) {
          downloadWithPath(`/api/batches/${encodeURIComponent(batch.id)}/errors.zip`)
            .catch((error) => toast(error.message, 'error'));
        }
      });
    },
  });
  void modal;
}

function emptyHistory() {
  return `
    <div class="empty-state" style="padding:64px 24px">
      <div class="empty-icon">${icon('history', 'icon-xl')}</div>
      <h3>还没有导入记录</h3>
      <p>每次导入都会在这里留下可追溯的文件、校验结果与异常明细。</p>
      <a class="btn btn-primary btn-sm" href="#/import">${icon('upload')}去导入数据</a>
    </div>`;
}

function batchRowMarkup(batch) {
  const kind = fileKind(batch.first_file);
  return `
    <button type="button" class="batch-row" data-batch-id="${e(batch.id)}">
      <span class="batch-file">
        <span class="batch-icon ${kind === 'mail' ? 'mail' : ''}">${icon(kind)}</span>
        <span class="batch-file-copy">
          <strong title="${e(batch.first_file || '批量导入')}">${e(batch.first_file || '批量导入')}</strong>
          <span>${n0(batch.source_count)} 个数据表${batch.first_sheet ? ` · ${e(batch.first_sheet)}` : ''}${batch.is_demo ? ' · 演示数据' : ''}</span>
        </span>
      </span>
      <span class="batch-stats">
        <span class="batch-stat"><span>数据行</span><strong>${n0(batch.total_rows)}</strong></span>
        <span class="batch-stat good"><span>新增</span><strong>${n0(batch.imported_rows)}</strong></span>
        <span class="batch-stat"><span>重复</span><strong>${n0(batch.duplicate_rows)}</strong></span>
        <span class="batch-stat bad"><span>异常</span><strong>${n0(batch.invalid_rows)}</strong></span>
      </span>
      <span class="batch-date">${relativeTime(batch.created_at)}<small>${formatDateTime(batch.created_at)}</small></span>
      <span class="batch-end">${statusPill(batch.status, batch.status_label)}${icon('chevron-right', 'batch-chevron-icon')}</span>
    </button>`;
}

export function render(container, ctx) {
  let q = (ctx.params.get('q') || '').trim();
  let status = ['completed', 'partial', 'duplicate', 'failed'].includes(ctx.params.get('status')) ? ctx.params.get('status') : '';
  let page = Math.max(1, Number(ctx.params.get('page')) || 1);
  const pageSize = 10;
  let disposed = false;
  let sequence = 0;
  let currentPage = 1;
  let currentTotal = 0;
  let abortController = null;
  const autoOpen = ctx.params.get('open');
  const searchDebounced = debounce(() => {
    page = 1;
    syncUrl();
    load();
  }, 320);

  container.innerHTML = `
    <div class="history-page">
      <div class="page-toolbar">
        <div>
          <h2>导入记录</h2>
          <p id="historySubtitle">每次导入的文件、校验结果和异常都留在这里。</p>
        </div>
        <div class="toolbar-actions">
          <a class="btn btn-secondary btn-sm" href="/api/import-template" download>${icon('download')}导入模板</a>
          <a class="btn btn-primary btn-sm" href="#/import">${icon('upload')}新建导入</a>
        </div>
      </div>
      <section class="card history-card">
        <div class="history-filters">
          <div class="history-search">
            ${icon('search')}
            <input class="input" id="historySearch" type="search" placeholder="搜索文件名、工作表或批次编号" value="${e(q)}" aria-label="搜索导入记录">
          </div>
          <select class="select" id="historyStatus" aria-label="按导入状态筛选">
            <option value="" ${!status ? 'selected' : ''}>全部状态</option>
            <option value="completed" ${status === 'completed' ? 'selected' : ''}>已完成</option>
            <option value="partial" ${status === 'partial' ? 'selected' : ''}>部分导入</option>
            <option value="duplicate" ${status === 'duplicate' ? 'selected' : ''}>全部重复</option>
            <option value="failed" ${status === 'failed' ? 'selected' : ''}>未导入</option>
          </select>
          <button type="button" class="btn btn-ghost btn-sm" data-action="reset-history">${icon('refresh')}重置</button>
          <span style="margin-left:auto;color:var(--faint);font-size:9.5px" id="historyCount"></span>
        </div>
        <div class="batch-list" id="historyList">
          <div class="loading-block" style="min-height:260px"><span class="loading-spinner dark"></span>正在读取导入记录…</div>
        </div>
        <footer class="history-pagination" id="historyPagination"></footer>
      </section>
    </div>
  `;
  const listHost = container.querySelector('#historyList');
  const paginationHost = container.querySelector('#historyPagination');
  const subtitle = container.querySelector('#historySubtitle');
  const countHost = container.querySelector('#historyCount');

  function syncUrl() {
    const params = {};
    if (q) params.q = q;
    if (status) params.status = status;
    if (page > 1) params.page = page;
    window.history.replaceState(null, '', `#/history${Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : ''}`);
  }

  function paginationMarkup() {
    if (!currentTotal) return '';
    const pages = Math.max(1, Math.ceil(currentTotal / pageSize));
    const numbers = [];
    if (pages <= 7) {
      for (let index = 1; index <= pages; index += 1) numbers.push(index);
    } else {
      numbers.push(1);
      if (currentPage > 4) numbers.push('...');
      for (let index = Math.max(2, currentPage - 1); index <= Math.min(pages - 1, currentPage + 1); index += 1) numbers.push(index);
      if (currentPage < pages - 3) numbers.push('...');
      numbers.push(pages);
    }
    const from = (currentPage - 1) * pageSize + 1;
    const to = Math.min(currentPage * pageSize, currentTotal);
    return `
      <span>显示 ${n0(from)}–${n0(to)}，共 ${n0(currentTotal)} 次导入</span>
      <div class="pager">
        <button type="button" data-history-page="${currentPage - 1}" ${currentPage <= 1 ? 'disabled' : ''} aria-label="上一页">${icon('chevron-left')}</button>
        ${numbers.map((number) => number === '...'
          ? '<button type="button" disabled>…</button>'
          : `<button type="button" data-history-page="${number}" class="${number === currentPage ? 'active' : ''}">${number}</button>`).join('')}
        <button type="button" data-history-page="${currentPage + 1}" ${currentPage >= pages ? 'disabled' : ''} aria-label="下一页">${icon('chevron-right')}</button>
      </div>`;
  }

  async function load() {
    const token = ++sequence;
    abortController?.abort();
    abortController = new AbortController();
    listHost.innerHTML = `<div class="loading-block" style="min-height:260px"><span class="loading-spinner dark"></span>正在读取导入记录…</div>`;
    try {
      const params = { page, page_size: pageSize };
      if (q) params.q = q;
      if (status) params.status = status;
      const result = await api.batches(params, abortController.signal);
      if (disposed || token !== sequence) return;
      page = result.page;
      currentPage = result.page;
      currentTotal = result.total;
      countHost.textContent = result.total ? `共 ${n0(result.total)} 次` : '';
      subtitle.textContent = result.total
        ? `共 ${n0(result.total)} 次导入。点开任意一条，可查看文件、映射结果与异常明细。`
        : '每次导入的文件、校验结果和异常都留在这里。';
      if (!result.items.length) {
        listHost.innerHTML = emptyHistory();
        paginationHost.innerHTML = '';
        return;
      }
      listHost.innerHTML = result.items.map(batchRowMarkup).join('');
      paginationHost.innerHTML = paginationMarkup();
    } catch (error) {
      if (error.name === 'AbortError' || disposed || token !== sequence) return;
      listHost.innerHTML = `<div class="empty-state" style="padding:50px 20px">
        <div class="empty-icon" style="color:var(--coral-700);background:var(--coral-50)">${icon('alert', 'icon-xl')}</div>
        <h3>导入记录读取失败</h3><p>${e(error.message)}</p>
        <button type="button" class="btn btn-secondary btn-sm" data-action="retry-history">${icon('refresh')}重试</button>
      </div>`;
      toast(error.message, 'error');
    }
  }

  const onInput = (event) => {
    if (event.target.id === 'historySearch') {
      q = event.target.value.trim();
      searchDebounced();
    }
  };
  const onChange = (event) => {
    if (event.target.id === 'historyStatus') {
      status = event.target.value;
      page = 1;
      syncUrl();
      load();
    }
  };
  const onClick = (event) => {
    const batchButton = event.target.closest('[data-batch-id]');
    if (batchButton) {
      openBatchDetail(batchButton.dataset.batchId);
      return;
    }
    if (event.target.closest('[data-action="reset-history"]')) {
      q = '';
      status = '';
      page = 1;
      container.querySelector('#historySearch').value = '';
      container.querySelector('#historyStatus').value = '';
      syncUrl();
      load();
      return;
    }
    if (event.target.closest('[data-action="retry-history"]')) {
      load();
      return;
    }
    const pageButton = event.target.closest('[data-history-page]');
    if (pageButton && !pageButton.disabled) {
      page = Number(pageButton.dataset.historyPage);
      syncUrl();
      load();
    }
  };
  container.addEventListener('input', onInput);
  container.addEventListener('change', onChange);
  container.addEventListener('click', onClick);
  load();
  if (autoOpen) {
    window.history.replaceState(null, '', '#/history');
    window.setTimeout(() => { if (!disposed) openBatchDetail(autoOpen); }, 80);
  }
  return () => {
    disposed = true;
    sequence += 1;
    abortController?.abort();
    searchDebounced.cancel();
    container.removeEventListener('input', onInput);
    container.removeEventListener('change', onChange);
    container.removeEventListener('click', onClick);
  };
}
