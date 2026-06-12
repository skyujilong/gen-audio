// 列表页逻辑
import { api, toast } from '/js/api.js';

const listEl = document.getElementById('cards-list');
const tabAll = document.getElementById('tab-all');
const tabFav = document.getElementById('tab-fav');
const importBtn = document.getElementById('import-btn');
const importModal = document.getElementById('import-modal');
const importFile = document.getElementById('import-file');
const importPreview = document.getElementById('import-preview');
const importCancel = document.getElementById('import-cancel');
const importConfirm = document.getElementById('import-confirm');

let currentTab = 'all';
let pendingImport = null;

async function loadList() {
  const query = currentTab === 'fav' ? '?favorited=true' : '';
  const cards = await api('GET', `/cards${query}`);
  listEl.innerHTML = '';
  if (cards.length === 0) {
    listEl.innerHTML = '<p class="muted">没有参数卡。</p>';
    return;
  }
  for (const c of cards) {
    const div = document.createElement('div');
    div.className = 'card';
    const favIcon = c.is_favorited ? '★' : '☆';
    const truncated = c.demo_text.length > 30 ? c.demo_text.slice(0, 30) + '...' : c.demo_text;
    const cardLabel = c.name || `参数 #${c.id}`;
    div.innerHTML = `
      <div class="row" style="margin-bottom: 8px;">
        <strong>#${c.id} ${cardLabel} ${favIcon}</strong>
      </div>
      <p class="muted">${truncated}</p>
      <p class="muted">seed ${c.params.seed} · temp ${c.params.temperature}</p>
      <audio controls src="/api/cards/${c.id}/audio"></audio>
      <div class="row" style="margin-top: 8px;">
        <button class="secondary" data-act="fav" data-id="${c.id}">${c.is_favorited ? '☆ 取消收藏' : '⭐ 收藏'}</button>
        <a class="tag" href="/synthesize?card_id=${c.id}" style="text-decoration:none;color:inherit">🎤 用此卡合成</a>
        <a class="tag" href="/draw?card_id=${c.id}" style="text-decoration:none;color:inherit" title="加载到抽卡页（可改参数/音色再生成）">🔊 抽卡页</a>
        <button class="danger" data-act="del" data-id="${c.id}" style="margin-left:auto">🗑 删</button>
      </div>
    `;
    listEl.appendChild(div);
  }

  listEl.querySelectorAll('button[data-act]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.id;
      const act = btn.dataset.act;
      if (act === 'fav') {
        const isFav = btn.textContent.includes('取消') ? false : true;
        await api('PATCH', `/cards/${id}`, { is_favorited: isFav });
        loadList();
      } else if (act === 'del') {
        if (!confirm('确认删除？此操作不可撤销。')) return;
        await api('DELETE', `/cards/${id}`);
        loadList();
      }
    });
  });
}

tabAll.addEventListener('click', () => { currentTab = 'all'; updateTabs(); loadList(); });
tabFav.addEventListener('click', () => { currentTab = 'fav'; updateTabs(); loadList(); });
function updateTabs() {
  tabAll.classList.toggle('secondary', currentTab !== 'all');
  tabFav.classList.toggle('secondary', currentTab !== 'fav');
  tabAll.style.fontWeight = currentTab === 'all' ? 'bold' : 'normal';
  tabFav.style.fontWeight = currentTab === 'fav' ? 'bold' : 'normal';
}
updateTabs();
loadList();

// 导入
importBtn.addEventListener('click', () => { importModal.style.display = 'flex'; });
importCancel.addEventListener('click', () => {
  importModal.style.display = 'none';
  pendingImport = null;
  importFile.value = '';
  importPreview.innerHTML = '';
});

importFile.addEventListener('change', async () => {
  const file = importFile.files[0];
  if (!file) return;
  const text = await file.text();
  try {
    const data = JSON.parse(text);
    if (!data.cards || !Array.isArray(data.cards)) throw new Error('格式错：需 {cards: [...]}');
    pendingImport = data.cards;
    const previewItems = pendingImport.slice(0, 3).map(c =>
      `<li>${c.name || '(无名)'} seed=${c.params?.seed ?? '?'}</li>`
    ).join('');
    const moreNote = pendingImport.length > 3
      ? `<p class="muted">...还有 ${pendingImport.length - 3} 张</p>`
      : '';
    importPreview.innerHTML = `<p>将导入 <strong>${pendingImport.length}</strong> 张卡：</p>
      <ul>${previewItems}</ul>${moreNote}`;
  } catch (e) {
    toast('JSON 解析失败：' + e.message, 'error');
    pendingImport = null;
  }
});

importConfirm.addEventListener('click', async () => {
  if (!pendingImport) return;
  try {
    const r = await api('POST', '/cards/import', { cards: pendingImport });
    toast(`成功导入 ${r.imported} 张`, 'success');
    importModal.style.display = 'none';
    pendingImport = null;
    importFile.value = '';
    importPreview.innerHTML = '';
    loadList();
  } catch (e) { /* */ }
});
