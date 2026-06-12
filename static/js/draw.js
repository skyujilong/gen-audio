// 生成页逻辑
import { api, toast } from '/js/api.js';

const drawBtn = document.getElementById('draw-btn');
const cardPanel = document.getElementById('card-panel');
const cardNameInput = document.getElementById('card-name');
const favBtn = document.getElementById('fav-btn');
const audioEl = document.getElementById('audio');
const subtitleEl = document.getElementById('subtitle-display');
const paramsSummary = document.getElementById('params-summary');
const toSynthesizeLink = document.getElementById('to-synthesize');
const discardBtn = document.getElementById('discard-btn');

// form 元素
const fSeed = document.getElementById('f-seed');
const randSeedBtn = document.getElementById('rand-seed-btn');
const fTempRange = document.getElementById('f-temp-range');
const fTemp = document.getElementById('f-temp');
const fToppRange = document.getElementById('f-topp-range');
const fTopp = document.getElementById('f-topp');
const fTopkRange = document.getElementById('f-topk-range');
const fTopk = document.getElementById('f-topk');
const fReppenRange = document.getElementById('f-reppen-range');
const fReppen = document.getElementById('f-reppen');
const fSpeed = document.getElementById('f-speed');
const fMaxToken = document.getElementById('f-max-token');
const fSpeaker = document.getElementById('f-speaker');
const fSpeakerTag = document.getElementById('f-speaker-tag');
const randSpeakerBtn = document.getElementById('rand-speaker-btn');
const copySpeakerBtn = document.getElementById('copy-speaker-btn');
const fRefiner = document.getElementById('f-refiner');
const fSkipRefine = document.getElementById('f-skip-refine');
const fSpkSmp = document.getElementById('f-spk-smp');
const fTxtSmp = document.getElementById('f-txt-smp');
const fDemoText = document.getElementById('f-demo-text');

let currentCardId = null;

// === range ↔ number 双向绑定 ===
function bindRangeNumber(rangeEl, numEl) {
  rangeEl.addEventListener('input', () => { numEl.value = rangeEl.value; });
  numEl.addEventListener('input', () => { rangeEl.value = numEl.value; });
}
bindRangeNumber(fTempRange, fTemp);
bindRangeNumber(fToppRange, fTopp);
bindRangeNumber(fTopkRange, fTopk);
bindRangeNumber(fReppenRange, fReppen);

// === 随机 seed ===
randSeedBtn.addEventListener('click', () => {
  fSeed.value = Math.floor(Math.random() * 2147483647);
});

// === 随机 speaker ===
async function fetchRandomSpeaker() {
  const data = await api('GET', '/draw/random_speaker');
  fSpeaker.value = data.speaker;
  updateSpeakerTag(data.speaker);
}

function updateSpeakerTag(speaker) {
  if (!speaker) {
    fSpeakerTag.textContent = '随机';
    return;
  }
  const short = speaker.length > 8 ? speaker.slice(0, 8) + '…' : speaker;
  fSpeakerTag.textContent = short;
}

randSpeakerBtn.addEventListener('click', fetchRandomSpeaker);

// === 复制 speaker ===
copySpeakerBtn.addEventListener('click', async () => {
  const spk = fSpeaker.value;
  if (!spk) {
    toast('还没有音色数据', 'error');
    return;
  }
  try {
    await navigator.clipboard.writeText(spk);
    toast('已复制 Speaker', 'success');
  } catch {
    toast('复制失败', 'error');
  }
});

// === 收集 form 数据 ===
function collectFormData() {
  const body = {};

  // seed：空 → 不传（后端随机）
  const seedVal = fSeed.value.trim();
  if (seedVal !== '') body.seed = parseInt(seedVal, 10);

  body.temperature = parseFloat(fTemp.value) || 0.3;
  body.top_p = parseFloat(fTopp.value) || 0.7;
  body.top_k = parseInt(fTopk.value, 10) || 20;
  body.repetition_penalty = parseFloat(fReppen.value) || 1.05;
  body.speed = fSpeed.value;
  body.max_new_token = parseInt(fMaxToken.value, 10) || 2048;

  // speaker：空 → 不传（后端随机）
  const spkVal = fSpeaker.value.trim();
  if (spkVal !== '') body.speaker = spkVal;

  // refiner_text：空 → 不传
  const refinerVal = fRefiner.value.trim();
  if (refinerVal !== '') body.refiner_text = refinerVal;

  body.skip_refine_text = fSkipRefine.checked;

  // spk_smp / txt_smp：空 → 不传
  const spkSmpVal = fSpkSmp.value.trim();
  if (spkSmpVal !== '') body.spk_smp = spkSmpVal;
  const txtSmpVal = fTxtSmp.value.trim();
  if (txtSmpVal !== '') body.txt_smp = txtSmpVal;

  body.demo_text = fDemoText.value.trim() || '你好，这是一段声音测试。';

  return body;
}

// === 更新 form 为实际使用的参数 ===
function updateFormFromParams(params) {
  fSeed.value = params.seed;
  fTemp.value = params.temperature;
  fTempRange.value = params.temperature;
  fTopp.value = params.top_p;
  fToppRange.value = params.top_p;
  fTopk.value = params.top_k;
  fTopkRange.value = params.top_k;
  fReppen.value = params.repetition_penalty;
  fReppenRange.value = params.repetition_penalty;
  fSpeed.value = params.speed;
  fMaxToken.value = params.max_new_token;
  fSpeaker.value = params.speaker;
  updateSpeakerTag(params.speaker);
  if (params.refiner_text) fRefiner.value = params.refiner_text;
  fSkipRefine.checked = params.skip_refine_text;
  if (params.spk_smp) fSpkSmp.value = params.spk_smp;
  if (params.txt_smp) fTxtSmp.value = params.txt_smp;
}

// === 生成 ===
drawBtn.addEventListener('click', async () => {
  drawBtn.disabled = true;
  drawBtn.textContent = '生成中...';
  try {
    const body = collectFormData();
    const card = await api('POST', '/draw', body);
    currentCardId = card.card_id;

    // 更新 form 为实际参数
    updateFormFromParams(card.params);

    // 更新卡片面板
    cardNameInput.value = `参数 #${card.card_id}`;
    audioEl.src = card.demo_audio_url;
    audioEl.load();
    subtitleEl.textContent = card.demo_text;
    paramsSummary.textContent = `seed ${card.params.seed} · temp ${card.params.temperature} · top_p ${card.params.top_p} · top_k ${card.params.top_k} · speed ${card.params.speed}`;
    toSynthesizeLink.href = `/synthesize?card_id=${card.card_id}`;
    cardPanel.style.display = 'block';
    drawBtn.textContent = '🔊 再生成';

    // 拉一次卡详情，确认收藏状态
    const detail = await api('GET', `/cards/${card.card_id}`);
    updateFavBtn(detail.is_favorited);
  } catch (e) {
    console.error(e);
  } finally {
    drawBtn.disabled = false;
  }
});

// === 收藏 ===
favBtn.addEventListener('click', async () => {
  if (!currentCardId) return;
  const isFav = favBtn.textContent.includes('★');
  const newFav = !isFav;
  try {
    await api('PATCH', `/cards/${currentCardId}`, { is_favorited: newFav });
    updateFavBtn(newFav);
    toast(newFav ? '已收藏 ⭐' : '已取消收藏', 'success');
  } catch (e) { /* toast 已 */ }
});

function updateFavBtn(isFav) {
  favBtn.textContent = isFav ? '★ 已收藏' : '☆ 收藏';
}

// === 改名 ===
cardNameInput.addEventListener('change', async () => {
  if (!currentCardId) return;
  try {
    await api('PATCH', `/cards/${currentCardId}`, { name: cardNameInput.value });
    toast('已改名', 'success');
  } catch (e) { /* */ }
});

// === 丢弃 ===
discardBtn.addEventListener('click', async () => {
  if (!currentCardId) return;
  if (!confirm('确认丢弃？此操作不可撤销。')) return;
  try {
    await api('DELETE', `/cards/${currentCardId}`);
    toast('已丢弃', 'success');
    cardPanel.style.display = 'none';
    currentCardId = null;
  } catch (e) { /* */ }
});
