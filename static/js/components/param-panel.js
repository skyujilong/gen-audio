// param-panel.js —— 7 滑条共享组件 + 增强分区
//
// API：
//   import { renderParamPanel } from '/js/components/param-panel.js';
//   const panel = renderParamPanel(containerEl, {
//     initial: { temperature: 0.3, top_p: 0.7, top_k: 20, ... },  // 可选
//     mode: 'draw' | 'synthesize',   // draw 时灰显增强分区
//     showSeed: true|false,          // 是否渲染 seed 行
//   });
//   panel.getParams() -> { seed, temperature, top_p, top_k, speed, oral, laugh, break_,
//                          repetition_penalty, max_new_token, skip_refine_text,
//                          enhance_audio, denoise_audio, solver, nfe, tau }
//   panel.setParams(p)    // 反向回填（用于"打开已有卡 / 加载历史 params"）
//   panel.setDisabled(b)  // 整体灰显
//   panel.onChange(fn)    // 任意滑条/数值变更时回调
//
// 7 个核心滑条 + seed + max_new_token + repetition_penalty + skip_refine_text + 增强分区（5 字段）。
// 所有字段名直接对到后端 `TtsParams` / `DrawRequest` 的字段名。

import { api } from '/js/api.js';


// 滑条定义：(字段名, 标签, min, max, step, default, integer)
const SLIDERS = [
  ['temperature',       '温度 Temp',     0.00001, 1.0, 0.01,  0.3,  false],
  ['top_p',             'Top P',         0.1,     0.9, 0.05,  0.7,  false],
  ['top_k',             'Top K',         1,       20,  1,     20,   true ],
  ['repetition_penalty','重复惩罚',     1.0,     2.0, 0.05,  1.05, false],
  ['speed',             '语速 Speed',    0,       10,  1,     5,    true ],
  ['oral',              '口语化 Oral',   0,       9,   1,     0,    true ],
  ['laugh',             '笑声 Laugh',    0,       9,   1,     0,    true ],
  ['break_',            '停顿 Break',    0,       9,   1,     0,    true ],
];


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


function _bindRangeNumber(rangeEl, numEl, onChange) {
  rangeEl.addEventListener('input', () => {
    numEl.value = rangeEl.value;
    if (onChange) onChange();
  });
  numEl.addEventListener('input', () => {
    const v = numEl.value;
    // 数值框超出范围时不强行改 range，让用户继续输入
    if (v === '') return;
    if (parseFloat(v) >= parseFloat(rangeEl.min) && parseFloat(v) <= parseFloat(rangeEl.max)) {
      rangeEl.value = v;
    }
    if (onChange) onChange();
  });
}


function _buildSliderRow(field, label, min, max, step, defaultVal, isInteger, onChange) {
  const range = _h('input', {
    type: 'range', min, max, step, value: defaultVal,
    dataset: { field },
  });
  const num = _h('input', {
    type: 'number', min, max, step, value: defaultVal,
    dataset: { field },
  });
  _bindRangeNumber(range, num, onChange);
  return _h('div', { class: 'slider-row' + (isInteger ? ' integer' : '') }, [
    _h('label', {}, [label]),
    range,
    num,
  ]);
}


function _buildEnhanceSection(initial, onChange) {
  const enhanceCb  = _h('input', { type: 'checkbox', dataset: { field: 'enhance_audio' } });
  const denoiseCb  = _h('input', { type: 'checkbox', dataset: { field: 'denoise_audio' } });
  const solverSel  = _h('select',  { dataset: { field: 'solver' } }, [
    _h('option', { value: 'midpoint' }, ['midpoint']),
    _h('option', { value: 'rk4' },      ['rk4']),
    _h('option', { value: 'euler' },    ['euler']),
  ]);
  const nfeRange   = _h('input', { type: 'range', min: 1, max: 128, step: 1, value: initial.nfe ?? 64, dataset: { field: 'nfe' } });
  const nfeNum     = _h('input', { type: 'number', min: 1, max: 128, step: 1, value: initial.nfe ?? 64, dataset: { field: 'nfe' } });
  const tauRange   = _h('input', { type: 'range', min: 0, max: 1, step: 0.01, value: initial.tau ?? 0.5, dataset: { field: 'tau' } });
  const tauNum     = _h('input', { type: 'number', min: 0, max: 1, step: 0.01, value: initial.tau ?? 0.5, dataset: { field: 'tau' } });

  // 设初始值
  enhanceCb.checked = !!initial.enhance_audio;
  denoiseCb.checked = !!initial.denoise_audio;
  solverSel.value   = initial.solver ?? 'midpoint';

  for (const el of [enhanceCb, denoiseCb, solverSel, nfeRange, nfeNum, tauRange, tauNum]) {
    el.addEventListener('input',  () => onChange && onChange());
    el.addEventListener('change', () => onChange && onChange());
  }
  _bindRangeNumber(nfeRange, nfeNum, onChange);
  _bindRangeNumber(tauRange, tauNum, onChange);

  return _h('div', { class: 'param-enhance' }, [
    _h('div', { class: 'form-row' }, [
      _h('label', {}, [enhanceCb, '音频增强 enhance']),
    ]),
    _h('div', { class: 'form-row' }, [
      _h('label', {}, [denoiseCb, '降噪 denoise']),
    ]),
    _h('div', { class: 'form-row' }, [
      _h('label', {}, ['Solver']),
      solverSel,
    ]),
    _h('div', { class: 'slider-row integer' }, [
      _h('label', {}, ['NFE Steps']), nfeRange, nfeNum,
    ]),
    _h('div', { class: 'slider-row' }, [
      _h('label', {}, ['Tau']),       tauRange, tauNum,
    ]),
  ]);
}


function _buildSection(title, hint, content) {
  return _h('div', { class: 'form-section' }, [
    _h('h3', {}, [title, hint ? _h('span', { class: 'hint' }, [hint]) : null]),
    content,
  ]);
}


/**
 * 渲染参数面板到指定容器。
 *
 * @param {HTMLElement} container
 * @param {object}   opts
 * @param {object?}  opts.initial    初始值（部分字段缺省用默认值）
 * @param {'draw'|'synthesize'} opts.mode  draw 时灰显增强分区
 * @param {boolean} opts.showSeed    是否渲染 seed + max_new_token + repetition_penalty
 * @returns {object} panel API
 */
export function renderParamPanel(container, opts = {}) {
  const initial = opts.initial || {};
  const mode = opts.mode || 'synthesize';
  const showSeed = opts.showSeed !== false;
  container.innerHTML = '';
  let onChangeCb = null;

  const emit = () => { if (onChangeCb) onChangeCb(panel.getParams()); };

  // 1) seed + max_new_token 行（仅 draw 页 show）
  const seedInput = showSeed ? _h('input', {
    type: 'number', min: 0, max: 2147483647, step: 1, value: initial.seed ?? '',
    placeholder: '随机', dataset: { field: 'seed' },
  }) : null;
  const maxTokenInput = showSeed ? _h('input', {
    type: 'number', min: 256, max: 4096, step: 256, value: initial.max_new_token ?? 2048,
    dataset: { field: 'max_new_token' },
  }) : null;
  const skipRefineCb = _h('input', {
    type: 'checkbox', dataset: { field: 'skip_refine_text' },
  });
  skipRefineCb.checked = !!initial.skip_refine_text;

  if (showSeed) {
    seedInput.addEventListener('input', emit);
    maxTokenInput.addEventListener('input', emit);
  }
  skipRefineCb.addEventListener('change', emit);

  const seedRow = showSeed ? _h('div', { class: 'slider-row integer' }, [
    _h('label', {}, ['Seed (留空=随机)']), seedInput,
  ]) : null;

  const maxTokenRow = showSeed ? _h('div', { class: 'slider-row integer' }, [
    _h('label', {}, ['Max Tokens']), maxTokenInput,
  ]) : null;

  // 2) 7 个核心滑条
  const sliderRows = [];
  for (const [field, label, min, max, step, defaultVal, isInteger] of SLIDERS) {
    const v = initial[field] !== undefined ? initial[field] : defaultVal;
    sliderRows.push(_buildSliderRow(field, label, min, max, step, v, isInteger, emit));
  }

  // 3) skip_refine_text
  const skipRefineRow = _h('div', { class: 'form-row' }, [
    _h('label', {}, [skipRefineCb, '跳过文本精炼（加速推理）']),
  ]);

  // 4) 增强分区
  const enhanceSection = _buildEnhanceSection(initial, emit);
  if (mode === 'draw') {
    enhanceSection.classList.add('is-disabled');
    // 顶部提示
    enhanceSection.insertBefore(
      _h('div', { class: 'notice' }, ['⚠ 试听不应用增强，仅最终合成生效']),
      enhanceSection.firstChild,
    );
  }

  // === 组装到容器 ===

  // 第 1 段：基础（seed + 温度三件套 + speed + max_token）
  if (showSeed) container.appendChild(_buildSection('🎲 基础', null, _h('div', {}, [
    seedRow,
    sliderRows[0],  // temperature
    sliderRows[1],  // top_p
    sliderRows[2],  // top_k
    sliderRows[3],  // repetition_penalty
    sliderRows[4],  // speed
    maxTokenRow,
    skipRefineRow,
  ])));

  // 第 2 段：风格（oral / laugh / break_）
  container.appendChild(_buildSection('🎭 风格', null, _h('div', {}, [
    sliderRows[5],  // oral
    sliderRows[6],  // laugh
    sliderRows[7],  // break_
  ])));

  // 第 3 段：增强（draw 灰显 + 提示）
  container.appendChild(_buildSection('✨ 增强 / 降噪',
    mode === 'draw' ? '试听不应用' : '仅正式合成生效',
    enhanceSection,
  ));


  // === API 暴露 ===

  const panel = {
    getParams() {
      const out = {};
      // 滑条 + 数值框：取 number 框的最终值
      container.querySelectorAll('input[data-field]').forEach(el => {
        const f = el.dataset.field;
        if (el.type === 'checkbox') {
          out[f] = el.checked;
        } else if (el.type === 'number' || el.type === 'range') {
          if (el.value === '' || el.value === undefined) return;  // seed 留空
          out[f] = parseFloat(el.value);
        }
      });
      container.querySelectorAll('select[data-field]').forEach(el => {
        out[el.dataset.field] = el.value;
      });
      return out;
    },
    setParams(p) {
      if (!p) return;
      for (const [k, v] of Object.entries(p)) {
        if (v === null || v === undefined) continue;
        const els = container.querySelectorAll(`[data-field="${k}"]`);
        for (const el of els) {
          if (el.type === 'checkbox') el.checked = !!v;
          else el.value = v;
        }
      }
      emit();
    },
    setDisabled(b) {
      container.querySelectorAll('input, select, button').forEach(el => { el.disabled = b; });
      container.classList.toggle('is-disabled', b);
    },
    onChange(fn) { onChangeCb = fn; },
  };

  return panel;
}
