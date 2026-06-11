// 合成页逻辑（多行动态列表 + 共享左栏卡）
import { api, toast, startPolling } from '/js/api.js';

const cardPicker = document.getElementById('card-picker');
const searchInput = document.getElementById('search');
const rowsEl = document.getElementById('rows');
const addRowBtn = document.getElementById('add-row');
const submitAllBtn = document.getElementById('submit-all');
const queueStatus = document.getElementById('queue-status');

let selectedCardId = null;
let selectedParams = null;
let allCards = [];
let jobs = [];  // 本地任务列表

// === 卡选择 ===
async function loadCards() {
  allCards = await api('GET', '/cards');
  renderCardPicker();
  // 处理 URL ?card_id=...
  const url = new URL(location.href);
  const preselect = parseInt(url.searchParams.get('card_id') || '0', 10);
  if (preselect) {
    const c = allCards.find(c => c.id === preselect);
    if (c) {
      selectedCardId = c.id;
      selectedParams = c.params;
      renderCardPicker();
    }
  }
}

function renderCardPicker() {
  const q = searchInput.value.toLowerCase();
  cardPicker.innerHTML = '';
  for (const c of allCards) {
    if (q && !((c.name || '').toLowerCase().includes(q) || String(c.id).includes(q))) continue;
    const div = document.createElement('div');
    div.className = 'card';
    div.style.cursor = 'pointer';
    div.style.borderColor = c.id === selectedCardId ? '#0071e3' : '#d2d2d7';
    const label = c.name || `参数 #${c.id}`;
    const favMark = c.is_favorited ? ' ★' : '';
    div.innerHTML = `<strong>#${c.id} ${label}${favMark}</strong>
      <p class="muted">seed ${c.params.seed}</p>`;
    div.addEventListener('click', () => {
      selectedCardId = c.id;
      selectedParams = c.params;
      renderCardPicker();
    });
    cardPicker.appendChild(div);
  }
}
searchInput.addEventListener('input', renderCardPicker);

// === 任务行 ===
function addRow() {
  const div = document.createElement('div');
  div.className = 'card';
  div.dataset.jobLocalId = String(Date.now()) + Math.random();
  div.innerHTML = `
    <textarea placeholder="输入要合成的文本..." rows="2"></textarea>
    <div class="row" style="margin-top: 8px;">
      <span class="tag status-pending">待提交</span>
      <button class="secondary" data-act="submit" style="margin-left:auto">提交</button>
      <button class="danger" data-act="remove">移除</button>
    </div>
  `;
  rowsEl.appendChild(div);

  div.querySelector('[data-act="remove"]').addEventListener('click', () => {
    div.remove();
  });
  div.querySelector('[data-act="submit"]').addEventListener('click', async () => {
    if (!selectedCardId) { toast('请先选卡', 'error'); return; }
    const text = div.querySelector('textarea').value.trim();
    if (!text) { toast('请输入文本', 'error'); return; }
    await submitOneJob(text, div);
  });
}
addRowBtn.addEventListener('click', addRow);

function _bindJobRow(rowEl, job, text) {
  rowEl.dataset.jobId = job.id;
  rowEl.dataset.jobText = text;
  const truncated = text.length > 50 ? text.slice(0, 50) + '...' : text;
  rowEl.innerHTML = `
    <p>${truncated}</p>
    <div class="row" style="margin-top: 8px;">
      <span class="tag status-${job.status}" id="status-tag">${job.status}</span>
      <div class="progress" style="flex: 1;"><div id="prog-bar" style="width: 0%"></div></div>
      <button class="secondary" data-act="hide">🙈 隐藏</button>
    </div>
    <div id="job-result"></div>
  `;
  rowEl.querySelector('[data-act="hide"]').addEventListener('click', () => rowEl.remove());
  jobs.push({ id: job.id, rowEl });
}

async function submitOneJob(text, rowEl) {
  try {
    const job = await api('POST', '/synthesize', {
      card_id: selectedCardId,
      params: selectedParams,
      text: text,
    });
    _bindJobRow(rowEl, job, text);
  } catch (e) { /* */ }
}

submitAllBtn.addEventListener('click', async () => {
  if (!selectedCardId) { toast('请先选卡', 'error'); return; }
  const items = [];
  rowsEl.querySelectorAll('div.card').forEach(r => {
    if (r.dataset.jobId) return;  // 已绑定 job 的跳过
    const text = r.querySelector('textarea')?.value.trim();
    if (text) items.push({ rowEl: r, text });
  });
  if (items.length === 0) { toast('没有可提交的行', 'error'); return; }
  try {
    const newJobs = await api('POST', '/synthesize/batch', {
      items: items.map(it => ({
        card_id: selectedCardId,
        params: selectedParams,
        text: it.text,
      })),
    });
    items.forEach((it, i) => {
      const job = newJobs[i];
      _bindJobRow(it.rowEl, job, it.text);
    });
    toast(`已提交 ${newJobs.length} 个任务`, 'success');
  } catch (e) { /* */ }
});

// === 轮询任务状态 ===
async function refreshJobs() {
  try {
    const all = await api('GET', '/jobs?limit=100');
    for (const j of all) {
      const local = jobs.find(x => x.id === j.id);
      if (local) updateJobRow(local.rowEl, j);
    }
    const running = all.filter(j => j.status === 'running').length;
    const pending = all.filter(j => j.status === 'pending').length;
    const done = all.filter(j => j.status === 'done').length;
    queueStatus.textContent = `队列：${running} 个运行中 / ${pending} 个排队中 / ${done} 个已完成`;
  } catch (e) { console.error(e); }
}

function updateJobRow(rowEl, job) {
  const tag = rowEl.querySelector('#status-tag');
  if (tag) {
    tag.textContent = job.status;
    tag.className = `tag status-${job.status}`;
  }
  const bar = rowEl.querySelector('#prog-bar');
  if (bar) bar.style.width = `${(job.progress * 100).toFixed(0)}%`;

  const resultEl = rowEl.querySelector('#job-result');
  if (resultEl && job.status === 'done' && !resultEl.innerHTML) {
    resultEl.innerHTML = `
      <audio controls src="/api/jobs/${job.id}/audio"></audio>
      <p class="muted" id="sub-${job.id}">加载字幕...</p>
      <div class="row" style="margin-top: 4px;">
        <a class="tag" href="/api/jobs/${job.id}/audio" download="audio.wav">⬇ 音频</a>
        <a class="tag" href="/api/jobs/${job.id}/subtitle" download="subtitle.srt">⬇ 字幕</a>
        <a class="tag" href="/api/jobs/${job.id}/params.json" download="params.json">⬇ 参数</a>
      </div>
    `;
    api('GET', `/jobs/${job.id}/subtitle`).then(s => {
      const el = document.getElementById(`sub-${job.id}`);
      if (el) el.textContent = s;
    });
  }
  if (resultEl && job.status === 'failed' && !resultEl.innerHTML) {
    resultEl.innerHTML = `<p style="color:#ff3b30">失败：${job.error || '未知错误'}</p>
      <button class="secondary" data-act="retry">🔄 重试</button>`;
    resultEl.querySelector('[data-act="retry"]').addEventListener('click', async () => {
      const newJob = await api('POST', '/synthesize', {
        card_id: job.card_id, params: job.params, text: rowEl.dataset.jobText,
      });
      rowEl.dataset.jobId = newJob.id;
      jobs.push({ id: newJob.id, rowEl });
      resultEl.innerHTML = '';
    });
  }
}

// === 启动 ===
loadCards();
const stop = startPolling(refreshJobs, 2000);
window.addEventListener('beforeunload', stop);
