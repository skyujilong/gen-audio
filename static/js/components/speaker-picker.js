// speaker-picker.js —— 音色库下拉 / 网格弹窗
//
// 用法：
//   import { openSpeakerPicker } from '/js/components/speaker-picker.js';
//   const result = await openSpeakerPicker();  // -> {speaker_id, name, tensor_base64} | null
//
// 也支持 `renderSpeakerTag` 工具方法用于把当前选中的音色显示成 .speaker-tag：
//   import { renderSpeakerTag } from '/js/components/speaker-picker.js';
//   const tag = renderSpeakerTag({name, speaker_id, is_favorited}, { onClear, onOpen });
//   container.appendChild(tag);
//
// 弹窗内含：搜索框、收藏过滤、上传 .pt 入口、网格展示。
// 返回的 `tensor_base64` 来自后端 `GET /api/speakers/{id}`（不是 list 接口节省的列表）。
// 上传成功后弹窗自动刷新。

import { api, toast } from '/js/api.js';


function _h(tag, props = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') el.className = v;
    else if (k === 'style') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === 'dataset') {
      Object.assign(el.dataset, v);
    } else if (k in el) {
      el[k] = v;
    } else {
      el.setAttribute(k, v);
    }
  }
  for (const c of children) {
    if (c == null) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}


function _closeModal(modal) {
  modal.remove();
  document.removeEventListener('keydown', _escCloser);
}

let _escCloser = null;


async function _uploadSpeakerFlow() {
  // 用 <input type=file> 选 .pt，调 /api/speakers/upload（multipart），刷新弹窗
  const file = await new Promise(resolve => {
    const i = _h('input', { type: 'file', accept: '.pt' });
    i.addEventListener('change', () => resolve(i.files[0] || null));
    i.click();
  });
  if (!file) return null;

  const name = (prompt('给这个音色起个名字（必填）') || '').trim();
  if (!name) {
    toast('已取消：名字不能空', 'info');
    return null;
  }

  const fd = new FormData();
  fd.append('file', file);
  fd.append('name', name);

  try {
    const spk = await api('POST', '/speakers/upload', fd, /* multipart */ true);
    toast(`已上传「${spk.name}」`, 'success');
    return spk;
  } catch (e) {
    // api() 内部已 toast
    return null;
  }
}


/**
 * 打开音色库弹窗。
 * @param {object} opts
 * @param {number} [opts.selectedId] 默认选中的 speaker_id
 * @returns {Promise<{speaker_id:number,name:string,tensor_base64:string} | null>}
 */
export async function openSpeakerPicker(opts = {}) {
  let selectedId = opts.selectedId ?? null;
  let selected = null;
  let speakers = [];

  const searchInput = _h('input', { type: 'text', placeholder: '搜索名字...', style: { width: '60%' } });
  const favOnlyCb   = _h('input', { type: 'checkbox' });
  const grid        = _h('div', { class: 'speaker-grid' });
  const status      = _h('p', { class: 'text-muted text-sm' }, ['加载中...']);
  const okBtn       = _h('button', { class: 'primary' }, ['选用']);
  okBtn.disabled = true;

  function renderGrid() {
    grid.innerHTML = '';
    const q = searchInput.value.trim().toLowerCase();
    const filtered = speakers.filter(s => {
      if (favOnlyCb.checked && !s.is_favorited) return false;
      if (q && !s.name.toLowerCase().includes(q)) return false;
      return true;
    });
    if (filtered.length === 0) {
      grid.appendChild(_h('p', { class: 'text-muted' }, ['没有匹配的音色。']));
      return;
    }
    for (const s of filtered) {
      const cell = _h('div', {
        class: 'speaker-cell' + (s.id === selectedId ? ' selected' : ''),
        dataset: { id: String(s.id) },
        onClick: () => {
          selectedId = s.id;
          okBtn.disabled = false;
          renderGrid();
        },
      }, [
        _h('div', { class: 'name' }, [s.is_favorited ? '⭐ ' : '', s.name]),
        _h('div', { class: 'tags' }, s.tags.map(t => _h('span', { class: 'tag-chip' }, [t]))),
      ]);
      grid.appendChild(cell);
    }
  }

  async function reload() {
    try {
      speakers = await api('GET', '/speakers');
      status.textContent = `共 ${speakers.length} 个音色`;
      renderGrid();
    } catch (e) { /* toast already */ }
  }

  searchInput.addEventListener('input', renderGrid);
  favOnlyCb.addEventListener('change', renderGrid);

  // 上传按钮
  const uploadBtn = _h('button', { class: 'secondary', onClick: async () => {
    const spk = await _uploadSpeakerFlow();
    if (spk) {
      await reload();
      selectedId = spk.id;
      okBtn.disabled = false;
    }
  }}, ['📤 上传 .pt']);

  // 收藏切换
  const favToggleBtn = _h('button', { class: 'secondary', disabled: true, onClick: async () => {
    if (selectedId == null) return;
    try {
      await api('POST', `/speakers/${selectedId}/favorite`);
      await reload();
    } catch (e) { /* */ }
  }}, ['⭐ 切收藏']);

  // 关闭按钮 + 选用按钮
  const closeBtn = _h('button', { class: 'close', onClick: () => _closeModal(modal) }, ['×']);
  okBtn.addEventListener('click', async () => {
    if (selectedId == null) return;
    okBtn.disabled = true;
    okBtn.textContent = '加载中...';
    try {
      const detail = await api('GET', `/speakers/${selectedId}`);
      selected = detail;
      _closeModal(modal);
      resolvePromise(detail);
    } catch (e) {
      okBtn.disabled = false;
      okBtn.textContent = '选用';
    }
  });

  const modal = _h('div', { class: 'modal-bg' }, [
    _h('div', { class: 'modal' }, [
      _h('header', {}, [
        _h('h2', {}, ['📚 音色库']),
        closeBtn,
      ]),
      _h('div', { class: 'modal-body' }, [
        _h('div', { class: 'row', style: { gap: '12px', marginBottom: '12px' } }, [
          searchInput,
          _h('label', { class: 'text-sm' }, [favOnlyCb, '仅收藏']),
          uploadBtn,
        ]),
        status,
        grid,
      ]),
      _h('footer', {}, [
        favToggleBtn,
        _h('button', { class: 'secondary', onClick: () => _closeModal(modal) }, ['取消']),
        okBtn,
      ]),
    ]),
  ]);
  document.body.appendChild(modal);
  _escCloser = (e) => { if (e.key === 'Escape') _closeModal(modal); };
  document.addEventListener('keydown', _escCloser);

  // 选区联动：切选时启用收藏按钮
  const observer = new MutationObserver(() => {
    favToggleBtn.disabled = !grid.querySelector('.speaker-cell.selected');
  });
  observer.observe(grid, { attributes: true, subtree: true, attributeFilter: ['class'] });

  let resolvePromise;
  const promise = new Promise(res => { resolvePromise = res; });
  await reload();
  return promise;
}


/**
 * 把当前选中的 speaker 渲染为 .speaker-tag。
 * @param {{speaker_id:number|null, name:string|null, is_favorited?:boolean} | null} speaker
 * @param {{onClear?:Function, onOpen?:Function}} handlers
 * @returns {HTMLElement}
 */
export function renderSpeakerTag(speaker, handlers = {}) {
  if (!speaker || speaker.speaker_id == null) {
    const tag = _h('span', { class: 'speaker-tag empty' }, ['未绑定音色']);
    if (handlers.onOpen) {
      tag.style.cursor = 'pointer';
      tag.addEventListener('click', handlers.onOpen);
    }
    return tag;
  }
  const tag = _h('span', { class: 'speaker-tag' + (speaker.is_favorited ? ' fav' : '') }, [
    speaker.name || `#${speaker.speaker_id}`,
  ]);
  if (handlers.onOpen) {
    tag.style.cursor = 'pointer';
    tag.addEventListener('click', handlers.onOpen);
  }
  return tag;
}
