/**
 * 本地 API 封装：所有请求只发往当前同源 Flask 服务。
 * 服务层统一返回 { ok, error, details }，这里转换成可展示的 ApiError。
 */
export class ApiError extends Error {
  constructor(message, { status = 0, code = 'network_error', details = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function errorFromResponse(status, payload) {
  return new ApiError(
    payload?.error || `请求失败（${status}）`,
    { status, code: payload?.code || 'request_failed', details: payload?.details || null },
  );
}

async function parseResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new ApiError('本地服务返回了无法解析的数据，请刷新页面重试。', { status: response.status });
    }
    if (!response.ok || payload.ok === false) throw errorFromResponse(response.status, payload);
    return payload;
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const text = await response.text();
      if (text && text.length < 300) message = text;
    } catch { /* ignore */ }
    throw new ApiError(message, { status: response.status });
  }
  return response;
}

export async function request(path, { method = 'GET', body, headers, signal } = {}) {
  const options = { method, signal, headers: { Accept: 'application/json', ...(headers || {}) } };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new ApiError('无法连接本地服务，请确认工作台窗口仍在运行，然后刷新页面。', {
      code: 'service_unreachable',
    });
  }
  return parseResponse(response);
}

export const api = {
  bootstrap: () => request('/api/bootstrap'),
  dashboard: (params) => request(`/api/dashboard?${buildQuery(params)}`),
  options: () => request('/api/options'),
  records: (params, signal) => request(`/api/records?${buildQuery(params)}`, { signal }),
  record: (id) => request(`/api/records/${encodeURIComponent(id)}`),
  createRecord: (payload) => request('/api/records', { method: 'POST', body: payload }),
  updateRecord: (id, payload) => request(`/api/records/${encodeURIComponent(id)}`, { method: 'PUT', body: payload }),
  deleteRecord: (id) => request(`/api/records/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  bulkDelete: (ids) => request('/api/records/bulk-delete', { method: 'POST', body: { ids } }),
  batches: (params, signal) => request(`/api/batches?${buildQuery(params)}`, { signal }),
  batch: (id) => request(`/api/batches/${encodeURIComponent(id)}`),
  getSession: (token) => request(`/api/imports/${encodeURIComponent(token)}`),
  discardSession: (token) => request(`/api/imports/${encodeURIComponent(token)}`, { method: 'DELETE' }),
  previewImport: (token, payload) => request(`/api/imports/${encodeURIComponent(token)}/preview`, { method: 'POST', body: payload }),
  commitImport: (token, payload) => request(`/api/imports/${encodeURIComponent(token)}/commit`, { method: 'POST', body: payload }),
  changeHeader: (token, sourceId, headerRow) => request(
    `/api/imports/${encodeURIComponent(token)}/sources/${encodeURIComponent(sourceId)}/header`,
    { method: 'PATCH', body: { header_row: headerRow } },
  ),
  settings: () => request('/api/settings'),
  saveSettings: (payload) => request('/api/settings', { method: 'PUT', body: payload }),
  createBackup: () => download('/api/maintenance/backup', { method: 'POST', filename: '数据统计工作台数据备份.db' }),
  cleanup: () => request('/api/maintenance/cleanup', { method: 'POST' }),
  audit: () => request('/api/audit?limit=12'),
  mappingProfiles: () => request('/api/mapping-profiles'),
  deleteMappingProfile: (signature) => request(`/api/mapping-profiles/${encodeURIComponent(signature)}`, { method: 'DELETE' }),
  seedDemo: () => request('/api/demo', { method: 'POST' }),
  clearDemo: () => request('/api/demo', { method: 'DELETE' }),
  updateStatus: () => request('/api/update/status'),
  inspectUpdate: (file) => uploadFile('/api/update/inspect', file),
  applyUpdate: (packageId) => request('/api/update/apply', { method: 'POST', body: { package_id: packageId } }),
  activateVersion: (version) => request('/api/update/activate', { method: 'POST', body: { version } }),
  restartApp: () => request('/api/update/restart', { method: 'POST' }),
  quitApp: () => request('/api/application/quit', { method: 'POST' }),
};

export function buildQuery(params = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    search.set(key, String(value));
  });
  return search.toString();
}

export function uploadFiles(files, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    files.forEach((file) => form.append('files', file, file.name));
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/imports/analyze');
    xhr.responseType = 'json';
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onProgress) onProgress(Math.round((event.loaded / event.total) * 100));
    });
    xhr.addEventListener('load', () => {
      let payload = xhr.response;
      if (!payload && xhr.responseText) {
        try { payload = JSON.parse(xhr.responseText); } catch { payload = null; }
      }
      if (xhr.status >= 200 && xhr.status < 300 && payload?.ok !== false) {
        resolve(payload);
      } else {
        reject(errorFromResponse(xhr.status, payload || { error: '文件上传失败，请重试。' }));
      }
    });
    xhr.addEventListener('error', () => reject(new ApiError(
      '文件上传失败，无法连接本地服务。请重新打开工作台后再试。', { code: 'upload_failed' },
    )));
    xhr.addEventListener('abort', () => reject(new ApiError('上传已取消', { code: 'aborted' })));
    xhr.send(form);
  });
}

export function uploadFile(url, file, fieldName = 'package') {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append(fieldName, file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.responseType = 'json';
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.addEventListener('load', () => {
      let payload = xhr.response;
      if (!payload && xhr.responseText) {
        try { payload = JSON.parse(xhr.responseText); } catch { payload = null; }
      }
      if (xhr.status >= 200 && xhr.status < 300 && payload?.ok !== false) resolve(payload);
      else reject(errorFromResponse(xhr.status, payload || { error: '文件上传失败。' }));
    });
    xhr.addEventListener('error', () => reject(new ApiError('无法连接本地服务，请刷新页面后重试。')));
    xhr.send(form);
  });
}

export async function download(path, { method = 'GET', body, filename = '下载文件' } = {}) {
  const options = { method, headers: { Accept: 'application/octet-stream, application/json' } };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError('无法连接本地服务，下载没有开始。', { code: 'service_unreachable' });
  }
  if (!response.ok) {
    let payload = null;
    try { payload = await response.json(); } catch { /* ignore */ }
    throw errorFromResponse(response.status, payload || { error: '下载失败，请重试。' });
  }
  const disposition = response.headers.get('content-disposition') || '';
  let name = filename;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
  if (utf8Match) {
    try { name = decodeURIComponent(utf8Match[1]); } catch { /* ignore */ }
  } else if (plainMatch) {
    name = plainMatch[1];
  }
  const blob = await response.blob();
  saveBlob(blob, name);
  return name;
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

export function downloadWithPath(path, filename) {
  return download(path, { filename });
}
