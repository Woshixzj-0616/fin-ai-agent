const $ = (selector) => document.querySelector(selector)
const api = async (path, options = {}) => {
  const response = await fetch(path, options)
  const type = response.headers.get('content-type') || ''
  const body = type.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) throw new Error(body?.detail || body || ('请求失败（HTTP ' + response.status + '）'))
  return body
}

let selectedRunId = ''
let selectedRun = null
let pollTimer = null
let toastTimer = null
const statusNames = {
  queued: '排队中', running: '分析中', completed: '运行结束 · 分析完整',
  partial: '部分完成', insufficient_data: '资料不足', failed: '运行失败',
  analyzed: '已分析', needs_review: '待复核', insufficient_evidence: '证据不足',
  not_applicable: '不适用', pending: '等待中',
}
const sectionLabels = {
  cash_overview: '现金全貌', profit_to_cash: '利润到经营现金',
  operating_cash: '经营现金收付', investment_cash: '投资现金',
  financing_cash: '筹资与现金余额',
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char])
}
function arr(value) { return Array.isArray(value) ? value : [] }
function mapById(value, idKey) {
  if (Array.isArray(value)) return new Map(value.filter((x) => x && x[idKey]).map((x) => [x[idKey], x]))
  if (value && typeof value === 'object') return new Map(Object.entries(value))
  return new Map()
}
function statusText(status) { return statusNames[status] || status || '未标注' }
function dateText(value) {
  if (!value) return '时间未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
function numberText(value, unit = '') {
  if (value === null || value === undefined || value === '') return '未披露'
  const number = Number(value)
  if (!Number.isFinite(number)) return String(value) + (unit ? ' ' + unit : '')
  if (unit === 'ratio') return (number * 100).toFixed(1) + '%'
  if (unit === 'CNY_yuan') return number.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' 元'
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 6 }) + (unit ? ' ' + unit : '')
}
function jsonBlock(value) {
  if (value === null || value === undefined || value === '') return '未披露'
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}
function showToast(message) {
  const toast = $('#toast')
  toast.textContent = message
  toast.classList.add('visible')
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3600)
}

async function loadSettings(showFirstRun = false) {
  try {
    const settings = await api('/api/settings')
    const status = $('#model-status')
    status.className = 'model-status ' + (settings.configured ? 'connected' : 'disconnected')
    status.textContent = settings.configured ? 'DeepSeek 已配置 · ' + (settings.model || 'deepseek-flash') : 'DeepSeek 未配置'
    $('#model-name').value = settings.model || 'deepseek-flash'
    $('#base-url').value = settings.base_url || 'https://api.deepseek.com'
    if (showFirstRun && !settings.configured) $('#settings-dialog').showModal()
  } catch (error) {
    $('#model-status').textContent = '模型配置读取失败'
    showToast(error.message)
  }
}

async function loadHistory(preferRunId = '') {
  const data = await api('/api/history')
  const items = arr(data.items)
  $('#history-count').textContent = items.length + ' 条'
  const list = $('#history-list')
  list.innerHTML = items.length ? items.map((item) => {
    const selected = item.id === (preferRunId || selectedRunId)
    const statusClass = esc(item.status || '')
    const identity = [item.company, item.report_year].filter(Boolean).join(' · ')
    return '<button class="history-item ' + (selected ? 'selected' : '') + '" data-run-id="' + esc(item.id) + '">' +
      '<b>' + esc(item.file_name || '未命名年报') + '</b>' +
      '<span>' + esc(identity || item.source_kind || '模块四分析') + ' · ' + esc(dateText(item.created_at)) + '</span>' +
      '<small class="status-pill ' + statusClass + '">' + esc(statusText(item.status)) + '</small></button>'
  }).join('') : '<div class="empty-mini">还没有分析记录</div>'
  list.querySelectorAll('[data-run-id]').forEach((button) => {
    button.addEventListener('click', () => selectRun(button.dataset.runId))
  })
  if (!selectedRunId && items.length) await selectRun(items[0].id)
}

async function selectRun(runId) {
  selectedRunId = runId
  const run = await api('/api/runs/' + encodeURIComponent(runId))
  renderRun(run)
  await loadHistory(runId)
  schedulePoll()
}

function renderRun(run) {
  selectedRun = run
  $('#empty-state').hidden = true
  $('#run-view').hidden = false
  $('#run-title').textContent = run.file_name || '模块四分析记录'
  const sha = run.sha256 ? 'SHA-256 ' + run.sha256.slice(0, 12) + '…' : ''
  const description = [run.company, run.report_year, (run.page_count || 0) + ' 页', sha].filter(Boolean).join(' · ')
  $('#run-subtitle').textContent = description
  $('#run-source').textContent = run.source_kind || '本次上传'
  const status = $('#run-status')
  status.textContent = statusText(run.status)
  status.className = 'status-pill ' + (run.status || '')
  const pdfLink = $('#open-pdf')
  pdfLink.hidden = !run.pdf_available
  pdfLink.href = '/api/runs/' + encodeURIComponent(run.id) + '/file'
  renderProgress(run)
  renderResults(run)
  renderFollowups(arr(run.followups))
  const canFollow = Boolean(run.result) && !['queued', 'running'].includes(run.status)
  $('#followup-input').disabled = !canFollow
  $('#followup-submit').disabled = !canFollow
  $('#history-list').querySelectorAll('[data-run-id]').forEach((button) => {
    button.classList.toggle('selected', button.dataset.runId === run.id)
  })
}

function renderProgress(run) {
  const steps = arr(run.steps)
  const running = ['queued', 'running'].includes(run.status)
  $('#progress-label').textContent = run.stage || statusText(run.status)
  const bar = $('#progress-bar')
  bar.className = running ? 'running' : (run.status === 'completed' ? 'done' : '')
  if (run.status === 'completed') bar.classList.add('done')
  $('#progress-steps').innerHTML = steps.length ? steps.slice(-12).map((step) => {
    const state = step.status || ''
    const cls = state.includes('失败') || state.includes('未通过') ? 'error' : (state.includes('部分') || state.includes('待复核') ? 'partial' : '')
    return '<div class="progress-step ' + cls + '"><b>' + esc(step.stage) + ' · ' + esc(state) + '</b><span>' + esc(step.detail || '') + '</span></div>'
  }).join('') : '<div class="empty-mini">等待任务进度</div>'
}

function renderResults(run) {
  const result = run.result
  if (!result) {
    $('#result-summary').innerHTML = '<div class="validation-item">' + esc(run.error || '本次运行还没有保存结构化结果。') + '</div>'
    $('#five-sections').innerHTML = ''
    $('#yoy-bridges').innerHTML = '<div class="empty-subresult">分析结果形成后显示同比贡献桥。</div>'
    $('#reconciliations').innerHTML = '<div class="empty-subresult">分析结果形成后显示程序勾稽。</div>'
    $('#topics').innerHTML = '<div class="empty-subresult">当前没有可展示的专题结果。</div>'
    $('#review-content').innerHTML = '<div class="validation-item">分析没有形成结果。请结合上方失败原因处理后重新上传。</div>'
    return
  }
  const facts = mapById(result.facts, 'fact_id')
  const calculations = mapById(result.calculations, 'calculation_id')
  const sections = arr(result.sections)
  const flags = arr(result.review_flags)
  let completeText = '请逐个查看五个板块的结果状态、引用事实和相关计算。'
  if (result.analysis_completeness === 'partial') completeText = '运行已结束，但结果为部分完成；请查看未完成板块和提交校验原因。'
  if (result.analysis_completeness === 'complete_with_review_flags') completeText = '五个板块已形成结果，仍有 ' + flags.length + ' 条复核提示；专业判断和证据边界请分开阅读。'
  const summaryChips = [
    sections.length + ' 个板块', arr(result.facts).length + ' 条事实',
    arr(result.calculations).length + ' 项计算', arr(run.read_pages).length + ' 个已读页码',
  ]
  $('#result-summary').innerHTML =
    '<div class="summary-head"><div><div class="run-kicker">模块四结构化结果 · ' + esc(result.module_version || 'cashflow') + '</div>' +
    '<h3>' + esc(result.company || '公司待识别') + ' · ' + esc(result.report_year || '报告年度待识别') + '</h3></div>' +
    '<div class="summary-meta">' + summaryChips.map((x) => '<span class="meta-chip">' + esc(x) + '</span>').join('') + '</div></div>' +
    '<p class="summary-body">' + esc(result.overall_view || '没有提交整体判断。') + '</p>' +
    '<div class="summary-caveat">' + esc(completeText) + (run.error ? ' 运行说明：' + esc(run.error) : '') + '</div>'

  const ordered = ['cash_overview', 'profit_to_cash', 'operating_cash', 'investment_cash', 'financing_cash']
  const byKey = new Map(sections.map((section) => [section.key, section]))
  $('#five-sections').innerHTML = ordered.map((key, index) => {
    const section = byKey.get(key)
    if (!section) return '<article class="panel analysis-section"><div class="section-heading"><span class="section-number">0' + (index + 1) +
      '</span><div><small>模块四分析板块</small><h3>' + esc(sectionLabels[key]) +
      '</h3></div><span class="section-status insufficient_evidence">未提交</span></div><p class="empty-subresult">本次结果未提交此板块，不能视作已完成分析。</p></article>'
    const factIds = arr(section.fact_ids)
    const calculationIds = arr(section.calculation_ids)
    const sectionCalcs = [...new Set(calculationIds)].map((id) => calculations.get(id)).filter(Boolean)
    const evidenceFactIds = [...new Set([
      ...factIds,
      ...sectionCalcs.flatMap((calc) => arr(calc.fact_ids)),
    ])]
    const sectionFacts = evidenceFactIds.map((id) => facts.get(id)).filter(Boolean)
    const tableHtml = arr(section.tables).length
      ? '<details class="data-details"><summary>模型提交的专题表格（' + section.tables.length + ' 张）</summary><pre class="page-text">' + esc(JSON.stringify(section.tables, null, 2)) + '</pre></details>'
      : ''
    const missingCalcs = calculationIds.filter((id) => !calculations.has(id))
    return '<article class="panel analysis-section" id="section-' + esc(key) + '">' +
      '<div class="section-headline"><div class="section-heading"><span class="section-number">0' + (index + 1) +
      '</span><div><small>模块四专业分析</small><h3>' + esc(section.title || sectionLabels[key]) + '</h3></div></div>' +
      '<span class="section-status ' + esc(section.status || '') + '">' + esc(statusText(section.status)) + '</span></div>' +
      '<p class="section-summary">' + esc(section.summary || '本板块未提供摘要。') + '</p>' +
      '<p class="section-detail">' + esc(section.detail || '未提交详细分析内容。') + '</p>' +
      '<div class="section-refline"><span class="meta-chip">引用事实 ' + factIds.length + ' 条</span><span class="meta-chip">关联计算 ' +
      calculationIds.length + ' 项</span><span class="meta-chip">原文页 ' + esc(arr(section.source_pages).join('、') || '未提供') + '</span></div>' +
      tableHtml + '<details class="data-details"><summary>事实、计算与原文依据（' + sectionFacts.length + ' 条事实 · ' +
      sectionCalcs.length + ' 项计算）</summary>' +
      (sectionFacts.length ? '<div class="fact-list">' + sectionFacts.map(renderFact).join('') + '</div>' :
        '<div class="empty-subresult">此板块没有关联事实编号；金额或专业结论不能由程序确认。</div>') +
      (sectionCalcs.length ? '<div class="calc-list">' + sectionCalcs.map(renderCalculation).join('') + '</div>' : '') +
      (evidenceFactIds.filter((id) => !facts.has(id)).length ? '<div class="validation-item">存在未能映射到本次结果的事实编号：' + esc(evidenceFactIds.filter((id) => !facts.has(id)).join('、')) + '</div>' : '') +
      (missingCalcs.length ? '<div class="validation-item">存在未能映射到本次结果的计算编号：' + esc(missingCalcs.join('、')) + '</div>' : '') +
      '</details></article>'
  }).join('')

  const bridges = arr(result.calculations).filter((calc) => calc.operation === 'yoy_bridge')
  $('#yoy-bridges').innerHTML = bridges.length
    ? bridges.map((calc) => renderBridge(calc, facts)).join('')
    : '<div class="empty-subresult">本次结果没有提交可展示的同比贡献桥。请勿把其他同比指标误当成完整贡献桥。</div>'
  const reconciliationOps = new Set(['cash_bridge', 'cash_balance_bridge', 'profit_to_cash'])
  const reconciliations = arr(result.calculations).filter((calc) => reconciliationOps.has(calc.operation))
  $('#reconciliations').innerHTML = reconciliations.length
    ? reconciliations.map(renderReconciliation).join('')
    : '<div class="empty-subresult">本次结果没有提交主表、期初期末余额或利润到经营现金的勾稽。</div>'
  $('#topics').innerHTML = arr(result.topics).length
    ? arr(result.topics).map(renderTopic).join('')
    : '<div class="empty-subresult">模型没有提交独立专题观察；这里不从其他段落自动拼造专题结论。</div>'
  renderReview(run, result, flags)
  $('#five-sections').querySelectorAll('[data-page]').forEach((button) => {
    button.addEventListener('click', () => openPage(Number(button.dataset.page)))
  })
}

function renderFact(fact) {
  const evidence = arr(fact.evidence)
  const value = fact.normalized_value ?? fact.value
  const unit = fact.normalized_value !== undefined ? fact.normalized_unit : fact.unit
  const identity = evidence.some((item) => item.row_column_identity && item.row_column_identity !== 'verified')
    ? '摘录数值匹配不等于科目、期间和范围身份已证实'
    : '仅表示当前保存的来源核对状态'
  return '<div class="fact-card"><div class="fact-title">' + esc(fact.original_label || fact.label || fact.metric_key || '未命名事实') + '</div>' +
    '<div class="fact-value">' + esc(numberText(value, unit)) + '</div>' +
    '<div class="fact-sub">' + esc(fact.period || '期间未标注') + ' · ' + esc(fact.scope || '范围未标注') + ' · ' + esc(fact.source_table || '来源表未标注') + '</div>' +
    '<div class="fact-sub">证据状态：' + esc(fact.evidence_status || '未标注') + '；' + esc(identity) + '</div>' +
    evidence.map((item) => '<div class="fact-evidence"><b>原文摘录：</b><div class="quote">' + esc(item.quote || '无摘录文本') + '</div><div>' +
      esc(item.table_label || fact.source_table || '') + ' · ' + esc(item.row_label || '') + ' · ' + esc(item.column_label || '') + '</div>' +
      '<button class="page-link" data-page="' + (Number(item.page) || 0) + '">回查 PDF 第 ' + esc(item.page ?? '?') + ' 页</button></div>').join('') +
    '</div>'
}

function renderCalculation(calc) {
  const detailEntries = calc.details && typeof calc.details === 'object' ? Object.entries(calc.details) : []
  return '<div class="calc-card"><div class="calc-title">' + esc(calc.name || calc.operation || '未命名计算') + '</div>' +
    '<div class="calc-main">' + esc(numberText(calc.value, calc.unit)) + '</div><div class="calc-formula">' + esc(calc.formula || calc.operation || '') + '</div>' +
    (detailEntries.length ? '<div class="calc-details">' + detailEntries.map(([key, value]) =>
      '<div><b>' + esc(key) + '：</b>' + esc(jsonBlock(value)) + '</div>').join('') + '</div>' : '') +
    '<div class="fact-id-list">事实编号：' + esc(arr(calc.fact_ids).join('、') || '无') + '</div></div>'
}

function renderBridge(calc, facts) {
  const details = calc.details || {}
  const components = arr(details.components)
  const table = components.length
    ? '<table class="bridge-table"><thead><tr><th>调节项目</th><th>本期</th><th>上期</th><th>同比贡献</th></tr></thead><tbody>' +
      components.map((item) => '<tr><td>' + esc(item.label || item.metric_key || '未命名项目') +
        '<div class="fact-id-list">' + esc(arr(item.fact_ids).map((id) => facts.get(id)?.original_label || id).join(' · ')) + '</div></td>' +
        '<td>' + esc(numberText(item.current_value, 'CNY_yuan')) + '</td><td>' + esc(numberText(item.prior_value, 'CNY_yuan')) +
        '</td><td>' + esc(numberText(item.change, 'CNY_yuan')) + '</td></tr>').join('') + '</tbody></table>'
    : '<div class="empty-subresult">结果没有提交分项贡献明细。</div>'
  const reconciles = details.reconciles
  const check = reconciles === true ? '分项贡献之和与目标同比变化一致'
    : reconciles === false ? '分项贡献与目标同比变化不一致，需复核' : '当前结果未给出自动勾稽状态'
  return '<div class="bridge-card"><div class="bridge-header"><span>' + esc(calc.name || '同比贡献桥') +
    '</span><span class="bridge-value">' + esc(numberText(calc.value, calc.unit)) + '</span></div>' +
    '<div class="recon-detail">目标本期：' + esc(numberText(details.target_current, 'CNY_yuan')) + ' · 目标上期：' +
    esc(numberText(details.target_prior, 'CNY_yuan')) + ' · 分项合计：' + esc(numberText(details.component_change_sum, 'CNY_yuan')) + '</div>' +
    '<div class="recon-badge ' + (reconciles === false ? 'failed' : '') + '">' + esc(check) + '；差额 ' +
    esc(numberText(details.reconciliation_difference, 'CNY_yuan')) + '</div>' + table + '</div>'
}

function renderReconciliation(calc) {
  const details = calc.details || {}
  let checkText = '程序计算结果'
  let good = true
  if (typeof details.reconciles === 'boolean') {
    good = details.reconciles
    checkText = good ? '勾稽通过' : '未勾稽'
  } else if (details.cash_balance_change !== undefined && details.reported_net_change !== undefined) {
    good = Number(details.cash_balance_change) === Number(details.reported_net_change)
    checkText = good ? '期初期末余额与现金净增加一致' : '余额变动与披露净增加存在差异'
  } else if (calc.operation === 'cash_bridge') {
    good = Number(details.computed_net_change) === Number(details.reported_net_change)
    checkText = good ? '三类活动及汇率影响与现金净增加一致' : '现金流量桥存在差额'
  } else if (calc.operation === 'profit_to_cash') {
    good = Number(calc.value) === 0
    checkText = good ? '调节项目合计与经营现金净额一致' : '利润调节表与经营净现金存在差额'
  }
  return '<div class="recon-card"><div class="recon-header"><span>' + esc(calc.name || calc.operation || '勾稽计算') +
    '</span><span class="recon-value">' + esc(numberText(calc.value, calc.unit)) + '</span></div>' +
    '<div class="recon-detail">' + esc(calc.formula || '') + '</div><div class="recon-detail">' +
    Object.entries(details).map(([key, value]) => esc(key) + '：' + esc(jsonBlock(value))).join(' · ') + '</div>' +
    '<span class="recon-badge ' + (good ? '' : 'failed') + '">' + esc(checkText) + '。这是程序按已登记输入计算的结果。</span>' +
    '<div class="fact-id-list">' + esc(arr(calc.fact_ids).join('、')) + '</div></div>'
}

function renderTopic(topic) {
  if (typeof topic === 'string') return '<article class="topic-card"><p>' + esc(topic) + '</p></article>'
  const title = topic.title || topic.topic || topic.name || topic.label || '未命名专题'
  const body = topic.summary || topic.finding || topic.detail || topic.description || ''
  const excluded = ['title', 'topic', 'name', 'label', 'summary', 'finding', 'detail', 'description']
  const other = Object.entries(topic).filter(([key]) => !excluded.includes(key))
  return '<article class="topic-card"><h4>' + esc(title) + '</h4><p>' + esc(body || jsonBlock(topic)) + '</p>' +
    (other.length ? '<div class="topic-refs">' + other.map(([key, value]) => esc(key) + '：' + esc(jsonBlock(value))).join(' · ') + '</div>' : '') + '</article>'
}

function renderReview(run, result, flags) {
  const rejected = arr(result.rejected_amount_claims)
  const missingRefs = arr(result.missing_section_references)
  const errors = result.submission_errors && typeof result.submission_errors === 'object' ? result.submission_errors : null
  const checks = Number(result.validated_amount_claim_count || 0)
  let html = '<div class="review-counts"><div class="review-count"><b>' + checks + '</b><span>金额引用已核验</span></div>' +
    '<div class="review-count"><b>' + flags.length + '</b><span>待复核提示</span></div>' +
    '<div class="review-count"><b>' + arr(run.read_pages).length + '</b><span>本次已读页码</span></div></div>' +
    '<div class="validation-ok">证据文字/金额匹配只是引用校验的一部分，不自动证明科目归属、期间、合并范围或专业判断正确。空值保持空值，未披露数据不会按 0 参与计算。</div>'
  if (run.error) html += '<h4 class="review-subheading">运行或模型错误</h4><div class="validation-item">' + esc(run.error) + '</div>'
  if (rejected.length) {
    html += '<h4 class="review-subheading">未通过金额引用校验（' + rejected.length + ' 条）</h4>'
    html += rejected.map((item) => {
      const candidateFacts = arr(item.candidate_fact_ids)
      const candidateCalculations = arr(item.candidate_calculation_ids)
      const reason = item.reason || item.message || (
        candidateFacts.length || candidateCalculations.length
          ? '找到金额相同的候选来源，但科目、期间、范围或方向未能完整通过校验。'
          : '当前登记的事实和计算中没有金额一致的候选来源。'
      )
      return '<div class="validation-item"><b>' + esc(item.section || '未注明板块') + ' · ' +
        esc(item.amount || '未注明金额') + '</b><br>' + esc(reason) +
        '<br>候选事实：' + esc(candidateFacts.join('、') || '无') + ' · 候选计算：' +
        esc(candidateCalculations.join('、') || '无') + '</div>'
    }).join('')
  }
  if (missingRefs.length) {
    html += '<h4 class="review-subheading">缺失板块引用（' + missingRefs.length + ' 条）</h4>'
    html += missingRefs.map((item) => '<div class="validation-item">' + esc(jsonBlock(item)) + '</div>').join('')
  }
  if (errors) html += '<h4 class="review-subheading">提交未通过的具体原因</h4><div class="validation-item"><pre class="plain-pre">' +
    esc(JSON.stringify(errors, null, 2)) + '</pre></div>'
  if (flags.length) {
    html += '<h4 class="review-subheading">模块四复核提示（' + flags.length + ' 条）</h4>'
    html += flags.map((flag) => '<div class="review-item">' + esc(typeof flag === 'string' ? flag : jsonBlock(flag)) + '</div>').join('')
  } else if (!rejected.length && !missingRefs.length && !errors) {
    html += '<h4 class="review-subheading">复核提示</h4><div class="empty-subresult">本次结果没有单独提交复核提示；这不等于所有金额和专业判断已被独立审计。</div>'
  }
  $('#review-content').innerHTML = html
}

function renderFollowups(turns) {
  $('#followup-list').innerHTML = turns.length ? turns.map((turn) => '<article class="followup-item ' +
    (turn.status === 'failed' ? 'failed' : '') + '"><div class="followup-question">' + esc(turn.question || '') + '</div>' +
    (['running', 'queued'].includes(turn.status) ? '<div class="followup-answer">正在携带模块四结果检索并回答…</div>' : '') +
    (turn.answer ? '<div class="followup-answer">' + esc(turn.answer) + '</div>' : '') +
    (turn.error ? '<div class="followup-error">' + esc(turn.error) + '</div>' : '') +
    (arr(turn.read_pages).length ? '<div class="followup-meta">本轮回查年报物理页：' + esc(turn.read_pages.join('、')) + '</div>' : '') +
    '<div class="followup-meta">' + esc(dateText(turn.created_at)) + ' · ' + esc(statusText(turn.status)) + '</div></article>').join('') :
    '<div class="empty-subresult">提出问题后，回答和回查页码会保存在这次分析记录中。</div>'
}

async function openPage(pageNumber) {
  if (!selectedRunId || !pageNumber) return
  try {
    const page = await api('/api/runs/' + encodeURIComponent(selectedRunId) + '/pages/' + pageNumber)
    $('#page-title').textContent = 'PDF 第 ' + pageNumber + ' 页原文'
    $('#page-subtitle').textContent = page.file_name || selectedRun?.file_name || ''
    $('#page-text').textContent = page.text || '该页没有可提取文字。'
    const openPdf = $('#page-open-pdf')
    openPdf.hidden = !selectedRun?.pdf_available
    openPdf.href = '/api/runs/' + encodeURIComponent(selectedRunId) + '/file#page=' + pageNumber
    $('#page-dialog').showModal()
  } catch (error) { showToast(error.message) }
}

function schedulePoll() {
  clearTimeout(pollTimer)
  if (!selectedRunId || !selectedRun) return
  const activeRun = ['queued', 'running'].includes(selectedRun.status)
  const activeQuestion = arr(selectedRun.followups).some((item) => ['queued', 'running'].includes(item.status))
  if (!activeRun && !activeQuestion) return
  pollTimer = setTimeout(async () => {
    try {
      if (selectedRunId) {
        const latest = await api('/api/runs/' + encodeURIComponent(selectedRunId))
        renderRun(latest)
        await loadHistory(selectedRunId)
      }
    } catch (error) { showToast(error.message) }
    schedulePoll()
  }, 1400)
}

$('#file-input').addEventListener('change', (event) => {
  const file = event.target.files?.[0]
  $('#file-label').textContent = file?.name || '点击选择，或把 PDF 拖到这里'
  $('#file-meta').hidden = !file
  $('#file-meta').textContent = file ? (file.size / 1024 / 1024).toFixed(2) + ' MB · ' + (file.type || 'PDF') : ''
  $('#analyze-button').disabled = !file
  $('#upload-error').hidden = true
})
$('#dropzone').addEventListener('dragover', (event) => {
  event.preventDefault()
  $('#dropzone').classList.add('drag-over')
})
$('#dropzone').addEventListener('dragleave', () => $('#dropzone').classList.remove('drag-over'))
$('#dropzone').addEventListener('drop', (event) => {
  event.preventDefault()
  $('#dropzone').classList.remove('drag-over')
  const file = event.dataTransfer.files?.[0]
  if (!file) return
  const transfer = new DataTransfer()
  transfer.items.add(file)
  $('#file-input').files = transfer.files
  $('#file-input').dispatchEvent(new Event('change'))
})
$('#analyze-button').addEventListener('click', async () => {
  const file = $('#file-input').files?.[0]
  if (!file) return
  $('#analyze-button').disabled = true
  $('#analyze-button').textContent = '正在提取年报文字…'
  $('#upload-error').hidden = true
  try {
    const form = new FormData()
    form.append('file', file)
    const run = await api('/api/runs', { method: 'POST', body: form })
    selectedRunId = run.id
    renderRun(run)
    await loadHistory(run.id)
    schedulePoll()
    showToast('年报已保存，模块四分析已开始。')
  } catch (error) {
    $('#upload-error').textContent = error.message
    $('#upload-error').hidden = false
    showToast(error.message)
  } finally {
    $('#analyze-button').textContent = '开始现金流分析 ↗'
    $('#analyze-button').disabled = !$('#file-input').files?.[0]
  }
})

$('#open-settings').addEventListener('click', async () => {
  await loadSettings(false)
  $('#api-key').value = ''
  $('#settings-error').hidden = true
  $('#settings-dialog').showModal()
})
$('#save-settings').addEventListener('click', async () => {
  const button = $('#save-settings')
  button.disabled = true
  $('#settings-error').hidden = true
  try {
    await api('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: $('#api-key').value,
        model: $('#model-name').value.trim() || 'deepseek-flash',
        base_url: $('#base-url').value.trim() || 'https://api.deepseek.com',
      }),
    })
    $('#api-key').value = ''
    $('#settings-dialog').close()
    showToast('配置已保存在模块四任务目录。')
    await loadSettings(false)
  } catch (error) {
    $('#settings-error').textContent = error.message
    $('#settings-error').hidden = false
  } finally { button.disabled = false }
})
document.querySelectorAll('.dialog-close').forEach((button) => {
  button.type = 'button'
  button.addEventListener('click', () => button.closest('dialog').close())
})
$('#close-page-dialog').addEventListener('click', () => $('#page-dialog').close())

$('#followup-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  if (!selectedRunId) return
  const input = $('#followup-input')
  const question = input.value.trim()
  if (!question) return
  $('#followup-submit').disabled = true
  try {
    await api('/api/runs/' + encodeURIComponent(selectedRunId) + '/followups', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }),
    })
    input.value = ''
    const latest = await api('/api/runs/' + encodeURIComponent(selectedRunId))
    renderRun(latest)
    schedulePoll()
  } catch (error) { showToast(error.message) }
  finally { $('#followup-submit').disabled = false }
})

async function init() {
  try {
    await api('/api/health')
    await loadSettings(true)
    await loadHistory()
  } catch (error) { showToast(error.message) }
}
init()
