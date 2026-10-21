/** 应用壳层、哈希路由与全局交互。 */
import { loadBootstrap, state } from './store.js';
import {
  buildHash, errorPanel, escapeHtml, icon, openHelpModal, parseHash, toast,
} from './ui.js';
import { render as renderDashboard } from './pages/dashboard.js';
import { render as renderRecords } from './pages/records.js';
import { render as renderImport } from './pages/importPage.js';
import { render as renderHistory } from './pages/history.js';
import { render as renderSettings } from './pages/settings.js';

const ROUTES = {
  '/dashboard': {
    title: '经营总览',
    eyebrow: '工作台',
    render: renderDashboard,
  },
  '/records': {
    title: '数据明细',
    eyebrow: '台账管理',
    render: renderRecords,
  },
  '/import': {
    title: '批量导入',
    eyebrow: '数据接入',
    render: renderImport,
  },
  '/history': {
    title: '导入记录',
    eyebrow: '数据接入',
    render: renderHistory,
  },
  '/settings': {
    title: '设置与数据安全',
    eyebrow: '管理',
    render: renderSettings,
  },
};

let cleanupCurrent = null;
let routeSequence = 0;
let booted = false;

function setShellRoute(name, route) {
  document.getElementById('pageTitle').textContent = route.title;
  document.getElementById('pageEyebrow').textContent = route.eyebrow;
  document.title = `${route.title} · 衡策经营数据工作台`;
  document.querySelectorAll('[data-nav]').forEach((link) => {
    const active = link.dataset.nav === name;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  document.getElementById('topImportButton').classList.toggle('hidden', name === 'import');
  document.body.dataset.route = name;
}

function navigate(path, params = {}, { replace = false } = {}) {
  const target = buildHash(path, params);
  if (window.location.hash === target) {
    renderRoute();
    return;
  }
  if (replace) {
    window.history.replaceState(null, '', target);
    renderRoute();
  } else {
    window.location.hash = target;
  }
}

async function renderRoute() {
  if (!booted) return;
  const { path, params } = parseHash();
  const name = path.replace(/^\//, '');
  const route = ROUTES[path];
  const container = document.getElementById('app');
  const token = ++routeSequence;
  if (cleanupCurrent) {
    try { cleanupCurrent(); } catch (error) { console.warn('页面清理失败', error); }
    cleanupCurrent = null;
  }
  if (!route) {
    setShellRoute('', { title: '页面不存在', eyebrow: '提示' });
    container.innerHTML = `
      <div class="card error-panel">
        <div class="empty-icon">${icon('help', 'icon-xl')}</div>
        <h2>没有找到这个页面</h2>
        <p>地址可能已经失效，请回到经营总览。</p>
        <a class="btn btn-primary" style="margin-top:18px" href="#/dashboard">${icon('grid')}返回经营总览</a>
      </div>`;
    return;
  }
  setShellRoute(name, route);
  container.innerHTML = `<div class="loading-block"><span class="loading-spinner dark"></span>正在准备页面…</div>`;
  window.scrollTo({ top: 0, behavior: 'auto' });
  // 每个路由使用独立挂载点，旧页面的异步回调不会写入新页面。
  const pageRoot = document.createElement('div');
  pageRoot.className = 'route-root page-enter';
  container.innerHTML = '';
  container.appendChild(pageRoot);
  const searchInput = document.getElementById('globalSearchInput');
  if (searchInput) searchInput.value = params.get('q') || '';
  const context = {
    params,
    navigate,
    refreshRoute: renderRoute,
    routeName: name,
    token,
  };
  try {
    const cleanup = await route.render(pageRoot, context);
    if (token !== routeSequence) {
      if (typeof cleanup === 'function') cleanup();
      return;
    }
    cleanupCurrent = typeof cleanup === 'function' ? cleanup : null;
  } catch (error) {
    if (token !== routeSequence) return;
    console.error(error);
    pageRoot.innerHTML = errorPanel(
      error?.message || '页面加载失败，请稍后重试。',
      { detail: error?.code === 'service_unreachable' ? '本地服务不可达' : '' },
    );
  }
}

function bindGlobal() {
  document.getElementById('globalSearchForm')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const value = document.getElementById('globalSearchInput').value.trim();
    navigate('/records', value ? { q: value } : {});
  });

  document.getElementById('helpButton')?.addEventListener('click', openHelpModal);

  document.addEventListener('click', (event) => {
    const reload = event.target.closest('[data-action="reload-page"]');
    if (reload) window.location.reload();
  });

  document.addEventListener('keydown', (event) => {
    const target = event.target;
    const typing = target instanceof HTMLElement && (
      target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable
    );
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      document.getElementById('globalSearchInput')?.focus();
      return;
    }
    if (event.key === 'Escape' && !document.querySelector('dialog[open]') && typing) {
      target.blur();
    }
  });

  window.addEventListener('hashchange', renderRoute);

  document.getElementById('mobileMenu')?.addEventListener('click', () => {
    document.getElementById('sidebar')?.classList.toggle('mobile-open');
  });
}

async function boot() {
  bindGlobal();
  const bootScreen = document.getElementById('bootScreen');
  try {
    await loadBootstrap();
    booted = true;
    document.getElementById('appShell').setAttribute('aria-hidden', 'false');
    bootScreen.classList.add('is-gone');
    window.setTimeout(() => bootScreen.remove(), 450);
    await renderRoute();
  } catch (error) {
    console.error(error);
    bootScreen.classList.add('is-gone');
    document.getElementById('appShell').setAttribute('aria-hidden', 'false');
    const container = document.getElementById('app');
    container.innerHTML = `
      <div class="card error-panel">
        <div class="empty-icon">${icon('alert', 'icon-xl')}</div>
        <h2>工作台暂时无法打开</h2>
        <p>${escapeHtml(error?.message || '本地数据服务没有响应。')}</p>
        <p class="text-faint" style="margin-top:8px">请确认程序窗口仍在运行。如果问题持续，请查看 data/logs/hengce.log。</p>
        <button class="btn btn-primary" style="margin-top:18px" data-action="reload-page">${icon('refresh')}重新加载</button>
      </div>`;
    if (bootScreen.isConnected) window.setTimeout(() => bootScreen.remove(), 450);
  }
}

boot();
