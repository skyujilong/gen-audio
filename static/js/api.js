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

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  let res;
  try {
    res = await fetch(`/api${path}`, opts);
  } catch (e) {
    toast('网络错误，请检查后端是否启动', 'error');
    throw e;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '请求失败', code: 'UNKNOWN' }));
    toast(err.detail, 'error');
    const e = new Error(err.detail);
    e.code = err.code;
    e.status = res.status;
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

export { api, toast, startPolling };
