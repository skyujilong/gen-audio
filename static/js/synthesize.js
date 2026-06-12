// 合成页主逻辑（Phase 6.2 重写）
//
// 整体流程：
//   1. 启动 → 拉卡列表 / 初始化 param-panel
//   2. 用户选卡 → 自动把卡的 params 加载到 param-panel；卡片自带的 speaker
//      同步到左侧「音色」区作默认值（用户可改）
//   3. 用户改 param-panel 任一滑条 → 改 `state.submissionParams`
//      （即下一次点"全部提交"时，提交给后端的 params）
//   4. 加任务行 → 每行一个文本 → 点"🚀 全部提交"→ 调 /api/synthesize/batch
//   5. startPolling 2s 轮询刷新任务行（保留 Phase 5 之前的轮询行为）
//
// 关键设计：
//   - `state.submissionParams` 是"提交用 params"快照；改 param-panel 即改它
//   - `state.currentSpeaker` 是"音色来源"（可与 card 自带不同）：
//       * null → 沿用卡内 speaker（用 card 自身 params.speaker/speaker_id）
//       * {speaker_id, name, tensor_base64} → 用此音色覆盖
//   - submitOneJob/submitAll 时把这两个 state 合并成最终的 params

import { api, toast, startPolling, speakers as speakersApi } from '/js/api.js';
import { renderParamPanel } from '/js/components/param-panel.js';
import { openSpeakerPicker, renderSpeakerTag } from '/js/components/speaker-picker.js';


// === state ===
const state = {
  // 当前选中的卡
  selectedCardId: null,
  selectedCard: null,            // 全量 card（GET /api/cards/{id} 拉的详情）
  // 当前选中的音色（null = 沿用卡内）
  currentSpeaker: null,
  // 提交用 params（param-panel 改即改它；与 panel.getParams() 等价但解耦刷新时机）
  submissionParams: {},
  // 本地任务列表
  jobs: [],
};


// === DOM ===
const paramHost         = document.getElementById('param-host');
const cardSearch        = document.getElementById('card-search');
const cardPicker        = document.getElementById('card-picker');
const selectedCardHint  = document.getElementById('selected-card-hint');
const speakerDisp       = document.getElementById('speaker-display');
const speakerHint       = document.getElementById('speaker-hint');
const loadSpkBtn        = document.getElementById('load-speaker-btn');
const randSpkBtn        = document.getElementById('rand-speaker-btn');
const clearSpkBtn       = document.getElementById('clear-speaker-btn');
const addRowBtn         = document.getElementById('add-row');
const submitAllBtn      = document.getElementById('submit-all');
const rowsEl            = document.getElementById('rows');
const queueStatus       = document.getElementById('queue-status');
const queueHint         = document.getElementById('queue-hint');

let panel;  // param-panel API
let allCards = [];


// === param-panel 初始化 ===
function initParamPanel(initial) {
  panel = renderParamPanel(paramHost, {
    initial: initial || {},
    mode: 'synthesize',   // 启用增强分区
    showSeed: false,      // seed 在抽卡时已定，合成时不重抽
  });
  // 任意滑条变更即更新提交用 params
  panel.onChange((p) => {
    state.submissionParams = p;
  });
  // 初次同步
  state.submissionParams = panel.getParams();
}


// === 卡选择 ===
async function loadCards() {
  try {
    allCards = await api('GET', '/cards');
    renderCardPicker();

    // URL ?card_id=...  → 自动选
    const url = new URL(location.href);
    const preselect = parseInt(url.searchParams.get('card_id') || '0', 10);
    if (preselect) {
      const c = allCards.find(c => c.id === preselect);
      if (c) await selectCard(c);
    } else {
      // 无预选：用空 params 初始化面板（不阻塞）
      initParamPanel({});
    }
  } catch (e) { /* toast already */ }
}

function renderCardPicker() {
  const q = cardSearch.value.toLowerCase();
  cardPicker.innerHTML = '';
  const filtered = allCards.filter(c => {
    if (!q) return true;
    const label = (c.name || `参数 #${c.id}`).toLowerCase();
    return label.includes(q) || String(c.id).includes(q);
  });
  if (filtered.length === 0) {
    cardPicker.innerHTML = '<p class="text-muted text-sm">没有匹配的卡。</p>';
    return;
  }
  for (const c of filtered) {
    const cell = document.createElement('div');
    cell.className = 'speaker-cell' + (c.id === state.selectedCardId ? ' selected' : '');
    cell.style.cursor = 'pointer';
    const label = c.name || `参数 #${c.id}`;
    const favMark = c.is_favorited ? '⭐ ' : '';
    const spkMark = c.speaker_id ? ' 🎤' : '';
    const truncated = (c.demo_text || '').length > 30
      ? c.demo_text.slice(0, 30) + '...'
      : (c.demo_text || '');
    cell.innerHTML = `
      <div class="name">${favMark}#${c.id} ${label}${spkMark}</div>
      <div class="tags">seed ${c.params?.seed ?? '?'} · temp ${c.params?.temperature ?? '?'}</div>
      <div class="tags">${truncated}</div>
    `;
    cell.addEventListener('click', () => selectCard(c));
    cardPicker.appendChild(cell);
  }
}

cardSearch.addEventListener('input', renderCardPicker);


// === 选卡 → 拉详情 + 加载到 param-panel ===
async function selectCard(c) {
  try {
    // 列表接口可能不返回 demo_text / params 完整字段；为安全拉一次详情
    const detail = await api('GET', `/cards/${c.id}`);
    state.selectedCardId = detail.id;
    state.selectedCard = detail;
    // 加载到 param-panel
    panel.setParams(detail.params || {});
    state.submissionParams = panel.getParams();
    // 同步音色默认值（用户后续可改；不自动覆盖 state.currentSpeaker）
    if (detail.speaker_id != null) {
      // 卡自带音色：渲染成默认 tag 但 state.currentSpeaker 留 null
      // （"沿用卡内"语义 = 提交时把卡内 speaker_id 带上）
      renderSpeakerBlock({speaker_id: detail.speaker_id, name: `库内 #${detail.speaker_id}`, is_favorited: false}, true);
    } else {
      renderSpeakerBlock(null, true);
    }
    selectedCardHint.textContent = `#${detail.id} ${detail.name || ''}`.trim();
    renderCardPicker();  // 高亮切换
    toast(`已选 #${detail.id} ${detail.name || ''}`.trim(), 'success');
  } catch (e) { /* */ }
}


// === 音色区渲染 ===
//
// @param {object|null} speaker   形如 {speaker_id, name, is_favorited}；null = 沿用卡内
// @param {boolean} isFromCard    true 表示这是「卡自带音色」默认渲染，
//                                点 tag 时弹 picker 即可改；不影响 state.currentSpeaker
function renderSpeakerBlock(speaker, isFromCard) {
  speakerDisp.innerHTML = '';
  const tag = renderSpeakerTag(speaker, { onOpen: loadSpeakerFromLibrary });
  speakerDisp.appendChild(tag);
  // 沿用卡内时：写明「沿用卡内音色」；否则显示名字
  if (isFromCard) {
    speakerHint.textContent = '沿用卡内音色';
  } else if (speaker && speaker.speaker_id != null) {
    speakerHint.textContent = speaker.name || `#${speaker.speaker_id}`;
  } else {
    speakerHint.textContent = '未绑定音色（使用卡内或 ChatTTS 默认）';
  }
}


// === 加载音色库 ===
async function loadSpeakerFromLibrary() {
  try {
    const sel = await openSpeakerPicker({
      selectedId: state.currentSpeaker?.speaker_id ?? state.selectedCard?.speaker_id ?? null,
    });
    if (sel) {
      state.currentSpeaker = {
        speaker_id: sel.id,
        name: sel.name,
        tensor_base64: sel.tensor_base64,
        is_favorited: sel.is_favorited,
      };
      renderSpeakerBlock(state.currentSpeaker, false);
      toast(`已加载「${sel.name}」`, 'success');
    }
  } catch (e) { /* */ }
}


// === 随机音色 ===
async function randomSpeaker() {
  try {
    const data = await speakersApi.random();
    state.currentSpeaker = {
      speaker_id: data.speaker_id,  // null
      name: null,
      tensor_base64: data.tensor_base64,
    };
    renderSpeakerBlock(
      { speaker_id: null, name: '🎲 随机（不存库）', is_favorited: false },
      false,
    );
    toast('已随机生成音色（不存库）', 'success');
  } catch (e) { /* */ }
}


// === 清除自定义音色 → 沿用卡内 ===
function clearSpeakerOverride() {
  state.currentSpeaker = null;
  if (state.selectedCard) {
    if (state.selectedCard.speaker_id != null) {
      renderSpeakerBlock(
        { speaker_id: state.selectedCard.speaker_id, name: `库内 #${state.selectedCard.speaker_id}`, is_favorited: false },
        true,
      );
    } else {
      renderSpeakerBlock(null, true);
    }
  } else {
    renderSpeakerBlock(null, true);
  }
  toast('已切换为「沿用卡内音色」', 'info');
}


// === 任务行 ===
function addRow() {
  const div = document.createElement('div');
  div.className = 'card';
  div.dataset.jobLocalId = String(Date.now()) + Math.random();
  div.innerHTML = `
    <textarea placeholder="输入要合成的文本..." rows="2"></textarea>
    <div class="row" style="margin-top: 8px;">
      <span class="tag status-pending">待提交</span>
      <button class="secondary" data-act="submit" style="margin-left:auto" type="button">提交</button>
      <button class="danger" data-act="remove" type="button">移除</button>
    </div>
  `;
  rowsEl.appendChild(div);

  div.querySelector('[data-act="remove"]').addEventListener('click', () => {
    div.remove();
    updateQueueHint();
  });
  div.querySelector('[data-act="submit"]').addEventListener('click', async () => {
    if (!state.selectedCardId) { toast('请先选卡', 'error'); return; }
    const text = div.querySelector('textarea').value.trim();
    if (!text) { toast('请输入文本', 'error'); return; }
    await submitOneJob(text, div);
  });
  updateQueueHint();
}
addRowBtn.addEventListener('click', addRow);


function updateQueueHint() {
  const total = rowsEl.querySelectorAll('div.card').length;
  const pending = rowsEl.querySelectorAll('div.card:not([data-job-id])').length;
  queueHint.textContent = `${total} 行（${pending} 待提交）`;
}


// === 构造提交用的 params（合并当前选中的 speaker）===
function _buildSubmitParams() {
  const p = { ...state.submissionParams };
  // 整数字段
  for (const k of ['speed', 'oral', 'laugh', 'break_', 'top_k', 'nfe', 'max_new_token']) {
    if (typeof p[k] !== 'undefined') p[k] = Math.round(p[k]);
  }
  // 音色优先级：state.currentSpeaker（用户改）> card 自带 speaker_id
  if (state.currentSpeaker) {
    if (state.currentSpeaker.speaker_id != null) {
      p.speaker_id = state.currentSpeaker.speaker_id;
      delete p.speaker;  // 库引用优先
    } else if (state.currentSpeaker.tensor_base64) {
      p.speaker = state.currentSpeaker.tensor_base64;
      delete p.speaker_id;
    }
  } else if (state.selectedCard && state.selectedCard.speaker_id != null) {
    p.speaker_id = state.selectedCard.speaker_id;
    delete p.speaker;
  }
  // TtsParams.speaker 是必填 str 字段；用 speaker_id 时要塞空串占位（后端 _resolve_speaker_id
  // 会按 speaker_id 从库读 tensor_base64 覆盖之）
  if (p.speaker == null) p.speaker = '';
  return p;
}


function _bindJobRow(rowEl, job, text) {
  rowEl.dataset.jobId = job.id;
  rowEl.dataset.jobText = text;
  const truncated = text.length > 50 ? text.slice(0, 50) + '...' : text;
  rowEl.innerHTML = `
    <p>${truncated}</p>
    <div class="row" style="margin-top: 8px;">
      <span class="tag status-${job.status}" data-role="status">${job.status}</span>
      <div class="progress" style="flex: 1;"><div data-role="prog-bar" style="width: 0%"></div></div>
      <button class="secondary" data-act="hide" type="button">🙈 隐藏</button>
    </div>
    <div data-role="job-result"></div>
  `;
  rowEl.querySelector('[data-act="hide"]').addEventListener('click', () => {
    rowEl.remove();
    updateQueueHint();
  });
  state.jobs.push({ id: job.id, rowEl });
  updateQueueHint();
}


async function submitOneJob(text, rowEl) {
  const params = _buildSubmitParams();
  try {
    const job = await api('POST', '/synthesize', {
      card_id: state.selectedCardId,
      params,
      text,
    });
    _bindJobRow(rowEl, job, text);
  } catch (e) { /* */ }
}


submitAllBtn.addEventListener('click', async () => {
  if (!state.selectedCardId) { toast('请先选卡', 'error'); return; }
  const items = [];
  rowsEl.querySelectorAll('div.card').forEach(r => {
    if (r.dataset.jobId) return;
    const text = r.querySelector('textarea')?.value.trim();
    if (text) items.push({ rowEl: r, text });
  });
  if (items.length === 0) { toast('没有可提交的行', 'error'); return; }

  // 一次性收集所有 items 共享的 params + 各自 text
  const params = _buildSubmitParams();
  try {
    const newJobs = await api('POST', '/synthesize/batch', {
      items: items.map(it => ({
        card_id: state.selectedCardId,
        params,
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


// === 轮询任务状态（保留 Phase 5 之前 startPolling 2s 行为）===
async function refreshJobs() {
  if (state.jobs.length === 0) {
    queueStatus.textContent = '';
    return;
  }
  try {
    const all = await api('GET', '/jobs?limit=100');
    for (const j of all) {
      const local = state.jobs.find(x => x.id === j.id);
      if (local) updateJobRow(local.rowEl, j);
    }
    const running = all.filter(j => j.status === 'running').length;
    const pending = all.filter(j => j.status === 'pending').length;
    const done = all.filter(j => j.status === 'done').length;
    queueStatus.textContent = `队列：${running} 个运行中 / ${pending} 个排队中 / ${done} 个已完成`;
  } catch (e) { console.error(e); }
}


function updateJobRow(rowEl, job) {
  const tag = rowEl.querySelector('[data-role="status"]');
  if (tag) {
    tag.textContent = job.status;
    tag.className = `tag status-${job.status}`;
  }
  const bar = rowEl.querySelector('[data-role="prog-bar"]');
  if (bar) bar.style.width = `${(job.progress * 100).toFixed(0)}%`;

  const resultEl = rowEl.querySelector('[data-role="job-result"]');
  if (resultEl && job.status === 'done' && !resultEl.innerHTML) {
    resultEl.innerHTML = `
      <audio controls src="/api/jobs/${job.id}/audio"></audio>
      <p class="text-muted text-sm" data-role="sub-${job.id}">加载字幕...</p>
      <div class="row" style="margin-top: 4px;">
        <a class="tag" href="/api/jobs/${job.id}/audio" download="audio.wav">⬇ 音频</a>
        <a class="tag" href="/api/jobs/${job.id}/subtitle" download="subtitle.srt">⬇ 字幕</a>
        <a class="tag" href="/api/jobs/${job.id}/params.json" download="params.json">⬇ 参数</a>
      </div>
    `;
    api('GET', `/jobs/${job.id}/subtitle`).then(s => {
      const el = resultEl.querySelector(`[data-role="sub-${job.id}"]`);
      if (el) el.textContent = s;
    });
  }
  if (resultEl && job.status === 'failed' && !resultEl.innerHTML) {
    resultEl.innerHTML = `<p style="color:#ff3b30">失败：${job.error || '未知错误'}</p>
      <button class="secondary" data-act="retry" type="button">🔄 重试</button>`;
    resultEl.querySelector('[data-act="retry"]').addEventListener('click', async () => {
      try {
        const newJob = await api('POST', '/synthesize', {
          card_id: job.card_id,
          params: job.params,
          text: rowEl.dataset.jobText,
        });
        rowEl.dataset.jobId = newJob.id;
        state.jobs.push({ id: newJob.id, rowEl });
        resultEl.innerHTML = '';
      } catch (e) { /* */ }
    });
  }
}


// === 启动 ===
function bootstrap() {
  initParamPanel({});
  loadCards();

  loadSpkBtn.addEventListener('click', loadSpeakerFromLibrary);
  randSpkBtn.addEventListener('click', randomSpeaker);
  clearSpkBtn.addEventListener('click', clearSpeakerOverride);

  const stop = startPolling(refreshJobs, 2000);
  window.addEventListener('beforeunload', stop);
}

bootstrap();
