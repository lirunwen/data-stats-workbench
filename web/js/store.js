/** 全局只读状态与轻量刷新。 */
import { api } from './api.js';

export const state = {
  app: null,
  options: null,
  settings: null,
  update: null,
  loaded: false,
};

export async function loadBootstrap() {
  const payload = await api.bootstrap();
  applyBootstrap(payload.app);
  state.loaded = true;
  return payload.app;
}

export function applyBootstrap(app) {
  state.app = app;
  state.options = app.options || { categories: [], departments: [], years: [], total: 0, demo_count: 0 };
  state.settings = app.settings || {};
  state.update = app.update || null;
  updateShellCounts();
}

export async function refreshOptions() {
  const payload = await api.options();
  state.options = payload.options;
  if (state.app) {
    state.app.options = payload.options;
    updateShellCounts();
  }
  return payload.options;
}

export async function refreshApp() {
  const payload = await api.bootstrap();
  applyBootstrap(payload.app);
  return payload.app;
}

export function updateShellCounts() {
  const count = Number(state.options?.total || 0);
  const badge = document.getElementById('navRecordCount');
  if (badge) badge.textContent = count > 999 ? '999+' : String(count);
  const version = document.getElementById('sidebarVersion');
  if (version && state.app) version.textContent = `数据统计工作台 v${state.app.version}`;
}

export function getCategoryNames() {
  return (state.options?.categories || []).map((item) => item.name);
}

export function getDepartmentNames() {
  return (state.options?.departments || []).map((item) => item.name);
}

export function findCategory(name) {
  return (state.options?.categories || []).find((item) => item.name === name) || null;
}
