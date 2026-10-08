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
function fmtBig(v) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (!isFinite(n)) return esc(v);
  const abs = Math.abs(n);
  if (abs >= 1e8) return (n / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' 亿';
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}
function scopeZh(s) {
  return { consolidated: '合并', parent_shareholders: '归母', parent_company: '母公司', unknown: '未知' }[s] || s || '—';
}
function adjZh(a) {
  return { as_reported: '披露值', before: '调整前', after: '调整后' }[a] || a || '—';
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
      <td class="num">${esc(r.period_start && r.period_end ? r.period_start + ' 至 ' + r.period_end : r.report_year)}</td>
      <td>${esc(r.metric_name || r.metric)}</td>
      <td class="num mono">${fmtBig(r.current)}</td>
      <td class="num mono">${fmtBig(r.previous)}</td>
      <td class="num mono">${esc(yoy)}</td>
      <td class="num mono">${check ? fmtNum(check.reported) + '%' : '—'}</td>
      <td>${statusBadge(outcome)}</td>
      <td class="num">${esc(r.page ?? '—')}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="9" class="empty">没有指标行</td></tr>`;
  const basis = DATA.analysis && DATA.analysis.basis;
  $('#yoy-basis').textContent = basis || '';
  let signals = document.getElementById('finance-signals');
  if (!signals) {
    signals = document.createElement('div');
    signals.id = 'finance-signals';
    $('#yoy-basis').after(signals);
  }
  const signalSource = (s) => (DATA.evidence || []).find(e => e.document_id === s.document_id);
  signals.innerHTML = ((DATA.analysis && DATA.analysis.signals) || [])
    .filter(s => !co || signalSource(s)?.company_code === co)
    .map(s => {
      const source = signalSource(s);
      const period = source ? `${source.company_name || source.company_code} · ${source.period_start || ''} 至 ${source.period_end || source.report_year}` : s.document_id || '';
      return `<details><summary>【${esc(s.epistemic_type || '事实')}】${esc(period)} · ${esc(s.description)} ${s.value == null ? '' : fmtNum(s.value)}${esc(s.unit || '')}</summary><p>${esc(s.interpretation || '')}</p><pre>${esc(JSON.stringify(s.calculation || s.observations || {}, null, 2))}</pre></details>`;
    }).join('');
  signals.innerHTML += ((DATA.analysis && DATA.analysis.qoq_rows) || [])
    .filter(r => !co || r.company_code === co)
    .map(r => `<p>${esc(r.company_code)} 单季环比 ${esc(METRICS[r.metric]?.[0] || r.metric)} · ${esc(r.period_start)} 至 ${esc(r.period_end)}：${r.qoq.status === 'ok' ? fmtNum(r.qoq.value) + '%' : esc(r.qoq.reason ?? r.qoq.status)}</p>`).join('');
}

/* ---------- 材料台账 ---------- */
function renderMaterials() {
  const q = ($('#mat-filter').value || '').trim().toLowerCase();
  const rows = (DATA.materials || []).filter((m) =>
    !q || (m.code || '').includes(q) || (m.name || '').includes(q) ||
    String(m.year || '').includes(q) || (m.title || '').includes(q));
  $('#mat-count').textContent = `${rows.length} / ${(DATA.materials || []).length} 份财务报告`;
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
      <td class="num">${esc(e.period_year)}${e.adjustment !== 'as_reported' ? ' <span class="muted">' + esc(adjZh(e.adjustment)) + '</span>' : ''}</td>
      <td class="num mono">${fmtBig(e.value)}</td>
      <td>${esc(e.unit || '—')}</td>
      <td class="num">${esc(e.page)}</td>
      <td class="muted">${esc(scopeZh(e.scope))}</td>
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
  const fieldName = window.announcementFieldLabels[e.metric_name] || e.metric_name;
  $('#src-title').textContent =
    `${e.company_name || e.company_code || '公告'} · PDF 页序 ${e.page} · ${fieldName}`;
  const imgPath = e.page_image || (DATA.page_images || {})[e.page] ||
    (DATA.page_images || {})[`${e.company_code}_${e.report_year}_${e.page}`];
  const meta = `
    <div class="src-meta">
      <b>原始标签 / 字段</b> ${esc(window.announcementFieldLabels[e.original_label] || e.original_label || '—')}<br>
      <b>数值</b> ${esc(e.raw_value ?? '—')} <b>单位</b> ${esc(e.unit || '—')}
      → 归一 <b>${fmtBig(e.normalized_value)}</b> ${esc(e.normalized_unit || '')}<br>
      ${e.period_year ? `<b>期间</b> ${esc(qaPeriodLabel(e))} · <b>口径</b> ${esc(scopeZh(e.scope))} · <b>调整列</b> ${esc(adjZh(e.adjustment))}<br>` : ''}
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
    const evHtml = (c.evidence_ids || []).length
      ? (c.evidence_ids || []).map((id) => {
          const idx = (DATA.evidence || []).findIndex((e) => e.evidence_id === id);
          return idx >= 0
            ? `<a class="evidence-chip" data-idx="${idx}">${esc(id.slice(0, 10))}…</a>`
            : esc(id.slice(0, 10)) + '…';
        }).join(' ')
      : '—';
    return `
    <tr>
      <td class="mono">${esc(c.claim_id)}</td>
      <td>${esc(c.original_sentence || c.sentence || '—')}</td>
      <td class="muted">${esc(c.check_item || '—')}</td>
      <td>${statusBadge(c.status)}</td>
      <td>${esc(c.reason || '—')}${c.verification_scope ? `<br>${esc(c.verification_scope)}` : ''}${c.suggestion ? `<br>${esc(c.suggestion)}` : ''}${c.correction ? `<details><summary>修改片段</summary><p>${esc(c.correction.before)} → ${esc(c.correction.after)}</p><p>${esc(c.correction.revised_sentence || '需人工调整句子')}</p></details>` : ''}</td>
      <td class="mono muted">${evHtml}${c.page ? ` · p${esc(c.page)}` : ''}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="6" class="empty">没有核查项</td></tr>`;
  // 核查页证据芯片 → 跳证据页定位（与问答页同一交互）
  $$('#ck-table a.evidence-chip').forEach((a) => {
    a.addEventListener('click', () => {
      const idx = +a.dataset.idx;
      const ev = (DATA.evidence || [])[idx];
      if (!ev) return;
      $$('#tabs button').forEach((x) => x.classList.remove('active'));
      $$('.tab').forEach((x) => x.classList.remove('active'));
      const evTab = $('#tabs button[data-tab="evidence"]');
      if (evTab) evTab.classList.add('active');
      const sec = $('#tab-evidence');
      if (sec) sec.classList.add('active');
      showSource(ev);
      $$('#ev-table tbody tr').forEach((tr) => {
        tr.classList.toggle('selected', +tr.dataset.idx === idx);
      });
    });
  });
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
  window.renderExecutionTrace($('#trace-view'), DATA.trace || [], id => {
    const ev = [...(DATA.evidence || []), ...(DATA.announcements || []).flatMap(a => a.evidence || [])].find(e => e.evidence_id === id);
    if (!ev) return;
    $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === 'evidence'));
    $$('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-evidence'));
    showSource(ev);
  });
}

function renderAnnouncements() {
  const labels = window.announcementFieldLabels;
  $('#announcement-view').innerHTML = (DATA.announcements || []).map(bundle => `<h3>${esc(bundle.sample_name || bundle.event_type)} ${bundle.simulated ? '· 模拟材料' : '· 真实公开披露'}</h3>${(bundle.events || []).map(e => `<details open><summary>${esc(e.event_name)} · ${esc(window.announcementStatusLabels[e.status] || e.status)}</summary><p>文件指纹：${esc(e.source_sha256)}<br>必填缺失：${esc(e.missing_required.map(k=>labels[k] || k).join('、') || '无')}；冲突：${esc(e.conflicts.map(k=>labels[k] || k).join('、') || '无')}</p><table class="grid"><thead><tr><th>字段</th><th>原值</th><th>规范值</th><th>状态</th><th>来源页</th></tr></thead><tbody>${Object.entries(e.fields).map(([k,v]) => `<tr><td>${esc(labels[k] || k)}</td><td>${esc(v.value ?? '缺失')}</td><td>${esc(v.normalized_value ?? '—')} ${esc(v.normalized_unit || '')}</td><td>${esc(window.announcementStatusLabels[v.status] || v.status)}</td><td>${v.evidence.map((x,i)=>`<button class="announcement-source" data-id="${esc(e.event_id+'_'+k+'_'+i)}">PDF 页序 ${esc(x.page)}</button>`).join(' ')}</td></tr>`).join('')}</tbody></table><details><summary>结构化字段与位置</summary><pre>${esc(JSON.stringify(e.fields, null, 2))}</pre></details></details>`).join('')}`).join('') || '<p>尚未打包公告样例；运行样例构建与 site_build 后可展示。</p>';
  $$('.announcement-source').forEach(button => button.addEventListener('click', () => {
    const ev = (DATA.announcements || []).flatMap(a => a.evidence || []).find(e => e.evidence_id === button.dataset.id);
    if (!ev) return;
    $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === 'evidence'));
    $$('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-evidence'));
    showSource(ev);
  }));
}

/* ---------- 受限问答（静态确定性，不调模型） ---------- */
/* 单一事实源：与 agent/extract.py METRICS 同步（14 个），[0]=展示名 */
const METRICS = {
  total_revenue: ['营业总收入', '营业总收入'],
  revenue: ['营业收入', '营业收入', '营收'],
  parent_net_profit: ['归母净利润', '归属于上市公司股东的净利润', '归属于母公司股东的净利润',
    '归属于母公司所有者的净利润', '归属于本行股东的净利润', '归属于本公司股东的净利润',
    '归母净利润', '归母净利'],
  adjusted_parent_net_profit: ['扣非归母净利润', '归属于上市公司股东的扣除非经常性损益的净利润',
    '归属于母公司股东的扣除非经常性损益的净利润', '归属于母公司所有者的扣除非经常性损益的净利润',
    '扣除非经常性损益后的归属于上市公司股东的净利润', '扣除非经常性损益后归属于上市公司股东的净利润',
    '扣非归母净利润', '扣非归母净利', '扣非净利润', '扣非净利'],
  operating_cash_flow: ['经营现金流净额', '经营活动产生的现金流量净额', '经营现金流净额', '经营活动现金流量净额'],
  basic_eps: ['基本每股收益', '基本每股收益', '每股收益'],
  diluted_eps: ['稀释每股收益', '稀释每股收益'],
  deducted_basic_eps: ['扣非基本每股收益', '扣除非经常性损益后的基本每股收益', '扣非基本每股收益', '扣非每股收益', '扣非EPS'],
  weighted_roe: ['加权平均净资产收益率', '加权平均净资产收益率', '净资产收益率（加权平均）', '净资产收益率(加权平均)',
    '加权ROE', '净资产收益率'],
  deducted_weighted_roe: ['扣非加权平均净资产收益率', '扣除非经常性损益后的加权平均净资产收益率',
    '扣非加权平均净资产收益率', '扣非加权ROE', '扣非ROE'],
  total_assets: ['总资产', '总资产', '资产总额', '资产总计'],
  parent_equity: ['归母净资产', '归属于上市公司股东的净资产', '归属于母公司股东的净资产',
    '归属于母公司所有者权益', '归属于上市公司股东的所有者权益',
    '归母净资产', '归母权益'],
  book_value_per_share: ['每股净资产', '归属于上市公司股东的每股净资产', '归属于母公司股东的每股净资产',
    '归属于上市公司普通股股东的每股净资产', '每股净资产'],
  revenue_after_deduction: ['营业收入扣除后金额', '营业收入扣除后金额', '扣除后营业收入'],
};
const YOY_CUE = /同比|增长|增幅|降幅|变化|yoy|回落|上升|下降/i;
const ISSUE_CUE = /问题|异常|issue|错误|风险|瑕疵|待复核|不一致/i;
const OVERVIEW_CUE = /哪些|有什么|列表|概览|总览|一览|整体|全部指标|分析结果/;

function qaMatchMetric(q) {
  let best = null, bestLen = 0;
  for (const [key, aliases] of Object.entries(METRICS)) {
    for (const alias of [key, ...aliases]) {
      if (alias && q.includes(alias) && alias.length > bestLen) {
        best = key; bestLen = alias.length;
      }
    }
  }
  return best;
}
function qaMatchCompany(q) {
  const ev = DATA.evidence || [];
  const seen = new Map();
  ev.forEach((e) => {
    if (e.company_code) seen.set(e.company_code, e.company_name || e.company_code);
  });
  for (const [code, name] of seen) {
    if (q.includes(code) || (name && q.includes(name))) return code;
  }
  // 公司简称（去掉「股份有限公司」等后缀后的短名）
  for (const [code, name] of seen) {
    const short = String(name || '').replace(/(股份有限公司|集团|控股)$/, '');
    if (short && short.length >= 2 && q.includes(short)) return code;
  }
  return null;
}
function qaMatchYear(q, facts) {
  const m = q.match(/(20\d{2})/);
  if (m) return +m[1];
  const years = (facts || []).map((f) => f.period_year).filter(Boolean);
  return years.length ? Math.max(...years) : null;
}
function qaMatchKind(q) {
  if (YOY_CUE.test(q)) return 'yoy';
  if (ISSUE_CUE.test(q)) return 'issues';
  if (OVERVIEW_CUE.test(q) && !/多少|是啥|是多少|几多/.test(q)) return 'overview';
  return 'value';
}
function qaPeriod(q, year) {
  let month = null, start = 1;
  if (/半年|半年度|上半年/.test(q)) month = 6;
  else if (/前三季|前[三3]季度|年初至.*三季/.test(q)) month = 9;
  else {
    const m = q.match(/第?([一二三四1234])季度|Q([1-4])/i);
    if (m) {
      const quarter = m[2] ? +m[2] : ({一:1, 二:2, 三:3, 四:4})[m[1]] || +m[1];
      month = quarter * 3; start = quarter * 3 - 2;
    } else if (/全年|年度报告|年报/.test(q)) month = 12;
  }
  return month ? {start: `${String(start).padStart(2, '0')}-01`, end: `${String(month).padStart(2, '0')}-${month === 3 || month === 12 ? '31' : '30'}`} : null;
}
function qaPeriodLabel(e) {
  return e.period_start && e.period_end ? `${e.period_start} 至 ${e.period_end}` : e.period_end || `${e.period_year} 年`;
}
function qaCite(e) {
  return {
    evidence_id: e.evidence_id,
    metric_name: e.metric_name || e.metric,
    period_year: e.period_year,
    period_label: qaPeriodLabel(e),
    value: e.value,
    unit: e.unit,
    page: e.page,
    company_name: e.company_name || e.company_code,
    idx: (DATA.evidence || []).indexOf(e),
  };
}
function qaFmtPct(v) {
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}
function qaScore(status) {
  const map = {
    ok: ['ok', '已答'],
    insufficient_evidence: ['info', '证据不足'],
    out_of_scope: ['warn', '超范围'],
  };
  const [cls, text] = map[status] || ['info', status || '—'];
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

function qaAnswer(question) {
  const q = (question || '').trim();
  if (!q) return { status: 'out_of_scope', answer: '问题为空。', evidence_ids: [], citations: [] };
  const all = (DATA.evidence || []).filter((e) => e.adjustment !== 'before');
  if (!all.length) return { status: 'insufficient_evidence', answer: '本页未加载证据。', evidence_ids: [], citations: [] };

  const metric = qaMatchMetric(q);
  const company = qaMatchCompany(q);
  const kind = qaMatchKind(q);
  const companyScoped = company ? all.filter((e) => e.company_code === company) : all;
  const year = qaMatchYear(q, companyScoped);
  const period = qaPeriod(q, year);
  const scoped = companyScoped.filter(e => !period ||
    (String(e.period_end || '').slice(5) === period.end &&
     (e.period_kind === 'instant' || String(e.period_start || '').slice(5) === period.start)));

  if (kind === 'overview') {
    const picked = new Map();
    scoped.forEach((e) => {
      const key = [e.company_code, e.metric, e.period_start, e.period_end, e.scope, e.document_id].join('|');
      const cur = picked.get(key);
      if (!cur || (e.period_year || 0) > (cur.period_year || 0)) picked.set(key, e);
    });
    const cites = [...picked.values()].map(qaCite);
    if (!cites.length) return { status: 'insufficient_evidence', answer: '无可汇总证据。', evidence_ids: [], citations: [] };
    const lines = cites.map((c) =>
      `· ${c.company_name} ${c.metric_name}（${c.period_label}）：${c.value} ${c.unit || ''}`);
    return {
      status: 'ok',
      answer: `指标概览（共 ${all.length} 条证据）：\n` + lines.join('\n'),
      evidence_ids: cites.map((c) => c.evidence_id),
      citations: cites,
    };
  }

  if (kind === 'issues') {
    const flagged = scoped.filter((e) => e.issues && e.issues.length);
    if (!flagged.length) {
      return {
        status: 'ok',
        answer: '本页证据未带问题标记（issues 为空），不代表业务无风险，仅表示抽取层干净。',
        evidence_ids: [], citations: [],
      };
    }
    const cites = flagged.slice(0, 20).map(qaCite);
    const lines = flagged.slice(0, 20).map((e) =>
      `· ${esc(e.company_name || e.company_code)} ${esc(e.metric_name)}（${esc(e.period_year)}）：${esc((e.issues || []).join('；'))}`);
    return {
      status: 'ok',
      answer: `抽取层标记 ${flagged.length} 条待复核：\n` + lines.join('\n'),
      evidence_ids: cites.map((c) => c.evidence_id),
      citations: cites,
    };
  }

  if (!metric) {
    return {
      status: 'out_of_scope',
      answer: '未识别出指标，本问答只答已抽取的财务指标（营收/净利/现金流等）。',
      evidence_ids: [], citations: [],
    };
  }
  const metricName = METRICS[metric][0];
  const matches = scoped.filter((e) => e.metric === metric && e.period_year === year && e.report_year === year);
  if (!matches.length) {
    return {
      status: 'insufficient_evidence',
      answer: `证据不足：未找到 ${company ? (company + ' ') : ''}${year} 年「${metricName}」的对应期间证据。`,
      evidence_ids: [], citations: [],
    };
  }
  if (matches.length > 1) {
    const cites = matches.map(qaCite);
    return {
      status: 'insufficient_evidence',
      answer: `证据不足：${year} 年「${metricName}」命中 ${matches.length} 条，需明确全年、半年或季度及公司、口径。`,
      evidence_ids: cites.map((c) => c.evidence_id), citations: cites,
    };
  }

  const fact = matches[0];
  const cite = qaCite(fact);
  if (fact.issues?.length) return {status: 'insufficient_evidence', answer: '证据含待复核标记，不能直接作为确定数值作答。', evidence_ids: [fact.evidence_id], citations: [cite]};

  if (kind === 'yoy') {
    const row = (DATA.analysis?.rows || []).find(r => r.evidence_id === fact.evidence_id);
    const computation = row?.yoy;
    const cites = (computation?.evidence_ids || [fact.evidence_id]).map(id => (DATA.evidence || []).find(e => e.evidence_id === id)).filter(Boolean).map(qaCite);
    if (computation?.status !== 'ok') return {status: 'insufficient_evidence', answer: `证据不足：${qaPeriodLabel(fact)}「${metricName}」同比无法复算（${computation?.reason || computation?.status || '缺少可比期间'}）。`, evidence_ids: cites.map(c => c.evidence_id), citations: cites};
    return {
      status: 'ok',
      answer: `${fact.company_name || fact.company_code} ${qaPeriodLabel(fact)} ${metricName}同比 ${qaFmtPct(computation.value)}%。计算式：${computation.formula}。`,
      evidence_ids: cites.map(c => c.evidence_id), citations: cites,
    };
  }

  // value
  const scopeTxt = { consolidated: '合并', parent_shareholders: '归母', parent_company: '母公司' }[fact.scope] || (fact.scope && fact.scope !== 'unknown' ? fact.scope : '—');
  return {
    status: 'ok',
    answer: `${fact.company_name || fact.company_code} ${qaPeriodLabel(fact)} ${metricName}为 ${fact.value} ${fact.unit || ''}，口径：${scopeTxt}（PDF 第 ${fact.page} 页）。`,
    evidence_ids: [fact.evidence_id],
    citations: [cite],
  };
}

function qaRenderItem(item) {
  const cites = (item.citations || []).map((c) => {
    const label = `${esc(c.evidence_id)} · ${esc(c.company_name || '')} ${esc(c.metric_name || '')}${c.page ? ' · p' + esc(String(c.page)) : ''}`;
    return `<a class="evidence-chip" data-idx="${c.idx}">${label}</a>`;
  }).join('');
  return `<div class="qa-item">
    <div class="q">问：${esc(item.question)}</div>
    <div class="a">${esc(item.answer)}</div>
    <div class="meta">${qaScore(item.status)}${cites}</div>
  </div>`;
}

function qaAsk() {
  const input = $('#qa-input');
  const q = (input.value || '').trim();
  if (!q) return;
  const out = qaAnswer(q);
  const item = { question: q, ...out };
  $('#qa-history').insertAdjacentHTML('afterbegin', qaRenderItem(item));
  $('#qa-empty').style.display = 'none';
  input.value = '';
  // 引用芯片 → 跳证据页并定位
  $$('#qa-history a.evidence-chip').forEach((a) => {
    a.addEventListener('click', () => {
      const idx = +a.dataset.idx;
      const ev = (DATA.evidence || [])[idx];
      if (!ev) return;
      $$('#tabs button').forEach((x) => x.classList.remove('active'));
      $$('.tab').forEach((x) => x.classList.remove('active'));
      const evTab = $('#tabs button[data-tab="evidence"]');
      if (evTab) evTab.classList.add('active');
      const sec = $('#tab-evidence');
      if (sec) sec.classList.add('active');
      showSource(ev);
      // 同步高亮证据表
      $$('#ev-table tbody tr').forEach((tr) => {
        tr.classList.toggle('selected', +tr.dataset.idx === idx);
      });
    });
  });
}

/* ---------- 启动 ---------- */
$('#mat-filter').addEventListener('input', renderMaterials);
$('#ck-filter').addEventListener('change', renderChecks);
$('#ev2-filter').addEventListener('input', renderEvents);
$('#qa-ask').addEventListener('click', qaAsk);
$('#qa-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); qaAsk(); }
});
document.addEventListener('click', (e) => {
  const s = e.target.closest('.qa-sample');
  if (!s) return;
  e.preventDefault();
  $('#qa-input').value = s.textContent.trim();
  qaAsk();
});

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
    renderAnnouncements();
    const n = (DATA.evidence || []).length;
    const cnt = $('#qa-ev-count');
    if (cnt) cnt.textContent = n;
  })
  .catch((e) => {
    document.querySelector('main').innerHTML =
      `<p class="load-error">数据加载失败：${esc(e.message)}</p>`;
  });

fetch('data/question_eval.json')
  .then((r) => r.ok ? r.json() : Promise.reject(new Error(r.status)))
  .then((ev) => {
    const root = $('#eval-view');
    if (!root) return;
    const pass = ev.passed ?? 0;
    const scored = ev.scored ?? 0;
    const rate = ev.pass_rate != null ? (ev.pass_rate * 100).toFixed(1) + '%' : '—';
    const by = ev.by_type || {};
    const typeRows = Object.entries(by).map(([t, b]) =>
      `<tr><td>${esc(t)}</td><td>${b.pass}</td><td>${b.fail}</td></tr>`).join('');
    const failed = (ev.failed_ids || []).map((id) => `<li><code>${esc(id)}</code></li>`).join('');
    const llm = ev.llm_draft;
    const llmHtml = llm
      ? `<h3>LLM 草稿核查</h3><p>
          skipped=${esc(String(llm.skipped))} · mode=<code>${esc(llm.mode || '—')}</code>
          · tools=${esc(String(llm.tools_used ?? 0))} · ok=${esc(String(llm.ok))}<br>
          counts：<code>${esc(JSON.stringify(llm.counts || {}))}</code>
        </p>`
      : '';
    root.innerHTML = `
      <div class="overview" style="margin:12px 0">
        <div class="stat-card ok"><div class="n">${rate}</div><div class="k">通过率</div></div>
        <div class="stat-card"><div class="n">${pass} / ${scored}</div><div class="k">通过 / 计分</div></div>
        <div class="stat-card warn"><div class="n">${ev.skipped ?? 0}</div><div class="k">跳过</div></div>
      </div>
      <p class="muted">run_id：<code>${esc(ev.run_id || '—')}</code> · ${esc(ev.note || '')}</p>
      <h3>分类型</h3>
      <table class="grid"><thead><tr><th>类型</th><th>通过</th><th>失败</th></tr></thead>
      <tbody>${typeRows}</tbody></table>
      ${failed ? `<h3>失败用例</h3><ul>${failed}</ul>` : '<p class="muted">无失败用例。</p>'}
      ${llmHtml}`;
  })
  .catch(() => {
    const root = $('#eval-view');
    if (root) root.innerHTML = '<p class="muted">尚未打包评测报告。先运行 <code>python scripts/eval/run_question_eval.py</code> 并重新 site_build。</p>';
  });
