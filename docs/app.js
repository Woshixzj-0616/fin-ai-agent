/* 金融投研智能体 · 可核验审计台 */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let DATA = null;
// 页图渲染矩阵（agent extract --render 用 Matrix(1.6,1.6)）
const RENDER_SCALE = 1.6;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

/* ---------- 标签页 ---------- */
$$('#tabs button').forEach((b) => {
  b.addEventListener('click', () => {
    $$('#tabs button').forEach((x) => x.classList.remove('active'));
    $$('.tab').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    $('#tab-' + b.dataset.tab).classList.add('active');
  });
});

/* ---------- 工具 ---------- */
function fmtNum(v) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (!isFinite(n)) return esc(v);
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}
function statusBadge(s) {
  const map = {
    '证据支持': 'ok', '确认错误': 'bad',
    '证据不足': 'info', '口径冲突／需人工复核': 'warn',
    'match': 'ok', 'mismatch': 'bad', '一致': 'ok', '不一致，需复核': 'bad',
    '模型判断': 'info',
  };
  return `<span class="badge ${map[s] || 'info'}">${esc(s)}</span>`;
}
function shortSha(h) { return h ? esc(h.slice(0, 12)) + '…' : '—'; }

/* ---------- 总览 ---------- */
function renderOverview() {
  const ev = DATA.evidence || [];
  const rows = (DATA.analysis && DATA.analysis.rows) || [];
  let match = 0, mismatch = 0, review = 0;
  rows.forEach((r) => {
    const c = r.reported_yoy_check;
    if (c && c.status === 'match') match += 1;
    else if (c && c.status === 'mismatch') mismatch += 1;
    else review += 1;
  });
  (DATA.checks || []).forEach((c) => {
    if (c.status === '口径冲突／需人工复核' || c.status === '证据不足') review += 1;
  });
  $('#st-companies').textContent = (DATA.companies || []).length;
  $('#st-materials').textContent = (DATA.materials || []).length;
  $('#st-evidence').textContent = ev.length;
  $('#st-match').textContent = match;
  $('#st-mismatch').textContent = mismatch;
  $('#st-review').textContent = review;
}

/* ---------- 指标与同比 ---------- */
function renderYoy() {
  const rows = (DATA.analysis && DATA.analysis.rows) || [];
  const coSel = $('#yoy-company');
  if (!coSel.options.length) {
    const cos = [...new Map(rows.map((r) =>
      [r.company_code, r.company_name || r.company_code])).entries()];
    coSel.innerHTML = '<option value="">全部公司</option>' +
      cos.map(([code, name]) => `<option value="${esc(code)}">${esc(name)}</option>`).join('');
    coSel.addEventListener('change', renderYoy);
  }
  const co = coSel.value;
  const shown = rows.filter((r) => !co || r.company_code === co);
  $('#yoy-count').textContent = `${shown.length} 行指标 · 同比核对`;
  $('#yoy-table tbody').innerHTML = shown.map((r) => {
    const yoy = r.yoy && r.yoy.status === 'ok'
      ? fmtNum(r.yoy.value) + '%'
      : (r.yoy ? r.yoy.status : '—');
    const check = r.reported_yoy_check;
    const outcome = check
      ? (check.status === 'match' ? '一致' : '不一致，需复核')
      : '未核对（缺披露值）';
    return `<tr>
      <td>${esc(r.company_name || r.company_code)}</td>
      <td class="num">${esc(r.report_year)}</td>
      <td>${esc(r.metric_name || r.metric)}</td>
      <td class="num mono">${fmtNum(r.current)}</td>
      <td class="num mono">${fmtNum(r.previous)}</td>
      <td class="num mono">${esc(yoy)}</td>
      <td class="num mono">${check ? fmtNum(check.reported) + '%' : '—'}</td>
      <td>${statusBadge(outcome)}</td>
      <td class="num">${esc(r.page ?? '—')}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="9" class="empty">没有指标行</td></tr>`;
  const basis = DATA.analysis && DATA.analysis.basis;
  $('#yoy-basis').textContent = basis || '';
}

/* ---------- 材料台账 ---------- */
function renderMaterials() {
  const q = ($('#mat-filter').value || '').trim().toLowerCase();
  const rows = (DATA.materials || []).filter((m) =>
    !q || (m.code || '').includes(q) || (m.name || '').includes(q) ||
    String(m.year || '').includes(q) || (m.title || '').includes(q));
  $('#mat-count').textContent = `${rows.length} / ${(DATA.materials || []).length} 份年报`;
  $('#mat-table tbody').innerHTML = rows.map((m) => `
    <tr>
      <td class="mono">${esc(m.code)}</td>
      <td>${esc(m.name)}</td>
      <td class="num">${esc(m.year)}</td>
      <td>${esc(m.title)}</td>
      <td class="mono">${esc(m.announcement_id)}</td>
      <td class="num">${esc(m.pages)}</td>
      <td class="mono muted" title="${esc(m.sha256)}">${shortSha(m.sha256)}</td>
    </tr>`).join('');
}

/* ---------- 证据与溯源 ---------- */
function renderEvidence() {
  const evidence = DATA.evidence || [];
  const metricSel = $('#ev-metric');
  const coSel = $('#ev-company');
  if (!metricSel.options.length) {
    const metrics = [...new Set(evidence.map((e) => e.metric_name))];
    metricSel.innerHTML = '<option value="">全部指标</option>' +
      metrics.map((m) => `<option>${esc(m)}</option>`).join('');
    metricSel.addEventListener('change', renderEvidence);
  }
  if (coSel && !coSel.options.length) {
    const cos = [...new Map(evidence.map((e) =>
      [e.company_code, e.company_name || e.company_code])).entries()];
    coSel.innerHTML = '<option value="">全部公司</option>' +
      cos.map(([code, name]) => `<option value="${esc(code)}">${esc(name)}</option>`).join('');
    coSel.addEventListener('change', renderEvidence);
  }
  const filter = metricSel.value;
  const coFilter = coSel ? coSel.value : '';
  const rows = evidence.filter((e) =>
      (!filter || e.metric_name === filter) && (!coFilter || e.company_code === coFilter))
    .sort((a, b) => (a.company_code || '').localeCompare(b.company_code || '') ||
      (a.metric_name || '').localeCompare(b.metric_name || '') ||
      ((a.period_year || 0) - (b.period_year || 0)));
  $('#ev-count').textContent = `${rows.length} 条证据 · 点击行定位原文`;
  $('#ev-table tbody').innerHTML = rows.map((e) => `
    <tr class="clickable" data-idx="${evidence.indexOf(e)}">
      <td>${esc(e.company_name || e.company_code || '')}</td>
      <td>${esc(e.metric_name)}</td>
      <td class="num">${esc(e.period_year)}${e.adjustment !== 'as_reported' ? ' <span class="muted">' + esc(e.adjustment) + '</span>' : ''}</td>
      <td class="num mono">${fmtNum(e.value)}</td>
      <td>${esc(e.unit || '—')}</td>
      <td class="num">${esc(e.page)}</td>
      <td class="muted">${esc(e.scope || '—')}</td>
    </tr>`).join('');
  $$('#ev-table tbody tr').forEach((tr) => {
    tr.addEventListener('click', () => {
      $$('#ev-table tbody tr').forEach((x) => x.classList.remove('selected'));
      tr.classList.add('selected');
      showSource(DATA.evidence[+tr.dataset.idx]);
    });
  });
  // 默认选中第一条，避免右侧长期空白
  const first = $('#ev-table tbody tr');
  if (first && !$('#ev-table tbody tr.selected')) {
    first.classList.add('selected');
    showSource(DATA.evidence[+first.dataset.idx]);
  }
}

function showSource(e) {
  $('#src-title').textContent =
    `${e.company_name || e.company_code} ${e.report_year} 年报 · 第 ${e.page} 页 · ${e.metric_name}`;
  const imgPath = e.page_image || (DATA.page_images || {})[e.page] ||
    (DATA.page_images || {})[`${e.company_code}_${e.report_year}_${e.page}`];
  const meta = `
    <div class="src-meta">
      <b>原始标签</b> ${esc(e.original_label || '—')}<br>
      <b>数值</b> ${esc(e.raw_value ?? '—')} <b>单位</b> ${esc(e.unit || '—')}
      → 归一 <b>${fmtNum(e.normalized_value)}</b> ${esc(e.normalized_unit || '')}<br>
      <b>期间</b> ${esc(e.period_year)} 年 · <b>口径</b> ${esc(e.scope || '—')}
      · <b>调整列</b> ${esc(e.adjustment)}<br>
      <b>定位</b> PDF 第 ${esc(e.page)} 页 · bbox <span class="mono">[${(e.value_bbox || []).map((n) => Number(n).toFixed(1)).join(', ')}]</span><br>
      <b>提取</b> ${esc(e.extraction_method)} · <b>指纹</b> <span class="mono">${shortSha(e.source_sha256)}</span>
      ${e.issues && e.issues.length ? `<br><b>待复核</b> ${esc(e.issues.join('、'))}` : ''}
    </div>`;
  if (!imgPath) {
    $('#src-view').innerHTML = `<div class="placeholder">第 ${esc(e.page)} 页未渲染</div>${meta}`;
    return;
  }
  $('#src-view').innerHTML = `<div id="src-canvas-wrap"><canvas id="src-canvas"></canvas></div>${meta}`;
  const img = new Image();
  img.onload = () => {
    const canvas = $('#src-canvas');
    canvas.width = img.width;
    canvas.height = img.height;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    if (e.value_bbox && e.value_bbox.length === 4) {
      const [x0, y0, x1, y1] = e.value_bbox.map((n) => n * RENDER_SCALE);
      ctx.fillStyle = 'rgba(26, 86, 219, 0.22)';
      ctx.fillRect(x0 - 2, y0 - 2, x1 - x0 + 4, y1 - y0 + 4);
      ctx.strokeStyle = '#1a56db';
      ctx.lineWidth = 3;
      ctx.strokeRect(x0 - 2, y0 - 2, x1 - x0 + 4, y1 - y0 + 4);
    }
    if (e.label_bbox && e.label_bbox.length === 4) {
      const [x0, y0, x1, y1] = e.label_bbox.map((n) => n * RENDER_SCALE);
      ctx.strokeStyle = '#0e7a3e';
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(x0 - 1, y0 - 1, x1 - x0 + 2, y1 - y0 + 2);
      ctx.setLineDash([]);
    }
    canvas.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };
  img.src = imgPath;
}

/* ---------- 陈述核查 ---------- */
function renderChecks() {
  const all = DATA.checks || [];
  const filter = $('#ck-filter').value;
  const rows = filter ? all.filter((c) => c.status === filter) : all;
  $('#ck-count').textContent = `${rows.length} / ${all.length} 条结构化陈述 · 判定由本地 Python 规则生成`;
  $('#ck-table tbody').innerHTML = rows.map((c) => {
    const ev = (c.evidence_ids || []).join(', ') || '—';
    return `
    <tr>
      <td class="mono">${esc(c.claim_id)}</td>
      <td>${esc(c.original_sentence || c.sentence || '—')}</td>
      <td class="muted">${esc(c.check_item || '—')}</td>
      <td>${statusBadge(c.status)}</td>
      <td>${esc(c.reason || '—')}${c.calculation && c.calculation.value ? `<br><span class="muted">程序建议：<b>${esc(c.calculation.value)}</b></span>` : ''}${c.suggestion ? `<br><span class="muted">${esc(c.suggestion)}</span>` : ''}</td>
      <td class="mono muted">${esc(ev)}${c.page ? ` · p${esc(c.page)}` : ''}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="6" class="empty">没有核查项</td></tr>`;
}

/* ---------- 审计日志 ---------- */
function renderEvents() {
  const all = DATA.events || [];
  const q = ($('#ev2-filter').value || '').trim().toLowerCase();
  const rows = q ? all.filter((e) => (e.event || '').toLowerCase().includes(q)) : all;
  $('#ev2-count').textContent = `${rows.length} 条运行留痕`;
  $('#ev2-table tbody').innerHTML = rows.map((e) => {
    const { time, run_id, event, ...rest } = e;
    const detail = Object.entries(rest)
      .map(([k, v]) => `<span class="muted">${esc(k)}=</span>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}`)
      .join(' · ');
    return `
    <tr>
      <td class="mono muted">${esc((time || '').replace('T', ' ').slice(0, 19))}</td>
      <td class="mono">${esc(event)}</td>
      <td class="muted">${detail}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="3" class="empty">没有事件</td></tr>`;
}

/* ---------- 启动 ---------- */
$('#mat-filter').addEventListener('input', renderMaterials);
$('#ck-filter').addEventListener('change', renderChecks);
$('#ev2-filter').addEventListener('input', renderEvents);

fetch('data/bundle.json')
  .then((r) => r.json())
  .then((d) => {
    DATA = d;
    renderOverview();
    renderYoy();
    renderMaterials();
    renderEvidence();
    renderChecks();
    renderEvents();
  })
  .catch((e) => {
    document.querySelector('main').innerHTML =
      `<p class="load-error">数据加载失败：${esc(e.message)}</p>`;
  });
