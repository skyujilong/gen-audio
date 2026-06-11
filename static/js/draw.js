// 抽卡页逻辑
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

let currentCardId = null;

drawBtn.addEventListener('click', async () => {
  drawBtn.disabled = true;
  drawBtn.textContent = '抽卡中...';
  try {
    const card = await api('POST', '/draw', {});
    currentCardId = card.card_id;
    cardNameInput.value = `参数 #${card.card_id}`;
    audioEl.src = card.demo_audio_url;
    audioEl.load();
    subtitleEl.textContent = card.demo_text;
    paramsSummary.textContent = `seed ${card.params.seed} · temp ${card.params.temperature} · top_p ${card.params.top_p} · top_k ${card.params.top_k}`;
    toSynthesizeLink.href = `/synthesize?card_id=${card.card_id}`;
    cardPanel.style.display = 'block';
    drawBtn.textContent = '🎲 再抽一张';
    // 拉一次卡详情，确认收藏状态
    const detail = await api('GET', `/cards/${card.card_id}`);
    updateFavBtn(detail.is_favorited);
  } catch (e) {
    console.error(e);
  } finally {
    drawBtn.disabled = false;
  }
});

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

cardNameInput.addEventListener('change', async () => {
  if (!currentCardId) return;
  try {
    await api('PATCH', `/cards/${currentCardId}`, { name: cardNameInput.value });
    toast('已改名', 'success');
  } catch (e) { /* */ }
});

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
