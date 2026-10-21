/** 设置与数据安全、离线升级、版本回退与操作记录。 */
import { api } from '../api.js';
import { refreshApp, state as store } from '../store.js';
import {
  confirmDialog, e, formatDateTime, formatFileSize, icon, n0, openModal,
  openHelpModal, relativeTime, setButtonLoading, toast,
} from '../ui.js';

function actionLabel(action) {
  return {
    create: '新增流水', update: '修改流水', delete: '删除流水', bulk_delete: '批量删除',
    import: '批量导入', demo_seed: '载入演示数据', demo_clear: '清除演示数据',
    settings: '修改设置', backup: '数据备份', cleanup: '清理临时文件',
    update_inspect: '校验升级包', update_apply: '安装升级包', update_switch: '切换版本',
    delete_mapping_profile: '删除字段映射',
  }[action] || action;
}

function storageCard(app) {
  const storage = app.storage || {};
  return `
    <div class="storage-grid">
      <div class="storage-item"><span>主数据库</span><strong>${formatFileSize(storage.database_size || 0)}</strong></div>
      <div class="storage-item"><span>本地备份</span><strong>${formatFileSize(storage.backup_size || 0)}</strong></div>
      <div class="storage-item"><span>临时上传</span><strong>${formatFileSize(storage.staging_size || 0)}</strong></div>
    </div>
    <div class="storage-path">${icon('hard-drive')}<code title="${e(storage.data_dir || '')}">${e(storage.data_dir || '')}</code>
      <button type="button" class="icon-button icon-button-sm" data-action="copy-path" aria-label="复制数据目录" title="复制路径">${icon('copy')}</button>
    </div>
      <p class="update-note">升级只替换程序文件，数据目录会完整保留。手动备份会下载到浏览器默认目录，每日自动备份保存在数据目录的 backup 中。</p>
  `;
}

function profilesMarkup(profiles) {
  if (!profiles?.length) {
    return `<div class="empty-state" style="padding:24px 10px">
      <div class="empty-icon" style="width:38px;height:38px;border-radius:10px">${icon('tag')}</div>
      <h3 style="font-size:11px">还没有字段映射习惯</h3>
      <p style="font-size:9.5px">成功导入一次后，相同表头的文件会自动套用上次的映射。</p>
    </div>`;
  }
  return profiles.map((profile) => `
    <div class="profile-row">
      <span class="profile-icon">${icon('layers')}</span>
      <div class="profile-copy">
        <strong title="${e(profile.name)}">${e(profile.name)}</strong>
        <span title="${e((profile.headers || []).join(' / '))}">${e((profile.headers || []).slice(0, 4).join(' · '))}${profile.headers.length > 4 ? ' …' : ''}</span>
      </div>
      <span class="profile-meta">用过 ${n0(profile.use_count)} 次</span>
      <button type="button" class="icon-button icon-button-sm" data-delete-profile="${e(profile.signature)}" aria-label="删除这条字段映射" title="删除">${icon('trash')}</button>
    </div>
  `).join('');
}

function versionCard(app) {
  const update = store.update || {};
  const running = update.running_version || app.version;
  const active = update.active_version || running;
  const versions = update.versions || [];
  return `
    <div class="version-hero">
      <span>DATA WORKBENCH</span>
      <h3>v${e(running)}</h3>
      <p>${e(app.released || '')} · ${update.installed ? '安装版 · 离线运行' : '源码开发模式'}</p>
      <span class="version-badge"><span class="status-dot"></span>${update.restart_required ? `新版本 v${e(active)} 待重启生效` : '当前为最新启动版本'}</span>
    </div>
    ${update.restart_required ? `
      <div class="notice notice-warning" style="margin-top:12px">
        ${icon('refresh')}<span>新版本 <strong>v${e(active)}</strong> 已准备好。重启后会载入新程序，数据不会丢失。</span>
      </div>
      <button type="button" class="btn btn-primary btn-sm btn-block" style="margin-top:10px" data-action="restart-app">${icon('refresh')}立即重启应用</button>
    ` : ''}
    <div class="update-drop" data-action="select-update" role="button" tabindex="0">
      ${icon('package')}
      <div><strong>选择离线升级包</strong><span>支持 .zip / .hcup，文件在本机校验哈希</span></div>
      <input type="file" id="updateFile" class="hidden" accept=".zip,.hcup">
    </div>
    <div class="update-note">升级包必须由本工作台生成。安装前会自动备份数据库；程序目录按版本保存，可切回旧版。</div>
    ${versions.length ? `
      <div class="update-history">
        ${versions.slice(0, 5).map((version) => `
          <div class="update-version-row">
            <span class="v">v${e(version.version)}</span>
            <span class="meta">${version.active ? '启动版本' : ''}${version.running ? ' · 正在运行' : ''}${version.released ? ` · ${e(version.released)}` : ''}</span>
            ${version.active ? `<span class="tag">当前</span>` : `<button type="button" class="btn btn-secondary btn-sm" data-activate-version="${e(version.version)}">${version.running ? '恢复运行版本' : '切换'}</button>`}
          </div>`).join('')}
      </div>
    ` : ''}
    <button type="button" class="btn btn-ghost btn-sm btn-block" style="margin-top:13px" data-action="check-integrity">${icon('shield')}查看数据完整性说明</button>
  `;
}

function auditMarkup(items) {
  if (!items?.length) return '<p class="update-note">还没有操作记录。</p>';
  return `<div class="audit-list">
    ${items.slice(0, 10).map((item) => `
      <div class="audit-item">
        <span class="audit-dot"></span>
        <div class="audit-copy"><strong>${e(actionLabel(item.action))} · ${e(item.summary || '')}</strong><span>${formatDateTime(item.created_at)} · ${relativeTime(item.created_at)}</span></div>
      </div>`).join('')}
  </div>`;
}

export function render(container, ctx) {
  let disposed = false;
  const app = store.app;
  const settings = store.settings || {};
  const options = store.options || {};
  const profiles = app.mapping_profiles || [];
  container.innerHTML = `
    <div class="settings-page">
      <div class="page-toolbar">
        <div>
          <h2>工作台设置</h2>
          <p>调整使用习惯、备份本地数据，或通过离线升级包更新功能。</p>
        </div>
        <div class="toolbar-actions">
          <button type="button" class="btn btn-secondary btn-sm" data-action="backup">${icon('download')}立即备份</button>
          <button type="button" class="btn btn-ghost btn-sm" data-help>${icon('help')}使用帮助</button>
        </div>
      </div>
      <div class="settings-grid">
        <div class="settings-column">
          <section class="card settings-card">
            <div class="settings-card-head">
              <div><h2>基础设置</h2><p>影响导入与页面的默认行为。</p></div>
              <span class="icon-box">${icon('settings')}</span>
            </div>
            <div class="settings-body">
              <form id="settingsForm">
                <div class="setting-row">
                  <div class="setting-copy"><strong>单位名称</strong><span>显示在经营总览顶部，可留空。</span></div>
                  <input class="input" name="org_name" maxlength="60" placeholder="例如：青屿商贸有限公司" value="${e(settings.org_name || '')}">
                </div>
                <div class="setting-row">
                  <div class="setting-copy"><strong>重复数据默认处理</strong><span>导入时遇到重复流水的默认选择，仍可在校验页修改。</span></div>
                  <select class="select" name="default_duplicate_policy">
                    <option value="skip" ${settings.default_duplicate_policy !== 'update' ? 'selected' : ''}>跳过重复（推荐）</option>
                    <option value="update" ${settings.default_duplicate_policy === 'update' ? 'selected' : ''}>更新已有记录</option>
                  </select>
                </div>
                <div class="setting-row">
                  <div class="setting-copy"><strong>记住字段映射习惯</strong><span>相同表头的文件下次导入时自动套用字段映射。</span></div>
                  <label class="switch"><input type="checkbox" name="remember_mapping" ${settings.remember_mapping !== false ? 'checked' : ''}><span></span></label>
                </div>
                <div class="settings-submit">
                  <button type="button" class="btn btn-primary btn-sm" data-action="save-settings">${icon('check')}保存设置</button>
                </div>
              </form>
            </div>
          </section>

          <section class="card settings-card">
            <div class="settings-card-head">
              <div><h2>数据安全</h2><p>数据都存在本机，备份与清理也在本机完成。</p></div>
              <span class="icon-box">${icon('database')}</span>
            </div>
            <div class="settings-body">
              <div id="storageHost">${storageCard(app)}</div>
              <div class="settings-buttons">
                <button type="button" class="btn btn-secondary btn-sm" data-action="backup">${icon('download')}备份数据库</button>
                <button type="button" class="btn btn-ghost btn-sm" data-action="cleanup">${icon('refresh')}清理临时上传</button>
              </div>
              <div class="demo-box" style="margin-top:15px">
                <h3>${icon('sparkles')}演示数据</h3>
                <p>${options.demo_count ? `当前有 ${n0(options.demo_count)} 条演示流水。演示数据带有标记，可随时整批清除。` : '还没有演示数据。载入一份示例，快速体验完整汇总与图表。'}</p>
                <div class="settings-buttons">
                  ${options.demo_count
                    ? `<button type="button" class="btn btn-danger-soft btn-sm" data-action="clear-demo">${icon('trash')}清除演示数据</button>`
                    : `<button type="button" class="btn btn-soft btn-sm" data-action="seed-demo">${icon('sparkles')}载入演示数据</button>`}
                  ${options.demo_count ? `<a class="btn btn-ghost btn-sm" href="#/dashboard">去查看</a>` : ''}
                </div>
              </div>
            </div>
          </section>

          <section class="card settings-card">
            <div class="settings-card-head">
              <div><h2>字段映射习惯</h2><p>系统自动记住最近成功导入的列对应关系。</p></div>
              <span class="icon-box">${icon('layers')}</span>
            </div>
            <div class="settings-body" id="profilesHost">${profilesMarkup(profiles)}</div>
          </section>
        </div>

        <div class="settings-column">
          <section class="card settings-card">
            <div class="settings-card-head">
              <div><h2>版本与离线升级</h2><p>通过本机文件更新，不依赖网络。</p></div>
              <span class="icon-box">${icon('package')}</span>
            </div>
            <div class="settings-body" id="versionHost">${versionCard(app)}</div>
          </section>

          <section class="card settings-card">
            <div class="settings-card-head">
              <div><h2>最近操作</h2><p>用于核对导入、修改与备份历史。</p></div>
              <span class="icon-box">${icon('clock')}</span>
            </div>
            <div class="settings-body" id="auditHost"><div class="loading-block" style="min-height:120px"><span class="loading-spinner dark"></span>读取操作记录…</div></div>
          </section>

          <section class="card settings-card">
            <div class="settings-card-head">
              <div><h2>关于工作台</h2><p>为办公室文员与财务文员准备的本地工具。</p></div>
              <span class="icon-box">${icon('info')}</span>
            </div>
            <div class="settings-body">
              <div class="profile-row"><span class="profile-icon">${icon('shield')}</span><div class="profile-copy"><strong>完全离线</strong><span>无 CDN、无外部字体、无云端接口，所有数据只在本机处理。</span></div></div>
              <div class="profile-row"><span class="profile-icon">${icon('file-xls')}</span><div class="profile-copy"><strong>支持文件</strong><span>.xlsx / .xls / .csv，以及 .msg / .oft / .eml 邮件附件。</span></div></div>
              <div class="profile-row"><span class="profile-icon">${icon('database')}</span><div class="profile-copy"><strong>数据库格式</strong><span>SQLite · schema v${e(app.schema_version)} · ${n0(app.options?.total || 0)} 条流水</span></div></div>
              <div class="profile-row"><span class="profile-icon">${icon('lock')}</span><div class="profile-copy"><strong>访问范围</strong><span>仅监听 127.0.0.1，局域网内其他电脑无法访问。</span></div></div>
              <button type="button" class="btn btn-ghost btn-sm btn-block" style="margin-top:12px" data-help>${icon('help')}打开使用帮助</button>
              <button type="button" class="btn btn-danger-soft btn-sm btn-block" style="margin-top:7px" data-action="quit-app">${icon('x')}退出工作台</button>
              <p class="update-note" style="text-align:center">退出后请双击桌面快捷方式重新启动。</p>
            </div>
          </section>
        </div>
      </div>
    </div>
  `;

  const auditHost = container.querySelector('#auditHost');
  const versionHost = container.querySelector('#versionHost');

  async function loadAudit() {
    try {
      const result = await api.audit();
      if (!disposed) auditHost.innerHTML = auditMarkup(result.items);
    } catch (error) {
      if (!disposed) auditHost.innerHTML = `<p class="update-note">操作记录暂时无法读取：${e(error.message)}</p>`;
    }
  }

  async function refreshVersionHost() {
    try {
      const result = await api.updateStatus();
      store.update = result.update;
      if (!disposed) versionHost.innerHTML = versionCard(store.app);
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function saveSettings(button) {
    const form = container.querySelector('#settingsForm');
    if (!form.reportValidity()) return;
    const body = {
      org_name: form.elements.org_name.value,
      default_duplicate_policy: form.elements.default_duplicate_policy.value,
      remember_mapping: form.elements.remember_mapping.checked,
    };
    const restore = setButtonLoading(button, true, '保存中…');
    try {
      const result = await api.saveSettings(body);
      store.settings = result.settings;
      await refreshApp();
      toast('设置已保存', 'success');
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      restore();
    }
  }

  async function backup(button) {
    const restore = setButtonLoading(button, true, '备份中…');
    try {
      const filename = await api.createBackup();
      toast(`备份已下载：${filename}`, 'success');
      await refreshApp();
      if (!disposed) container.querySelector('#storageHost').innerHTML = storageCard(store.app);
      loadAudit();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      restore();
    }
  }

  async function cleanup(button) {
    const confirmed = await confirmDialog({
      title: '清理临时上传？',
      message: '将删除超过 24 小时的导入暂存文件与过期异常报告。已有流水、导入记录和备份不会被删除。',
      confirmText: '开始清理',
      tone: 'primary',
    });
    if (!confirmed) return;
    const restore = setButtonLoading(button, true, '清理中…');
    try {
      const result = await api.cleanup();
      toast(result.message || '临时文件已清理', 'success');
      await refreshApp();
      if (!disposed) container.querySelector('#storageHost').innerHTML = storageCard(store.app);
      loadAudit();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      restore();
    }
  }

  async function seedDemo(button) {
    const confirmed = await confirmDialog({
      title: '载入演示数据？',
      message: '将添加一批带有“演示数据”标记的经营流水，用于体验导入、汇总和图表。你随时可以在设置中清除它们。',
      confirmText: '载入演示数据',
      tone: 'primary',
    });
    if (!confirmed) return;
    const restore = setButtonLoading(button, true, '正在准备…');
    try {
      const result = await api.seedDemo();
      toast(result.message || '演示数据已载入', 'success');
      await refreshApp();
      if (!disposed) ctx.refreshRoute();
    } catch (error) {
      toast(error.message, 'error');
      restore();
    }
  }

  async function clearDemo(button) {
    const confirmed = await confirmDialog({
      title: '清除演示数据？',
      message: '将删除所有带“演示”标记的流水及对应的演示导入记录。真实数据不会受到影响。',
      confirmText: '清除演示数据',
      tone: 'danger',
    });
    if (!confirmed) return;
    const restore = setButtonLoading(button, true, '正在清除…');
    try {
      const result = await api.clearDemo();
      toast(`已清除 ${n0(result.deleted || 0)} 条演示流水`, 'success');
      await refreshApp();
      if (!disposed) ctx.refreshRoute();
    } catch (error) {
      toast(error.message, 'error');
      restore();
    }
  }

  function openPackageInspection(pkg) {
    const installed = Boolean(store.update?.installed);
    const modal = openModal({
      title: '升级包校验通过',
      subtitle: '所有文件哈希已在本机完成校验',
      size: 'lg',
      body: `
        <div class="batch-detail-meta">
          <div class="batch-meta-item"><span>目标版本</span><strong style="font-family:var(--font-number);font-size:15px">v${e(pkg.version)}</strong></div>
          <div class="batch-meta-item"><span>文件数量</span><strong>${n0(pkg.file_count)} 个</strong></div>
          <div class="batch-meta-item"><span>升级包大小</span><strong>${formatFileSize(pkg.size)}</strong></div>
          <div class="batch-meta-item"><span>兼容起始版本</span><strong>v${e(pkg.min_from)}</strong></div>
        </div>
        <div class="form-section-title" style="margin-top:18px">${icon('info')}更新说明</div>
        <div class="notice notice-info" style="white-space:pre-wrap">${e(pkg.notes || '此升级包没有附加说明。')}</div>
        <div class="notice notice-warning" style="margin-top:12px">${icon('shield')}<span>安装时会把新版本放到独立的版本目录，自动备份数据库，再切换启动版本。升级完成后需要重启应用。</span></div>
        ${installed ? '' : `<div class="notice notice-danger" style="margin-top:12px">${icon('alert')}<span>当前是源码开发模式，升级包不会覆盖源码。请在 Windows 安装版中使用。</span></div>`}
      `,
      footer: `
        <button type="button" class="btn btn-ghost" data-modal-close>取消</button>
        <button type="button" class="btn btn-primary" data-apply-package ${installed ? '' : 'disabled'}>${icon('package')}安装此版本</button>
      `,
      onMount(dialog, controls) {
        const applyButton = dialog.querySelector('[data-apply-package]');
        applyButton?.addEventListener('click', async () => {
          const confirmed = await confirmDialog({
            title: `安装 v${pkg.version}？`,
            message: '安装会备份数据库并把新版本设为启动版本。完成前不会改动当前运行的数据。',
            confirmText: '安装升级包',
            tone: 'primary',
            warning: '安装后需要重启应用。请先保存正在编辑的内容。',
          });
          if (!confirmed) return;
          controls.setLocked(true);
          const restore = setButtonLoading(applyButton, true, '正在安装…');
          try {
            const result = await api.applyUpdate(pkg.package_id);
            modal.close();
            toast(result.message || '新版本已安装，请重启应用', 'success', 6000);
            await refreshApp();
            if (!disposed) ctx.refreshRoute();
          } catch (error) {
            toast(error.message, 'error');
            restore();
            controls.setLocked(false);
          }
        });
      },
    });
    void modal;
  }

  async function inspectUpdate(file) {
    if (!file) return;
    if (!/\.(zip|hcup)$/i.test(file.name)) {
      toast('升级包应为 .zip 或 .hcup 文件', 'warning');
      return;
    }
    const drop = container.querySelector('.update-drop');
    const original = drop?.innerHTML || '';
    if (drop) {
      drop.innerHTML = `${icon('package')}<div><strong>正在校验 ${e(file.name)}</strong><span>计算文件哈希与版本兼容性…</span></div><span class="loading-spinner dark"></span>`;
    }
    try {
      const result = await api.inspectUpdate(file);
      if (drop) drop.innerHTML = original;
      openPackageInspection(result.package);
    } catch (error) {
      if (drop) drop.innerHTML = original;
      toast(error.message, 'error', 6000);
    } finally {
      const fileInput = container.querySelector('#updateFile');
      if (fileInput) fileInput.value = '';
    }
  }

  async function activateVersion(version, button) {
    const confirmed = await confirmDialog({
      title: `切换到 v${version}？`,
      message: `将把启动版本切换为 v${version}，并自动备份数据库。切换后需要重启应用。`,
      confirmText: '切换并准备重启',
      tone: 'primary',
      warning: '如果旧版本不认识新版本写入的数据结构，回退可能失败。建议在切换前先下载备份。',
    });
    if (!confirmed) return;
    const restore = setButtonLoading(button, true, '切换中…');
    try {
      const result = await api.activateVersion(version);
      toast(result.message || '版本已切换，请重启应用', 'success', 6000);
      await refreshApp();
      if (!disposed) ctx.refreshRoute();
    } catch (error) {
      toast(error.message, 'error');
      restore();
    }
  }

  async function restartApp(button) {
    const confirmed = await confirmDialog({
      title: '立即重启应用？',
      message: '当前页面会短暂断开，应用会载入新版本并自动重新打开。',
      confirmText: '重启应用',
      tone: 'primary',
    });
    if (!confirmed) return;
    const restore = setButtonLoading(button, true, '正在重启…');
    try {
      const result = await api.restartApp();
      toast(result.message || '正在重启应用，请稍候', 'success', 7000);
      if (!result.scheduled) restore();
      window.setTimeout(() => window.location.reload(), 6000);
    } catch (error) {
      toast(error.message, 'error');
      restore();
    }
  }

  async function deleteProfile(signature) {
    const confirmed = await confirmDialog({
      title: '删除字段映射习惯？',
      message: '删除后，下次导入相同表头时不会再自动套用这套映射。已导入的数据不会受影响。',
      confirmText: '删除映射',
      tone: 'danger',
    });
    if (!confirmed) return;
    try {
      await api.deleteMappingProfile(signature);
      toast('字段映射习惯已删除', 'success');
      await refreshApp();
      if (!disposed) ctx.refreshRoute();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function quitApp(button) {
    const confirmed = await confirmDialog({
      title: '退出工作台？',
      message: '所有已保存的数据都会保留。退出后浏览器页面将无法继续访问，需要重新双击桌面快捷方式启动。',
      confirmText: '退出工作台',
      tone: 'danger',
      warning: '请确认当前没有正在上传或导入的文件。',
    });
    if (!confirmed) return;
    const restore = setButtonLoading(button, true, '正在退出…');
    try {
      await api.quitApp();
      document.body.innerHTML = `
        <div class="exit-screen">
          <div class="boot-mark">数</div>
          <h1>工作台已退出</h1>
          <p>数据已保存在本机。需要继续使用时，请双击桌面上的「数据统计工作台」快捷方式。</p>
          <span>现在可以关闭此页面。</span>
        </div>`;
    } catch (error) {
      restore();
      toast(error.message, 'error');
    }
  }

  const onClick = (event) => {
    if (event.target.id === 'updateFile') return;
    const actionButton = event.target.closest('[data-action]');
    if (actionButton) {
      const action = actionButton.dataset.action;
      if (action === 'save-settings') { saveSettings(actionButton); return; }
      if (action === 'backup') { backup(actionButton); return; }
      if (action === 'cleanup') { cleanup(actionButton); return; }
      if (action === 'seed-demo') { seedDemo(actionButton); return; }
      if (action === 'clear-demo') { clearDemo(actionButton); return; }
      if (action === 'select-update') { container.querySelector('#updateFile')?.click(); return; }
      if (action === 'restart-app') { restartApp(actionButton); return; }
      if (action === 'quit-app') { quitApp(actionButton); return; }
      if (action === 'copy-path') {
        const path = store.app?.storage?.data_dir || '';
        if (!navigator.clipboard) {
          toast('当前浏览器不支持自动复制，请手动选择路径', 'warning');
          return;
        }
        navigator.clipboard.writeText(path)
          .then(() => toast('数据目录已复制', 'success'))
          .catch(() => toast('复制失败，请手动选择路径', 'warning'));
        return;
      }
      if (action === 'check-integrity') {
        openModal({
          title: '数据完整性说明',
          subtitle: '每次启动与导入都会经过本地校验',
          body: `
            <div class="help-faq">
              <div><strong>${icon('database')} SQLite 事务</strong><p>所有导入在一个事务内完成，任何一步失败都会整体回滚，不会留下半批数据。</p></div>
              <div><strong>${icon('shield')} 数据库完整性检查</strong><p>启动时执行 quick_check；每天首次启动自动保留一份数据库备份。</p></div>
              <div><strong>${icon('copy')} 去重与判重</strong><p>有单据编号按“年份 + 收支 + 单据编号 + 金额 + 类别”；无编号按日期、收支、金额、类别、部门、往来单位和备注综合判断。</p></div>
              <div><strong>${icon('package')} 升级安全</strong><p>升级包逐个文件校验 SHA-256；安装前自动备份数据库，旧版本程序保留用于回退。</p></div>
            </div>`,
          footer: `<button type="button" class="btn btn-primary" data-modal-close>知道了</button>`,
        });
        return;
      }
      if (action === 'delete-profile') { return; }
    }
    const help = event.target.closest('[data-help]');
    if (help) {
      openHelpModal();
      return;
    }
    const deleteButton = event.target.closest('[data-delete-profile]');
    if (deleteButton) {
      deleteProfile(deleteButton.dataset.deleteProfile);
      return;
    }
    const activateButton = event.target.closest('[data-activate-version]');
    if (activateButton) {
      activateVersion(activateButton.dataset.activateVersion, activateButton);
    }
  };

  const onChange = (event) => {
    if (event.target.id === 'updateFile') {
      inspectUpdate(event.target.files?.[0]);
    }
  };
  const onKeydown = (event) => {
    if ((event.key === 'Enter' || event.key === ' ') && event.target.closest('.update-drop')) {
      event.preventDefault();
      container.querySelector('#updateFile')?.click();
    }
  };

  container.addEventListener('click', onClick);
  container.addEventListener('change', onChange);
  container.addEventListener('keydown', onKeydown);
  loadAudit();
  refreshVersionHost();
  return () => {
    disposed = true;
    container.removeEventListener('click', onClick);
    container.removeEventListener('change', onChange);
    container.removeEventListener('keydown', onKeydown);
  };
}
