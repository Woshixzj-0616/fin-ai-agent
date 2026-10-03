/* 金融投研智能体 · 可核验审计台 */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let DATA = null;
// 页图渲染矩阵（agent extract --render 用 Matrix(1.6,1.6)）
const RENDER_SCALE = 1.6;

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
  if (!isFinite(n)) return v;
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}
function statusBadge(s) {
  const map = {
    '证据支持': 'ok', '确认错误': 'bad',
    '证据不足': 'info', '口径冲突／需人工复核': 'warn',
    'match': 'ok', 'mismatch': 'bad',
  };
  return `<span class="badge ${map[s] || 'info'}">${s}</span>`;
}
function shortSha(h) { return h ? h.slice(0, 12) + '…' : '—'; }

/* ---------- 材料台账 ---------- */
function renderMaterials() {
  const q = ($('#mat-filter').value || '').trim().toLowerCase();
  const rows = DATA.materials.filter((m) =>
    !q || m.code.includes(q) || m.name.includes(q) || String(m.year).includes(q) ||
    m.title.includes(q));
  $('#mat-count').textContent = `${rows.length} / ${DATA.materials.length} 份年报`;
  $('#mat-table tbody').innerHTML = rows.map((m) => `
    <tr>
      <td class="mono">${m.code}</td>
      <td>${m.name}</td>
      <td class="num">${m.year}</td>
      <td>${m.title}</td>
      <td class="mono">${m.announcement_id}</td>
      <td class="num">${m.pages}</td>
      <td class="mono muted" title="${m.sha256}">${shortSha(m.sha256)}</td>
    </tr>`).join('');
}

/* ---------- 证据与溯源 ---------- */
function renderEvidence() {
  const metricSel = $('#ev-metric');
  const coSel = $('#ev-company');
  if (!metricSel.options.length) {
    const metrics = [...new Set(DATA.evidence.map((e) => e.metric_name))];
    metricSel.innerHTML = '<option value="">全部指标</option>' +
      metrics.map((m) => `<option>${m}</option>`).join('');
    metricSel.addEventListener('change', renderEvidence);
  }
  if (coSel && !coSel.options.length) {
    const cos = [...new Map(DATA.evidence.map((e) =>
      [e.company_code, e.company_name || e.company_code])).entries()];
    coSel.innerHTML = '<option value="">全部公司</option>' +
      cos.map(([code, name]) => `<option value="${code}">${name}</option>`).join('');
    coSel.addEventListener('change', renderEvidence);
  }
  const filter = metricSel.value;
  const coFilter = coSel ? coSel.value : '';
  const rows = DATA.evidence.filter((e) =>
      (!filter || e.metric_name === filter) && (!coFilter || e.company_code === coFilter))
    .sort((a, b) => (a.company_code || '').localeCompare(b.company_code || '') ||
      a.metric_name.localeCompare(b.metric_name) || a.period_year - b.period_year);
  $('#ev-count').textContent = `${rows.length} 条证据 · 点击行定位原文`;
  $('#ev-table tbody').innerHTML = rows.map((e, i) => `
    <tr class="clickable" data-idx="${DATA.evidence.indexOf(e)}">
      <td>${e.company_name || e.company_code || ''}</td>
      <td>${e.metric_name}</td>
      <td class="num">${e.period_year}${e.adjustment !== 'as_reported' ? ' <span class="muted">' + e.adjustment + '</span>' : ''}</td>
      <td class="num mono">${fmtNum(e.value)}</td>
      <td>${e.unit || '—'}</td>
      <td class="num">${e.page}</td>
      <td class="muted">${e.scope || '—'}</td>
    </tr>`).join('');
  $$('#ev-table tbody tr').forEach((tr) => {
    tr.addEventListener('click', () => {
      $$('#ev-table tbody tr').forEach((x) => x.classList.remove('selected'));
      tr.classList.add('selected');
      showSource(DATA.evidence[+tr.dataset.idx]);
    });
  });
}

function showSource(e) {
  $('#src-title').textContent =
    `${e.company_name} ${e.report_year} 年报 · 第 ${e.page} 页 · ${e.metric_name}`;
  const imgPath = e.page_image || DATA.page_images[e.page];
  const meta = `
    <div class="src-meta">
      <b>原始标签</b> ${e.original_label || '—'}<br>
      <b>数值</b> ${e.raw_value ?? '—'} <b>单位</b> ${e.unit || '—'}
      → 归一 <b>${fmtNum(e.normalized_value)}</b> ${e.normalized_unit || ''}<br>
      <b>期间</b> ${e.period_year} 年 · <b>口径</b> ${e.scope || '—'}
      · <b>调整列</b> ${e.adjustment}<br>
      <b>定位</b> PDF 第 ${e.page} 页 · bbox <span class="mono">[${e.value_bbox?.map((n) => n.toFixed(1)).join(', ')}]</span><br>
      <b>提取</b> ${e.extraction_method} · <b>指纹</b> <span class="mono">${shortSha(e.source_sha256)}</span>
      ${e.issues?.length ? `<br><b>待复核</b> ${e.issues.join('、')}` : ''}
    </div>`;
  if (!imgPath) {
    $('#src-view').innerHTML = `<div class="placeholder">第 ${e.page} 页未渲染</div>${meta}`;
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
      // 高亮底色 + 边框
      ctx.fillStyle = 'rgba(26, 86, 219, 0.22)';
      ctx.fillRect(x0 - 2, y0 - 2, x1 - x0 + 4, y1 - y0 + 4);
      ctx.strokeStyle = '#1a56db';
      ctx.lineWidth = 3;
      ctx.strokeRect(x0 - 2, y0 - 2, x1 - x0 + 4, y1 - y0 + 4);
    }
    // 也标一下标签框（若有）
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
  $('#ck-count').textContent = `${DATA.checks.length} 条结构化陈述 · 判定由本地 Python 规则生成`;
  $('#ck-table tbody').innerHTML = DATA.checks.map((c) => {
    const ev = (c.evidence_ids || []).join(', ') || '—';
    return `
    <tr>
      <td class="mono">${c.claim_id}</td>
      <td>${c.original_sentence || c.sentence || '—'}</td>
      <td class="muted">${c.check_item || '—'}</td>
      <td>${statusBadge(c.status)}</td>
      <td>${c.reason || '—'}${c.calculation?.value ? `<br><span class="muted">程序建议：<b>${c.calculation.value}</b></span>` : ''}${c.suggestion ? `<br><span class="muted">${c.suggestion}</span>` : ''}</td>
      <td class="mono muted">${ev}${c.page ? ` · p${c.page}` : ''}</td>
    </tr>`;
  }).join('');
}

/* ---------- 审计日志 ---------- */
function renderEvents() {
  $('#ev2-count').textContent = `${DATA.events.length} 条运行留痕`;
  $('#ev2-table tbody').innerHTML = DATA.events.map((e) => {
    const { time, run_id, event, ...rest } = e;
    const detail = Object.entries(rest)
      .map(([k, v]) => `<span class="muted">${k}=</span>${typeof v === 'object' ? JSON.stringify(v) : v}`)
      .join(' · ');
    return `
    <tr>
      <td class="mono muted">${(time || '').replace('T', ' ').slice(0, 19)}</td>
      <td class="mono">${event}</td>
      <td class="muted">${detail}</td>
    </tr>`;
  }).join('');
}

/* ---------- 启动 ---------- */
fetch('data/bundle.json')
  .then((r) => r.json())
  .then((d) => {
    DATA = d;
    renderMaterials();
    renderEvidence();
    renderChecks();
    renderEvents();
  })
  .catch((e) => {
    document.querySelector('main').innerHTML =
      `<p style="color:#c0392b">数据加载失败：${e.message}</p>`;
  });
