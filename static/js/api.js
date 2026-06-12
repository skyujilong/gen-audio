// API 客户端 + 通用工具。
// 所有 fetch 走 `api()` 统一处理错误（toast + 抛错）。

const TOAST_DURATION_MS = 3000;

const _toastHost = document.createElement('div');
_toastHost.id = 'toast-host';
_toastHost.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;';
document.body && document.body.appendChild(_toastHost);

function toast(message, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = message;
  _toastHost.appendChild(el);
  setTimeout(() => el.remove(), TOAST_DURATION_MS);
}

/**
 * 通用 API 调用。
 * @param {string} method   GET / POST / PATCH / DELETE / PUT
 * @param {string} path     e.g. '/draw' (会自动补 /api 前缀)
 * @param {any}    body     普通对象 / FormData / null
 * @param {boolean} [isForm] true 时 body 视为 FormData，不加 Content-Type
 */
async function api(method, path, body, isForm = false) {
  const opts = { method };
  if (body === undefined || body === null) {
    // no body
  } else if (isForm) {
    opts.body = body;  // FormData, 让浏览器自己设 boundary
  } else {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`/api${path}`, opts);
  } catch (e) {
    toast('网络错误，请检查后端是否启动', 'error');
    throw e;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '请求失败', code: 'UNKNOWN' }));
    // FastAPI 校验错误时 detail 是 [{loc, msg, type}, ...] 数组，
    // 旧版直接 toast 会出现 "[object Object]"。展平成可读文本。
    let detail;
    if (Array.isArray(err.detail)) {
      detail = err.detail.map(d => {
        const path = Array.isArray(d.loc) ? d.loc.filter(x => x !== 'body').join('.') : '';
        return path ? `${path}: ${d.msg}` : d.msg;
      }).join('; ');
    } else if (typeof err.detail === 'string') {
      detail = err.detail;
    } else {
      detail = `请求失败 (${res.status})`;
    }
    toast(detail, 'error');
    const e = new Error(detail);
    e.code = err.code;
    e.status = res.status;
    e.detail = err.detail;
    throw e;
  }
  if (res.status === 204) return null;
  return res.json();
}

// 轮询工具
function startPolling(fn, intervalMs = 2000) {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try { await fn(); } catch (e) { console.error('poll error', e); }
    if (!stopped) setTimeout(tick, intervalMs);
  };
  tick();
  return () => { stopped = true; };
}

// === 音色库 wrapper（Phase 5.4） ===
//
// 全部走 /api/speakers 前缀的 REST 端点。
// 详见 app/api/speakers.py。
const speakers = {
  list({favorited, search} = {}) {
    const params = new URLSearchParams();
    if (favorited !== undefined) params.set('favorited', String(favorited));
    if (search) params.set('search', search);
    const q = params.toString();
    return api('GET', `/speakers${q ? '?' + q : ''}`);
  },
  get(id)              { return api('GET', `/speakers/${id}`); },
  create({name, tensor_base64, tags = [], is_favorited = false}) {
    return api('POST', '/speakers', {name, tensor_base64, tags, is_favorited});
  },
  /** 从上传的 .pt 文件创建；File 对象 + 必填 name。 */
  upload(file, name, {tags = [], is_favorited = false} = {}) {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('name', name);
    fd.append('tags', JSON.stringify(tags));
    fd.append('is_favorited', String(is_favorited));
    return api('POST', '/speakers/upload', fd, /* isForm */ true);
  },
  update(id, body)     { return api('PATCH', `/speakers/${id}`, body); },
  delete(id)           { return api('DELETE', `/speakers/${id}`); },
  toggleFavorite(id)   { return api('POST', `/speakers/${id}/favorite`); },
  /** 随机音色（不写库） */
  random()             { return api('GET', '/speakers/random'); },
};

export { api, toast, startPolling, speakers };
