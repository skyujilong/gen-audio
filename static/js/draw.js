// 生成页主逻辑（Phase 5.6 重写）
//
// 模块拆分：
//   - state         集中状态（当前 card / 当前 speaker）
//   - initForm      初始化 param-panel + 绑定表单
//   - collectFormData  收集 form → /api/draw 请求体
//   - updateFormFromParams  生成成功后用 server 端实测 params 回填
//   - generate      点 "🔊 生成" 的主流程
//   - randomSpeaker / saveSpeakerToLibrary / loadSpeakerFromLibrary
//   - discardCard / favCard / renameCard
//   - loadCardById  URL ?card_id=... → 拉详情 → 回填（Phase 6 favorites 跳过来用）

import { api, toast, speakers as speakersApi } from '/js/api.js';
import { renderParamPanel } from '/js/components/param-panel.js';
import { openSpeakerPicker, renderSpeakerTag } from '/js/components/speaker-picker.js';


// === state ===
const state = {
  currentCardId: null,
  // 当前选中的音色。两种来源：
  //   - 库加载：{speaker_id, name, tensor_base64, is_favorited}
  //   - 随机/临时：{speaker_id: null, name: null, tensor_base64: '...'}
  currentSpeaker: null,
};


// === DOM ===
const paramHost    = document.getElementById('param-host');
const demoTextEl   = document.getElementById('f-demo-text');
const drawBtn      = document.getElementById('draw-btn');
const speakerDisp  = document.getElementById('speaker-display');
const randBtn      = document.getElementById('rand-speaker-btn');
const loadBtn      = document.getElementById('load-speaker-btn');
const saveBtn      = document.getElementById('save-speaker-btn');
const cardPanel    = document.getElementById('card-panel');
const cardIdLabel  = document.getElementById('card-id-label');
const cardNameEl   = document.getElementById('card-name');
const favBtn       = document.getElementById('fav-btn');
const audioEl      = document.getElementById('audio');
const subtitleEl   = document.getElementById('subtitle-display');
const paramsSummary = document.getElementById('params-summary');
const toSynthLink  = document.getElementById('to-synthesize');
const discardBtn   = document.getElementById('discard-btn');


// === Form 初始化 ===
let panel;  // param-panel API

function initForm() {
  panel = renderParamPanel(paramHost, {
    initial: {},
    mode: 'draw',          // draw 页：增强分区灰显
    showSeed: true,
  });
}


// === 收集表单数据 ===
function collectFormData() {
  const p = panel.getParams();
  // 试听文本
  p.demo_text = (demoTextEl.value || '').trim() || '你好，这是一段声音测试。';
  // 音色绑定：speaker_id 优先（Phase 4.1），没有则用随机生成的字符串
  if (state.currentSpeaker) {
    if (state.currentSpeaker.speaker_id != null) {
      p.speaker_id = state.currentSpeaker.speaker_id;
      // 用 speaker_id 时把 speaker 字符串清空（draw.py 会按 id 重新解析）
      delete p.speaker;
    } else if (state.currentSpeaker.tensor_base64) {
      p.speaker = state.currentSpeaker.tensor_base64;
    }
  } else {
    // 没选音色：清空 speaker 让 draw.py 走随机分支
    delete p.speaker;
  }
  // 整数字段
  for (const k of ['speed', 'oral', 'laugh', 'break_', 'top_k', 'nfe', 'max_new_token']) {
    if (typeof p[k] !== 'undefined') p[k] = Math.round(p[k]);
  }
  return p;
}


// === 用后端实测参数回填 ===
function updateFormFromParams(params) {
  if (!params) return;
  panel.setParams(params);
  if (params.demo_text) demoTextEl.value = params.demo_text;
  if (params.speaker_id != null) {
    state.currentSpeaker = state.currentSpeaker && state.currentSpeaker.speaker_id === params.speaker_id
      ? state.currentSpeaker
      : {speaker_id: params.speaker_id, name: `#${params.speaker_id}`, tensor_base64: params.speaker};
  } else if (params.speaker) {
    state.currentSpeaker = {speaker_id: null, name: null, tensor_base64: params.speaker};
  }
  renderSpeakerBlock();
}


// === 音色区渲染 ===
function renderSpeakerBlock() {
  speakerDisp.innerHTML = '';
  const tag = renderSpeakerTag(state.currentSpeaker, {
    onOpen: loadSpeakerFromLibrary,
  });
  speakerDisp.appendChild(tag);
  // "保存到音色库" 仅在当前音色未入库时启用
  saveBtn.disabled = !!(state.currentSpeaker && state.currentSpeaker.speaker_id);
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
    renderSpeakerBlock();
    toast('已随机生成音色（不存库）', 'success');
  } catch (e) { /* toast already */ }
}


// === 加载音色库 ===
async function loadSpeakerFromLibrary() {
  try {
    const sel = await openSpeakerPicker({
      selectedId: state.currentSpeaker?.speaker_id ?? null,
    });
    if (sel) {
      state.currentSpeaker = {
        speaker_id: sel.id,
        name: sel.name,
        tensor_base64: sel.tensor_base64,
        is_favorited: sel.is_favorited,
      };
      renderSpeakerBlock();
      toast(`已加载「${sel.name}」`, 'success');
    }
  } catch (e) { /* */ }
}


// === 保存当前音色到库 ===
async function saveSpeakerToLibrary() {
  if (!state.currentSpeaker || !state.currentSpeaker.tensor_base64) {
    toast('当前没有可保存的音色（先生成一个或加载一个）', 'error');
    return;
  }
  const name = (prompt('给这个音色起个名字（必填）') || '').trim();
  if (!name) {
    toast('已取消：名字不能空', 'info');
    return;
  }
  try {
    const spk = await speakersApi.create({
      name,
      tensor_base64: state.currentSpeaker.tensor_base64,
    });
    // 切换为已入库状态
    state.currentSpeaker = {
      speaker_id: spk.id,
      name: spk.name,
      tensor_base64: spk.tensor_base64,
      is_favorited: spk.is_favorited,
    };
    renderSpeakerBlock();
    toast(`已保存到音色库（id=${spk.id}）`, 'success');
  } catch (e) { /* */ }
}


// === 生成主流程 ===
async function generate() {
  drawBtn.disabled = true;
  drawBtn.textContent = '生成中...';
  try {
    const body = collectFormData();
    const card = await api('POST', '/draw', body);
    state.currentCardId = card.card_id;
    // 回填
    updateFormFromParams(card.params);

    cardPanel.classList.remove('hidden');
    cardIdLabel.textContent = `#${card.card_id}`;
    cardNameEl.value = `参数 #${card.card_id}`;
    audioEl.src = card.demo_audio_url;
    audioEl.load();
    subtitleEl.textContent = card.demo_text;
    paramsSummary.textContent = (
      `seed ${card.params.seed} · temp ${card.params.temperature} · ` +
      `top_p ${card.params.top_p} · top_k ${card.params.top_k} · ` +
      `speed ${card.params.speed} · oral ${card.params.oral} · ` +
      `laugh ${card.params.laugh} · break ${card.params.break_}`
    );
    toSynthLink.href = `/synthesize?card_id=${card.card_id}`;
    drawBtn.textContent = '🔊 再生成';

    // 拉详情确认收藏状态
    try {
      const detail = await api('GET', `/cards/${card.card_id}`);
      updateFavBtn(detail.is_favorited);
    } catch (e) { /* */ }
  } catch (e) {
    console.error(e);
  } finally {
    drawBtn.disabled = false;
  }
}


// === 收藏切换 ===
async function favCard() {
  if (!state.currentCardId) return;
  const isFav = favBtn.textContent.includes('★');
  const newFav = !isFav;
  try {
    await api('PATCH', `/cards/${state.currentCardId}`, {is_favorited: newFav});
    updateFavBtn(newFav);
    toast(newFav ? '已收藏 ⭐' : '已取消收藏', 'success');
  } catch (e) { /* */ }
}

function updateFavBtn(isFav) {
  favBtn.textContent = isFav ? '★ 已收藏' : '☆ 收藏';
}


// === 改名 ===
async function renameCard() {
  if (!state.currentCardId) return;
  try {
    await api('PATCH', `/cards/${state.currentCardId}`, {name: cardNameEl.value});
    toast('已改名', 'success');
  } catch (e) { /* */ }
}


// === 丢弃 ===
async function discardCard() {
  if (!state.currentCardId) return;
  if (!confirm('确认丢弃？此操作不可撤销。')) return;
  try {
    await api('DELETE', `/cards/${state.currentCardId}`);
    toast('已丢弃', 'success');
    cardPanel.classList.add('hidden');
    state.currentCardId = null;
  } catch (e) { /* */ }
}


// === 加载指定 card（favorites 页跳转过来） ===
async function loadCardById(cardId) {
  try {
    const c = await api('GET', `/cards/${cardId}`);
    state.currentCardId = c.id;
    updateFormFromParams(c.params);
    if (c.demo_text) demoTextEl.value = c.demo_text;
    cardPanel.classList.remove('hidden');
    cardIdLabel.textContent = `#${c.id}`;
    cardNameEl.value = c.name || `参数 #${c.id}`;
    audioEl.src = `/api/cards/${c.id}/audio`;
    audioEl.load();
    subtitleEl.textContent = c.demo_text;
    paramsSummary.textContent = `seed ${c.params.seed} · temp ${c.params.temperature}`;
    toSynthLink.href = `/synthesize?card_id=${c.id}`;
    drawBtn.textContent = '🔊 再生成';
    updateFavBtn(c.is_favorited);
  } catch (e) {
    toast(`加载 card #${cardId} 失败：${e.message}`, 'error');
  }
}


// === 启动 ===
function bootstrap() {
  initForm();
  renderSpeakerBlock();

  drawBtn.addEventListener('click', generate);
  randBtn.addEventListener('click', randomSpeaker);
  loadBtn.addEventListener('click', loadSpeakerFromLibrary);
  saveBtn.addEventListener('click', saveSpeakerToLibrary);
  favBtn.addEventListener('click', favCard);
  discardBtn.addEventListener('click', discardCard);
  cardNameEl.addEventListener('change', renameCard);

  // URL ?card_id=...  → 加载已有卡（Phase 6 跳过来用）
  const url = new URL(location.href);
  const preselectId = parseInt(url.searchParams.get('card_id') || '0', 10);
  if (preselectId) loadCardById(preselectId);
}

bootstrap();
