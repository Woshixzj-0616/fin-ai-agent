(() => {
  'use strict'

  const API = window.location.origin
  const resultPanel = document.getElementById('result-panel')
  const fileInput = document.getElementById('file-input')
  const dropZone = document.getElementById('drop-zone')
  const selectedFile = document.getElementById('selected-file')
  const startButton = document.getElementById('start-analysis')
  const keyStatus = document.getElementById('key-status')
  const historyList = document.getElementById('history-list')
  const historyCount = document.getElementById('history-count')
  const state = { health: null, runs: [], current: null, turns: [], file: null, tab: 'overview', timer: null, factQuery: '' }

  const labels = {
    summary: '资产质量总览', asset_map: '资产结构', overview: '整体概览', composition_2024: '本期结构', key_movement: '主要变动',
    receivables: '应收及资金占用', narrative: '分析说明', aging_2024: '账龄结构', provision: '坏账准备', concentration: '集中度',
    other_receivable_items: '其他应收项目', inventory: '存货', movement: '存货变动', production_sales: '产销情况', long_term_assets: '长期资产',
    fixed_assets: '固定资产', construction_in_progress: '在建工程', goodwill: '商誉', intangible_assets: '无形资产', efficiency: '经营效率',
    asset_intensity_estimate: '收入对应资产占用', notes: '口径说明', findings: '重点发现', conclusion: '判断', analysis: '分析过程', title: '标题',
    evidence_refs: '原文事实依据', calculation_refs: '计算依据', citation_audit: '引用核对', limitations: '限制与待核', handoff: '后续模块线索',
    facts: '登记事实', calculations: '程序计算', evidence: '原文摘录', evidence_notes: '事实核验说明', evidence_reference_notes: '引用核对说明',
    extraction_notes: '抽取备注', calculation_limitations: '计算限制', discovery: '页面定位记录', report: '报告身份', company: '公司',
    security_code: '证券代码', report_year: '报告年度', period_start: '期间起始', period_end: '期间结束', reporting_scope: '合并范围', currency: '币种',
    unit_context: '报告单位', module_id: '模块编号', module_version: '模块版本', analysis_status: '分析状态', extraction_status: '抽取状态',
    calculation_status: '计算状态', analysis_missing_fields: '缺失板块', analysis_shape_issues: '结构提示', quality_issues: '数据复核提醒',
    requested_new_fact_count: '追问新增事实数', usage: '模型用量', prompt_tokens: '输入 tokens', completion_tokens: '输出 tokens',
    fact_id: '事实编号', metric_key: '指标键', original_label: '年报原科目', dimension: '分类', value: '金额/数值', unit: '单位',
    period_type: '期间类型', as_of_date: '时点日期', measurement_basis: '计量基础', calculation_ready: '可参与计算', normalization_notes: '口径规范说明',
    validation: '核验状态', all_quotes_matched_page: '摘录文字可在页码定位', value_found_in_matched_quote: '数字出现在匹配摘录',
    original_label_found_in_matched_quote: '科目名出现在匹配摘录', unit_supported_by_cited_pages: '单位有页内依据', period_supported_by_cited_pages: '期间有页内依据',
    currency_supported_by_unit_or_currency_quote: '币种有依据', scope_supported_by_cited_pages: '合并范围有依据', value_status: '数字匹配状态',
    row_column_semantics_independently_verified: '表格行列语义已独立确认', quote: '摘录', role: '用途', page: 'PDF页码', quote_matched: '摘录文字匹配',
    calculation_id: '计算编号', name: '计算名称', formula: '公式', input_fact_ids: '输入事实编号', input_calculation_ids: '前置计算', inputs: '输入值',
    output: '计算结果', note: '说明', rule_version: '规则版本', period_label: '期间', measurement_basis: '总额/净额口径',
    status: '状态', resolved_fact_refs: '可关联事实数', resolved_calculation_refs: '可关联计算数', note: '说明',
    prompt_version: '提示词版本', page_count: 'PDF总页数', candidate_page_count: '初筛页数', search_log: '检索记录',
    read_pages: '模型读取页码', analysis_error: '分析错误', additional_facts: '追问补充事实', source_pages: '读取页码',
    created_at: '开始时间', completed_at: '完成时间', sha256: '文件摘要', adjustment_basis: '调整口径', report_id: '报告编号',
  }

  const tabItems = [
    ['overview', '总览'], ['asset_map', '资产结构'], ['receivables', '应收及资金占用'], ['inventory', '存货'],
    ['long_term_assets', '长期资产'], ['efficiency', '经营效率'], ['findings', '重点发现'], ['facts', '事实底表'],
    ['calculations', '程序计算'], ['evidence', '依据与限制'], ['handoff', '模块交接'], ['followup', '本模块追问'], ['run_info', '运行信息'],
  ]

  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]))
  const display = (key) => labels[key] || key.replaceAll('_', ' ')
  const clean = (value) => typeof value === 'string' ? value.trim() : value

  async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, options)
    let data = {}
    try { data = await response.json() } catch { /* A download or empty response. */ }
    if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`)
    return data
  }

  function toast(message) {
    let node = document.getElementById('toast')
    if (!node) {
      node = document.createElement('div')
      node.id = 'toast'
      node.className = 'toast'
      document.body.append(node)
    }
    node.textContent = message
    node.classList.add('visible')
    window.clearTimeout(node._timer)
    node._timer = window.setTimeout(() => node.classList.remove('visible'), 3400)
  }

  function statusLabel(value) {
    return ({
      pending: '待开始', complete: '分析完整', completed: '运行结束', partial: '部分完成',
      insufficient: '资料不足', needs_review: '待复核', failed: '失败', running: '运行中',
    })[value] || value || '状态待定'
  }

  function statusClass(value) {
    if (['failed'].includes(value)) return 'is-failed'
    if (['partial', 'needs_review', 'insufficient'].includes(value)) return `is-${value === 'needs_review' ? 'review' : value}`
    if (['pending', 'running'].includes(value)) return 'is-pending'
    return ''
  }

  function updateKeyStatus() {
    const ready = Boolean(state.health?.api_configured)
    keyStatus.classList.toggle('is-ready', ready)
    keyStatus.querySelector('span').textContent = ready ? `DeepSeek 已配置 · ${state.health?.model || ''}` : 'DeepSeek 未配置'
  }

  function openModal(id) { document.getElementById(id)?.classList.remove('hidden') }
  function closeModal(id) { document.getElementById(id)?.classList.add('hidden') }

  function selectFile(file) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.pdf')) { toast('请选择 PDF 年报。'); return }
    if (file.size > 25 * 1024 * 1024) { toast('文件超过 25 MB，请先压缩 PDF。'); return }
    state.file = file
    selectedFile.innerHTML = `<span class="pdf-icon">PDF</span><span title="${escape(file.name)}">${escape(file.name)} · ${(file.size / 1024 / 1024).toFixed(1)} MB</span>`
    selectedFile.classList.remove('hidden')
    startButton.disabled = false
  }

  async function startAnalysis() {
    if (!state.file) return
    if (!state.health?.api_configured) { openModal('key-modal'); toast('先配置 DeepSeek Key，再开始分析。'); return }
    startButton.disabled = true
    startButton.innerHTML = '正在提交… <span>↗</span>'
    try {
      const form = new FormData()
      form.append('file', state.file)
      const created = await request('/api/runs', { method: 'POST', body: form })
      state.file = null
      fileInput.value = ''
      selectedFile.classList.add('hidden')
      await reloadHistory()
      await openRun(created.id)
    } catch (error) {
      toast(error.message)
      startButton.disabled = false
    } finally {
      startButton.innerHTML = '开始资产分析 <span>↗</span>'
    }
  }

  async function reloadHistory() {
    try {
      const data = await request('/api/runs')
      state.runs = data.runs || []
      historyCount.textContent = `${state.runs.length} 条`
      renderHistory()
    } catch (error) {
      historyList.innerHTML = `<div class="history-empty">本机历史记录暂时无法读取：${escape(error.message)}</div>`
    }
  }

  function renderHistory() {
    if (!state.runs.length) {
      historyList.innerHTML = '<div class="history-empty">还没有分析记录。完成的结果会保存在这台电脑上。</div>'
      return
    }
    historyList.innerHTML = state.runs.map((run) => {
      const status = run.analysis_status || run.run_status
      const selected = state.current?.id === run.id ? 'selected' : ''
      const title = [run.company, run.report_year && `${run.report_year} 年`].filter(Boolean).join(' · ') || run.file_name || '未命名年报'
      const date = run.created_at ? new Date(run.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''
      const from = run.legacy ? '历史结果' : `${run.fact_count || 0} 事实 · ${run.calculation_count || 0} 计算`
      return `<button class="history-item ${selected}" type="button" data-open-run="${escape(run.id)}"><span class="history-pdf">PDF</span><span class="history-copy"><b title="${escape(title)}">${escape(title)}</b><small>${escape(date)} · ${escape(from)}</small></span><span class="history-tag ${statusClass(status)}">${escape(statusLabel(status))}</span></button>`
    }).join('')
  }

  async function openRun(id) {
    stopPolling()
    resultPanel.innerHTML = '<div class="empty-panel">正在打开本次分析结果…</div>'
    try {
      const [run, chat] = await Promise.all([request(`/api/runs/${id}`), request(`/api/runs/${id}/followup`).catch(() => ({ turns: [] }))])
      state.current = run
      state.turns = chat.turns || []
      state.tab = 'overview'
      renderCurrent()
      renderHistory()
      if (run.run_status === 'running' || state.turns.some((turn) => turn.status === 'running')) startPolling()
    } catch (error) {
      state.current = null
      resultPanel.innerHTML = `<div class="empty-panel">打开失败：${escape(error.message)}</div>`
    }
  }

  function startPolling() {
    stopPolling()
    state.timer = window.setInterval(async () => {
      if (!state.current?.id) return stopPolling()
      try {
        const [run, chat] = await Promise.all([request(`/api/runs/${state.current.id}`), request(`/api/runs/${state.current.id}/followup`)])
        state.current = run
        state.turns = chat.turns || []
        renderCurrent()
        renderHistory()
        if (run.run_status !== 'running' && !state.turns.some((turn) => turn.status === 'running')) stopPolling()
      } catch { /* Keep the last visible result while the service recovers. */ }
    }, 1500)
  }

  function stopPolling() {
    if (state.timer) window.clearInterval(state.timer)
    state.timer = null
  }

  function refButton(id, page, text = '') {
    const label = text || (page ? `PDF 第 ${page} 页` : id || '来源')
    return `<button type="button" class="ref-chip ${page ? 'page-link' : ''}" ${page ? `data-page="${escape(page)}"` : ''}>${escape(label)}</button>`
  }

  function renderEvidenceRefs(refs) {
    if (!Array.isArray(refs) || !refs.length) return '<span class="empty-value">未提供可定位引用</span>'
    return `<div class="ref-list">${refs.map((ref) => {
      if (typeof ref === 'string') return refButton(ref, null, ref)
      const factId = ref?.fact_id || ref?.calculation_id || ''
      const page = Number(ref?.page) || null
      return refButton(factId, page, `${factId ? `${factId} · ` : ''}${page ? `第 ${page} 页` : '无页码'}`)
    }).join('')}</div>`
  }

  function renderGeneric(value, key = '', depth = 0) {
    value = clean(value)
    if (value === null || value === undefined || value === '') return '<span class="empty-value">未提供</span>'
    if (typeof value === 'boolean') return value ? '是' : '否'
    if (typeof value === 'number') return escape(value)
    if (typeof value === 'string') return escape(value).replace(/\n/g, '<br>')
    if (Array.isArray(value)) {
      if (!value.length) return '<span class="empty-value">未提供</span>'
      if (['evidence_refs', 'calculation_refs'].includes(key)) return renderEvidenceRefs(value)
      if (value.every((item) => ['string', 'number', 'boolean'].includes(typeof item) || item == null)) {
        return `<ul class="value-list">${value.map((item) => `<li>${renderGeneric(item, '', depth + 1)}</li>`).join('')}</ul>`
      }
      return `<div class="value-stack">${value.map((item, index) => `<div class="nested-object"><span class="field-label">${escape(display(key))} ${index + 1}</span>${renderGeneric(item, '', depth + 1)}</div>`).join('')}</div>`
    }
    if (typeof value === 'object') {
      if (depth > 7) return `<pre>${escape(JSON.stringify(value))}</pre>`
      const entries = Object.entries(value)
      if (!entries.length) return '<span class="empty-value">未提供</span>'
      return `<div class="object-grid">${entries.map(([childKey, child]) => {
        const wide = ['summary', 'analysis', 'narrative', 'note', 'conclusion', 'limitations', 'formula', 'quote'].includes(childKey) || Array.isArray(child) || (child && typeof child === 'object')
        return `<div class="object-field ${wide ? 'wide' : ''}"><span class="field-label">${escape(display(childKey))}</span><div class="field-value">${renderGeneric(child, childKey, depth + 1)}</div></div>`
      }).join('')}</div>`
    }
    return escape(value)
  }

  function renderEvidence(evidence) {
    if (!Array.isArray(evidence) || !evidence.length) return '<div class="empty-panel">没有保留逐条原文摘录。</div>'
    return evidence.map((item) => {
      const matched = item?.quote_matched === true
      return `<div class="evidence-quote">${escape(item?.quote || '未提供摘录')}<div class="quote-meta"><button type="button" class="ref-chip page-link" data-page="${escape(item?.page || '')}">PDF 第 ${escape(item?.page || '—')} 页</button><span>${escape(display(item?.role || ''))}</span><span class="${matched ? 'quote-match' : 'quote-unmatched'}">${matched ? '原文摘录文字匹配' : '摘录未匹配'}</span></div></div>`
    }).join('')
  }

  function renderFact(fact) {
    const ready = fact?.calculation_ready === true
    const validation = fact?.validation || {}
    return `<article class="fact-card">
      <div class="fact-topline"><div><div class="fact-name">${escape(fact?.original_label || display(fact?.metric_key || '事实'))}${fact?.dimension ? ` · ${escape(fact.dimension)}` : ''}</div><div class="fact-value">${fact?.value === '' || fact?.value == null ? '<span class="empty-value">未提供</span>' : escape(fact.value)} <span class="fact-unit">${escape(fact?.unit || '')}</span></div></div><span class="readiness ${ready ? '' : 'is-no'}">${ready ? '可参与计算' : '待核口径'}</span></div>
      <div class="fact-meta"><span>${escape(fact?.fact_id || '无编号')}</span><span>${escape(fact?.as_of_date || fact?.period_end || fact?.period_label || '期间未提供')}</span><span>${escape(fact?.reporting_scope || '范围未提供')}</span><span>${escape(fact?.measurement_basis || '总额/净额未提供')}</span></div>
      ${fact?.normalization_notes?.length ? `<p class="citation-note">${escape(fact.normalization_notes.join('；'))}</p>` : ''}
      <div class="evidence-block">${renderEvidence(fact?.evidence)}</div>
      <details class="json-details"><summary>查看该事实的核验字段</summary>${renderGeneric(validation, 'validation')}</details>
      <p class="citation-note">摘录匹配只说明原文文字可定位；复杂表格的行列语义未必已独立确认。</p>
    </article>`
  }

  function renderCalculation(calc) {
    const inputs = Array.isArray(calc?.inputs) ? calc.inputs : []
    return `<article class="calculation-card">
      <div class="calc-topline"><div><div class="calc-name">${escape(calc?.name || '程序计算')}</div><div class="field-label">${escape(calc?.calculation_id || '无编号')}</div></div><span class="readiness">程序计算</span></div>
      <div class="calculation-value">${calc?.output === '' || calc?.output == null ? '<span class="empty-value">未提供结果</span>' : escape(calc.output)} <span class="fact-unit">${escape(calc?.unit || '')}</span></div>
      <div class="formula">${escape(calc?.formula || '公式未提供')}</div>
      ${inputs.length ? `<div class="input-list">${inputs.map((item) => `<div class="input-row"><span>${escape(item?.fact_id || item?.calculation_id || '输入值')} ${item?.as_of_date ? `· ${escape(item.as_of_date)}` : ''}</span><span>${item?.value == null || item?.value === '' ? '未提供' : escape(item.value)} ${escape(item?.unit || '')}</span></div>`).join('')}</div>` : ''}
      ${calc?.note ? `<p class="citation-note">${escape(calc.note)}</p>` : ''}
      <div class="ref-list">${(calc?.input_fact_ids || []).map((id) => refButton(id, null, id)).join('')}</div>
    </article>`
  }

  function renderFinding(finding, index) {
    const audit = finding?.citation_audit || {}
    const refs = finding?.evidence_refs || []
    const citations = refs.map((ref) => {
      const fact = (state.current?.result?.facts || []).find((item) => item.fact_id === ref?.fact_id)
      const page = Number(ref?.page) || fact?.evidence?.find((item) => Number(item.page))?.page
      return { ...ref, page }
    })
    const calculations = finding?.calculation_refs || []
    return `<article class="finding-card">
      <div class="fact-topline"><h4>${escape(finding?.title || `发现 ${index + 1}`)}</h4><span class="citation-state ${escape(audit.status || 'needs_review')}">${escape(({ source_linked: '引用可定位', needs_review: '待复核', missing: '缺少引用' })[audit.status] || '引用状态待核')}</span></div>
      ${finding?.conclusion ? `<p><b>判断：</b>${escape(finding.conclusion)}</p>` : ''}
      ${finding?.analysis ? `<p><b>分析：</b>${escape(finding.analysis)}</p>` : ''}
      ${finding?.explanation ? `<p><b>解释：</b>${escape(finding.explanation)}</p>` : ''}
      <div class="section-block"><span class="field-label">${escape(display('evidence_refs'))}</span>${renderEvidenceRefs(citations)}</div>
      <div class="section-block"><span class="field-label">${escape(display('calculation_refs'))}</span>${renderEvidenceRefs(calculations)}</div>
      ${finding?.limitations ? `<p class="citation-note"><b>限制：</b>${escape(Array.isArray(finding.limitations) ? finding.limitations.join('；') : finding.limitations)}</p>` : ''}
      <p class="citation-note">引用可关联只表示事实编号、页码和摘录可连接，不等于专业判断已证实。</p>
    </article>`
  }

  function sectionBlock(title, body) {
    return `<section class="section-block"><h4 class="section-label">${escape(title)}</h4>${body}</section>`
  }

  function renderOverview(result) {
    const report = result.report || {}
    const facts = result.facts || []
    const findings = result.findings || []
    return `${sectionBlock('报告身份', `<div class="content-card">${renderGeneric(report, 'report')}</div>`)}
      ${sectionBlock('模块三分析摘要', `<div class="summary-box">${escape(result.summary || '本次未提供摘要。')}</div>`)}
      ${sectionBlock('关键数量', `<div class="object-grid"><div class="object-field"><span class="field-label">登记事实</span><div class="field-value">${facts.length}</div></div><div class="object-field"><span class="field-label">程序计算</span><div class="field-value">${(result.calculations || []).length}</div></div><div class="object-field"><span class="field-label">重点发现</span><div class="field-value">${findings.length}</div></div><div class="object-field"><span class="field-label">已回查页码</span><div class="field-value">${(state.current.read_pages || []).length || '未记录'}</div></div></div>`)}
      ${findings.length ? sectionBlock('重点发现', findings.slice(0, 4).map(renderFinding).join('')) : sectionBlock('重点发现', '<div class="empty-panel">本次没有形成重点发现，不能据此推断没有风险。</div>')}
      ${result.quality_issues?.length ? sectionBlock('数据复核提醒', `<div class="limitation-note">${renderGeneric(result.quality_issues)}</div>`) : ''}`
  }

  function renderFiveSection(key, result) {
    const value = result[key]
    if (!value || (typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length)) {
      return '<div class="empty-panel">本次结果没有提供该专题内容。这表示资料未提供或模块未形成结论，不代表数值为零或没有风险。</div>'
    }
    return `<div class="content-card">${renderGeneric(value, key)}</div>`
  }

  function renderFindings(result) {
    const findings = Array.isArray(result.findings) ? result.findings : []
    return findings.length ? findings.map(renderFinding).join('') : '<div class="empty-panel">本次没有形成重点发现。请同时检查专题页和限制说明。</div>'
  }

  function renderFacts(result) {
    const all = Array.isArray(result.facts) ? result.facts : []
    const query = state.factQuery.trim().toLowerCase()
    const visible = query ? all.filter((fact) => JSON.stringify(fact).toLowerCase().includes(query)) : all
    return `<div class="search-row"><input id="fact-search" type="search" placeholder="搜索科目、事实编号、期间…" value="${escape(state.factQuery)}" /><span class="count-label">${visible.length} / ${all.length} 条</span></div>${visible.length ? visible.map(renderFact).join('') : '<div class="empty-panel">没有匹配的事实。</div>'}`
  }

  function renderCalculations(result) {
    const calculations = Array.isArray(result.calculations) ? result.calculations : []
    return calculations.length ? calculations.map(renderCalculation).join('') : '<div class="empty-panel">本次没有可用的程序计算。空值和资料缺项未按零处理。</div>'
  }

  function renderEvidenceLimitations(result) {
    const groups = [
      ['事实摘录核验说明', result.evidence_notes],
      ['结论引用核对说明', result.evidence_reference_notes],
      ['事实抽取备注', result.extraction_notes],
      ['计算限制', result.calculation_limitations],
      ['资料限制', result.limitations],
      ['分析缺失字段', result.analysis_missing_fields],
      ['结果结构提示', result.analysis_shape_issues],
      ['需复核数据', result.quality_issues],
    ]
    const rendered = groups.filter(([, value]) => Array.isArray(value) ? value.length : Boolean(value)).map(([title, value]) => sectionBlock(title, `<div class="note-card">${renderGeneric(value)}</div>`)).join('')
    const banner = '<div class="limitation-note">原文文字匹配只说明摘录可定位；并不自动证明复杂表格的行列对应、业务解释或因果判断。资料未披露、空值和未计算均保持为缺项，不以 0 替代。</div>'
    return `${banner}${rendered || '<div class="empty-panel">本次没有单独登记其他限制。仍请按原年报复核关键判断。</div>'}`
  }

  function renderHandoff(result) {
    return Array.isArray(result.handoff) && result.handoff.length ? `<div class="value-stack">${result.handoff.map((item, index) => `<article class="note-card"><span class="field-label">交接线索 ${index + 1}</span>${renderGeneric(item, 'handoff')}</article>`).join('')}</div>` : '<div class="empty-panel">本次没有提供给其他模块的交接线索。</div>'
  }

  function renderFollowup() {
    const turns = state.turns || []
    const messages = turns.map((turn) => `<div class="chat-message question"><b>你的问题</b><p>${escape(turn.question || '')}</p></div><div class="chat-message"><b>${turn.status === 'running' ? '正在查询年报并组织回答…' : turn.status === 'failed' ? '追问未完成' : '模块三回答'}</b><p>${escape(turn.error || turn.answer || (turn.status === 'running' ? '可能正在检索原文或执行计算。' : ''))}</p>${(turn.source_pages || []).length ? `<div class="ref-list">${turn.source_pages.map((page) => refButton('', page)).join('')}</div>` : ''}</div>`).join('')
    return `<div class="followup-chat">${messages || '<div class="empty-panel">追问会携带本次模块三完整结果和专业指导；需要时可继续搜索、读取年报页并计算。</div>'}</div>
      <form id="followup-form" class="followup-form"><textarea name="question" maxlength="1200" placeholder="例如：应收账款余额变化主要由哪些项目造成？请回到年报页核对。" required></textarea><button class="primary-button" type="submit" ${turns.some((turn) => turn.status === 'running') ? 'disabled' : ''}>发送追问 <span>↗</span></button><p class="followup-hint">每次追问会重新附带模块三专业指导与当前结果。读取到的页码会显示为可点击回查。</p></form>`
  }

  function renderRunInfo(result) {
    const fields = ['module_id', 'module_version', 'analysis_status', 'extraction_status', 'calculation_status', 'prompt_version', 'requested_new_fact_count', 'usage', 'discovery', 'analysis_error']
    const info = Object.fromEntries(fields.filter((key) => result[key] !== undefined).map((key) => [key, result[key]]))
    return `<div class="content-card">${renderGeneric(info, 'run_info')}</div><details class="json-details"><summary>查看完整 JSON（包含本次全部结构化结果）</summary><pre>${escape(JSON.stringify(result, null, 2))}</pre></details>`
  }

  function renderTabContent(result) {
    if (state.tab === 'overview') return renderOverview(result)
    if (['asset_map', 'receivables', 'inventory', 'long_term_assets', 'efficiency'].includes(state.tab)) return renderFiveSection(state.tab, result)
    if (state.tab === 'findings') return renderFindings(result)
    if (state.tab === 'facts') return renderFacts(result)
    if (state.tab === 'calculations') return renderCalculations(result)
    if (state.tab === 'evidence') return renderEvidenceLimitations(result)
    if (state.tab === 'handoff') return renderHandoff(result)
    if (state.tab === 'followup') return renderFollowup()
    return renderRunInfo(result)
  }

  function renderCurrent() {
    const run = state.current
    if (!run) return
    const result = run.result
    const status = run.analysis_status || 'pending'
    const company = result?.report?.company || run.company || '年报分析'
    const year = result?.report?.report_year || run.report_year || ''
    const isRunning = run.run_status === 'running'
    const tabs = tabItems.map(([key, title]) => `<button type="button" class="${state.tab === key ? 'active' : ''}" data-tab="${key}">${title}</button>`).join('')
    const content = result ? renderTabContent(result) : `<div class="empty-panel">分析结果尚未生成。完成情况：${escape(run.stage_detail || run.error || run.stage || '等待后端处理')}。</div>`
    resultPanel.innerHTML = `<div class="result-layout">
      <header class="result-heading"><div><span class="eyebrow">模块三 · ${escape(run.legacy ? '历史分析' : '本机新运行')}</span><h2>${escape(company)}${year ? ` · ${escape(year)} 年` : ''}</h2><p>${escape(run.file_name || '年报 PDF')}${run.page_count ? ` · ${escape(run.page_count)} 页` : ''}${run.legacy ? ' · 历史产物只读' : ''}</p><div class="status-line"><span class="status-pill ${statusClass(run.run_status)}">运行：${escape(statusLabel(run.run_status))}</span><span class="status-pill ${statusClass(status)}">分析：${escape(statusLabel(status))}</span></div></div><div class="result-actions">${!run.legacy ? '<button class="subtle-button" type="button" data-open-pdf>打开 PDF</button>' : ''}<button class="subtle-button" type="button" data-download-json>下载完整 JSON</button></div></header>
      <div class="status-disclaimer">运行结束只说明本次程序已停止。分析状态单独表示完整、部分完成、资料不足、待复核或失败；原文引文可定位不等于专业结论已被证实。</div>
      ${isRunning ? `<div class="progress-card"><div class="progress-title"><b>${escape(run.stage || '正在分析')}</b><span>${escape(run.stage_status || '进行中')}</span></div><div class="progress-track"><i></i></div><p>${escape(run.stage_detail || '模块三正在定位资产页、核对事实并形成专题结果。')}</p>${(run.progress || []).slice(-5).map((item) => `<p>${escape(item.stage || '')} · ${escape(item.status || '')}　${escape(item.detail || '')}</p>`).join('')}</div>` : ''}
      ${run.error ? `<div class="error-card">${escape(run.error)}</div>` : ''}
      <nav class="result-tabs">${tabs}</nav>
      <section class="tab-panel"><div class="tab-heading"><div><span class="eyebrow">${escape((tabItems.find(([key]) => key === state.tab) || ['', '结果'])[1])}</span><h3>${escape((tabItems.find(([key]) => key === state.tab) || ['', '结果'])[1])}</h3><p>${state.tab === 'facts' ? `${(result?.facts || []).length} 条候选事实` : state.tab === 'calculations' ? `${(result?.calculations || []).length} 项可复核计算` : '保留本次模块三的原始结构与公司专题'}</p></div></div>${content}</section>
      ${result ? `<details class="json-details"><summary>导出之外，展开查看本页所有字段</summary><pre>${escape(JSON.stringify(result, null, 2))}</pre></details>` : ''}
    </div>`
  }

  async function openSourcePage(page) {
    if (!state.current?.id || !page) return
    document.getElementById('page-title').textContent = `PDF 第 ${page} 页`
    document.getElementById('page-meta').textContent = `${state.current.file_name || '年报'} · 原文文字提取回查`
    document.getElementById('page-text').textContent = '正在读取年报原文…'
    openModal('page-modal')
    try {
      const data = await request(`/api/runs/${state.current.id}/pages/${page}`)
      document.getElementById('page-meta').textContent = `${data.file_name || state.current.file_name || '年报'} · ${data.source || '原文'} · 第 ${page} 页`
      document.getElementById('page-text').textContent = data.text || '该页没有可提取文字。'
    } catch (error) {
      document.getElementById('page-text').textContent = error.message
    }
  }

  async function submitKey(event) {
    event.preventDefault()
    const field = document.getElementById('api-key')
    const errorNode = document.getElementById('key-error')
    errorNode.classList.add('hidden')
    if (!field.value.trim()) { errorNode.textContent = '请先粘贴 DeepSeek API Key。'; errorNode.classList.remove('hidden'); return }
    const submit = event.currentTarget.querySelector('button[type=submit]')
    submit.disabled = true
    try {
      await request('/api/settings/deepseek-key', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: field.value }) })
      field.value = ''
      state.health = await request('/api/health')
      updateKeyStatus()
      closeModal('key-modal')
      toast('Key 已保存到任务03本机配置。')
      startButton.disabled = !state.file
    } catch (error) {
      errorNode.textContent = error.message
      errorNode.classList.remove('hidden')
    } finally {
      submit.disabled = false
    }
  }

  async function submitFollowup(event) {
    event.preventDefault()
    const textarea = event.currentTarget.elements.question
    const question = textarea.value.trim()
    if (!question || !state.current?.id) return
    const button = event.currentTarget.querySelector('button[type=submit]')
    button.disabled = true
    try {
      await request(`/api/runs/${state.current.id}/followup`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) })
      state.turns = (await request(`/api/runs/${state.current.id}/followup`)).turns || []
      state.tab = 'followup'
      textarea.value = ''
      renderCurrent()
      startPolling()
    } catch (error) {
      toast(error.message)
      button.disabled = false
    }
  }

  function downloadResult() {
    const result = state.current?.result
    if (!result) return
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${(state.current.file_name || '模块三分析').replace(/\.pdf$/i, '')}_模块三分析.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  resultPanel.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-tab]')
    if (tab) { state.tab = tab.dataset.tab; renderCurrent(); return }
    const page = event.target.closest('[data-page]')
    if (page) { openSourcePage(Number(page.dataset.page)); return }
    if (event.target.closest('[data-open-pdf]')) { window.open(`${API}/api/runs/${state.current.id}/file`, '_blank', 'noopener'); return }
    if (event.target.closest('[data-download-json]')) { downloadResult(); return }
  })
  resultPanel.addEventListener('submit', (event) => { if (event.target.id === 'followup-form') submitFollowup(event) })
  resultPanel.addEventListener('input', (event) => {
    if (event.target.id === 'fact-search') { state.factQuery = event.target.value; const cursor = event.target.selectionStart; renderCurrent(); const field = document.getElementById('fact-search'); field?.focus(); field?.setSelectionRange(cursor, cursor) }
  })
  historyList.addEventListener('click', (event) => { const button = event.target.closest('[data-open-run]'); if (button) openRun(button.dataset.openRun) })
  dropZone.addEventListener('click', () => fileInput.click())
  fileInput.addEventListener('change', () => selectFile(fileInput.files?.[0]))
  startButton.addEventListener('click', startAnalysis)
  keyStatus.addEventListener('click', () => openModal('key-modal'))
  document.getElementById('key-form').addEventListener('submit', submitKey)
  document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => closeModal(button.dataset.close)))
  document.querySelectorAll('.modal-backdrop').forEach((modal) => modal.addEventListener('click', (event) => { if (event.target === modal && modal.id !== 'key-modal' && !state.health?.api_configured) return; if (event.target === modal) closeModal(modal.id) }))
  for (const eventName of ['dragenter', 'dragover']) dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.add('is-over') })
  for (const eventName of ['dragleave', 'drop']) dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.remove('is-over') })
  dropZone.addEventListener('drop', (event) => selectFile(event.dataTransfer?.files?.[0]))

  async function init() {
    try {
      state.health = await request('/api/health')
      updateKeyStatus()
      if (!state.health.api_configured) openModal('key-modal')
    } catch (error) {
      keyStatus.querySelector('span').textContent = '无法连接模块三服务'
      toast(`工作台服务暂时不可用：${error.message}`)
    }
    await reloadHistory()
    const lastId = new URLSearchParams(window.location.search).get('run')
    if (lastId) await openRun(lastId)
    else if (state.runs.length) {
      document.getElementById('saved-result-hint').textContent = `已载入 ${state.runs.length} 份本机历史结果。点击左侧记录，即可分别查看五个专题、事实、计算和原文。`
    }
  }

  init()
})()
