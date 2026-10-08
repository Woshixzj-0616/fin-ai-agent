import { useCallback, useEffect, useMemo, useState } from 'react'
import './solvency-workbench.css'

const API = '/api'
const BUSY = new Set(['queued', 'running'])
const MODULE_VERSION = '模块五_债务与资金压力_v2.1.1'

const STATUS_TEXT = {
  complete: '分析完整',
  partial: '部分完成',
  insufficient_data: '资料不足',
  failed: '分析失败',
  needs_review: '待复核',
  available: '已提取资料',
  captured_semantics_confirmed: '已登记，语义标记已确认',
  captured_needs_review: '已登记，需复核',
  not_yet_captured: '尚未登记',
  not_found_after_search: '检索后未找到',
  not_disclosed: '标记为未披露',
  not_applicable: '不适用',
  ambiguous: '口径有歧义',
  calculated: '程序已计算',
  not_calculated: '未能计算',
  quote_and_number_matched: '摘录与数字匹配',
  quote_matched: '摘录文字匹配',
  unmatched: '来源未匹配',
  automatic_source_text_match_only: '仅完成自动来源文字匹配',
}

const LABELS = {
  label: '项目', original_label: '原文项目名', amount_100m_cny: '金额（亿元）', display: '呈现值',
  display_value: '计算结果', display_unit: '单位', value: '数值', unit: '单位', summary: '分析摘要',
  status: '状态', section_status: '板块状态', section_evidence_notes: '证据说明', note: '说明',
  fact_ids: '事实编号', calculation_ids: '计算编号', evidence_fact_ids: '关联事实',
  evidence: '原文证据', page: 'PDF 页码', quote: '原文摘录', role: '证据用途',
  source_context: '表格语境', row_label: '表格行名', column_header: '列标题', unit_label: '单位栏',
  semantic_status: '语义状态', review_note: '语境说明', validation: '自动匹配检查',
  calculation_eligible: '允许进入计算', source_pages: '来源页码', period: '期间', period_end: '期末日期',
  period_start: '起始日期', as_of_date: '时点', period_type: '期间类型', measurement_basis: '金额口径',
  currency: '币种', scope: '报表范围', liability_type: '负债类别', normalized_value: '标准化金额',
  normalized_unit: '标准化单位', formula: '公式', input_fact_ids: '输入事实编号', input_labels: '输入项目',
  failure_reason: '未能计算原因', error: '错误原因', reason: '原因', missing_inputs: '缺失输入',
  name: '指标', metric: '指标代码', topic: '专题', statement: '分析判断', claim: '分析判断',
  evidence_status: '证据状态', limitations: '限制', follow_up: '后续核查', question: '问题',
  reason_text: '原因', next_module: '后续模块', analysis_type: '判断类型', alternatives: '其他解释',
  human_reviewed: '人工复核', checked: '已检查范围', not_checked: '未检查范围', problems: '问题',
  semantic_context_status: '语义核验状态', semantic_context_problems: '语义问题',
  row_column_unit_scope_identity_checked: '行列/单位/范围人工核实', revision: '修订次数',
  included_fact_ids: '包含的组成项', included_in_total: '是否计入合计',
  total_assets: '资产总计', total_liabilities: '负债合计', current_assets: '流动资产合计',
  current_liabilities: '流动负债合计', equity: '所有者权益', cash: '货币资金', inventory: '存货',
  short_borrowings: '短期借款', current_maturities: '一年内到期负债', long_borrowings: '长期借款',
  bonds: '应付债券', lease_liabilities: '租赁负债', operating_cash_flow: '经营活动现金流量净额',
  pretax_profit: '利润总额', interest_expense: '利息费用',
}

function statusText(value) {
  return STATUS_TEXT[value] || value || '未提供'
}

function labelText(value) {
  return LABELS[value] || value.replaceAll('_', ' ')
}

function stringify(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function apiError(response) {
  return response.json().then((data) => data.detail || '请求没有完成。').catch(() => '请求没有完成。')
}

function taskStatus(run) {
  if (!run) return { text: '尚未选择分析记录', tone: 'neutral' }
  if (run.status === 'queued' || run.status === 'running') return { text: `任务运行中 · ${run.stage || '处理中'}`, tone: 'working' }
  if (run.status === 'failed') return { text: '任务失败', tone: 'danger' }
  if (run.status !== 'completed') return { text: '任务状态待确认', tone: 'neutral' }
  const analysis = run.result?.analysis_status || run.analysis_status
  if (analysis === 'complete') return { text: '任务已结束 · 分析完整', tone: 'good' }
  if (analysis === 'partial') return { text: '任务已结束 · 分析部分完成', tone: 'review' }
  if (analysis === 'insufficient_data') return { text: '任务已结束 · 资料不足', tone: 'review' }
  if (analysis === 'failed') return { text: '任务已结束 · 分析失败', tone: 'danger' }
  return { text: '任务已结束 · 结果待复核', tone: 'review' }
}

function ValueView({ value, runId, onOpenPage, field = '' }) {
  if (value === null || value === undefined || value === '') return <span className="sw-muted">—</span>
  if (Array.isArray(value)) {
    if (!value.length) return <span className="sw-muted">无已登记项目</span>
    return <div className="sw-value-list">{value.map((item, index) => <ValueView key={index} value={item} runId={runId} onOpenPage={onOpenPage} field={field} />)}</div>
  }
  if (typeof value === 'object') {
    if (Number.isInteger(value.page) && value.quote !== undefined) {
      return <div className="sw-evidence">
        <button type="button" className="sw-page-link" onClick={() => onOpenPage(value.page)}>PDF 第 {value.page} 页 ↗</button>
        {value.role && <span className="sw-evidence-role">{value.role}</span>}
        {value.quote && <blockquote>{value.quote}</blockquote>}
        {value.matched !== undefined && <small>文字匹配：{value.matched ? '是' : '否'}{value.number_present !== undefined ? ` · 数字出现：${value.number_present ? '是' : '否'}` : ''}</small>}
      </div>
    }
    return <dl className="sw-kv">{Object.entries(value).map(([key, child]) => (
      <div key={key}><dt>{labelText(key)}</dt><dd><ValueView value={child} runId={runId} onOpenPage={onOpenPage} field={key} /></dd></div>
    ))}</dl>
  }
  if (field === 'status' || field.endsWith('_status') || field === 'semantic_status' || field === 'evidence_status') {
    return <span className={`sw-chip sw-chip-${value === 'calculated' || value === 'complete' ? 'good' : 'review'}`}>{statusText(value)}</span>
  }
  if (field === 'fact_ids' || field === 'calculation_ids' || field === 'evidence_fact_ids' || field === 'input_fact_ids') {
    return <span className="sw-id-list">{String(value)}</span>
  }
  return <span className="sw-value-text">{stringify(value)}</span>
}

function SectionCard({ title, section, runId, onOpenPage, emptyText }) {
  const items = Array.isArray(section?.items) ? section.items : []
  return <section className="sw-section-card">
    <div className="sw-section-heading"><div><span className="sw-section-kicker">模块五专题</span><h3>{title}</h3></div>
      {section?.section_status && <span className="sw-chip sw-chip-review">{statusText(section.section_status)}</span>}
    </div>
    {section?.summary && <p className="sw-section-summary">{section.summary}</p>}
    {items.length ? <div className="sw-detail-items">{items.map((item, index) => (
      <article className="sw-detail-item" key={`${item?.label || item?.name || title}-${index}`}>
        {typeof item === 'object' && !Array.isArray(item)
          ? <ValueView value={item} runId={runId} onOpenPage={onOpenPage} />
          : <p>{stringify(item)}</p>}
      </article>
    ))}</div> : <p className="sw-empty">{emptyText}</p>}
    {section?.section_evidence_notes && <div className="sw-section-notes"><b>证据与限制</b><ValueView value={section.section_evidence_notes} runId={runId} onOpenPage={onOpenPage} /></div>}
  </section>
}

function calculationNeedsReview(item, facts) {
  const used = new Set(item.input_fact_ids || [])
  return facts.some((fact) => used.has(fact.fact_id)
    && fact.source_context?.kind === 'table'
    && fact.source_context?.layout_alignment !== 'confirmed')
}

function KeyModal({ onClose, onSave, saving, error }) {
  const [key, setKey] = useState('')
  const [visible, setVisible] = useState(false)
  return <div className="sw-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <form className="sw-modal" onSubmit={(event) => { event.preventDefault(); onSave(key) }}>
      <div className="sw-modal-mark">密</div>
      <button className="sw-modal-close" type="button" onClick={onClose} aria-label="关闭">×</button>
      <span className="sw-eyebrow">本机配置</span>
      <h2>连接 DeepSeek</h2>
      <p>Key 写入本任务目录的本机配置文件，只由本机服务调用，不写入浏览器缓存或代码提交。</p>
      <div className="sw-key-field"><input autoFocus autoComplete="off" spellCheck="false" type={visible ? 'text' : 'password'} value={key} onChange={(event) => setKey(event.target.value)} placeholder="粘贴 DeepSeek API Key" /><button type="button" onClick={() => setVisible((current) => !current)}>{visible ? '隐藏' : '显示'}</button></div>
      {error && <div className="sw-error-text" role="alert">{error}</div>}
      <div className="sw-modal-actions"><button className="sw-button sw-button-quiet" type="button" onClick={onClose}>稍后配置</button><button className="sw-button sw-button-primary" disabled={saving || !key.trim()}>{saving ? '保存中…' : '保存并继续'}</button></div>
    </form>
  </div>
}

function SourceModal({ page, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    fetch(`${API}/runs/${page.runId}/pages/${page.number}`).then(async (response) => {
      if (!response.ok) throw new Error(await apiError(response))
      return response.json()
    }).then((result) => { if (!cancelled) setData(result) }).catch((cause) => { if (!cancelled) setError(cause.message) })
    return () => { cancelled = true }
  }, [page])
  return <div className="sw-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className="sw-source-modal" aria-label={`PDF 第 ${page.number} 页原文`}>
      <div className="sw-source-head"><div><span className="sw-eyebrow">来源回查</span><h2>PDF 第 {page.number} 页</h2></div><div className="sw-source-actions"><a href={`${API}/runs/${page.runId}/file#page=${page.number}`} target="_blank" rel="noreferrer">打开原始 PDF ↗</a><button type="button" className="sw-modal-close-inline" onClick={onClose}>关闭</button></div></div>
      {error ? <div className="sw-error-box">{error}</div> : data ? <pre className="sw-source-text">{data.text}</pre> : <div className="sw-loading">正在读取页面原文…</div>}
    </section>
  </div>
}

function RunResult({ run, onOpenPage }) {
  const result = run?.result
  if (!run) return <section className="sw-empty-state"><div className="sw-empty-icon">05</div><h2>上传年报，开始模块五分析</h2><p>系统会沿着债务构成、期限、资金、偿付指标与约束事项查找证据，并保留原文回查入口。</p></section>
  if (BUSY.has(run.status)) return <section className="sw-work-state"><div className="sw-loader"/><h2>{run.stage || '正在分析'}</h2><p>页面会保留当前进度，结果将在后端保存后显示。</p><StepList steps={run.steps || []} />{result && <PartialSnapshot result={result} run={run} onOpenPage={onOpenPage} />}</section>
  if (run.status === 'failed' && !result) return <section className="sw-error-box"><b>任务失败</b><p>{run.error || '本次分析没有保存可展示的结果。'}</p></section>
  if (!result) return <section className="sw-empty-state"><h2>没有可展示的结构化结果</h2><p>任务状态：{run.status} · {run.stage}</p>{run.error && <p>{run.error}</p>}</section>

  const calculations = Array.isArray(result.calculations) ? result.calculations : []
  const facts = Array.isArray(result.facts) ? result.facts : []
  const findings = Array.isArray(result.findings) ? result.findings : []
  const coverage = result.data_coverage && typeof result.data_coverage === 'object' ? Object.entries(result.data_coverage) : []
  const evidenceReview = result.evidence_review || {}
  const mainStatus = taskStatus(run)
  const unverifiedLayouts = facts.filter((fact) => fact.source_context?.kind === 'table' && fact.source_context?.layout_alignment !== 'confirmed')
  const resultReview = result.analysis_status !== 'complete' || evidenceReview.status !== 'human_reviewed' || unverifiedLayouts.length > 0

  return <div className="sw-result">
    <div className="sw-result-title-row"><div><span className="sw-eyebrow">{result.module_version || MODULE_VERSION}</span><h2>{run.file_name}</h2><p>债务与偿债能力 · PDF {run.page_count} 页 · 本次完整读取 {result.read_pages?.length || 0} 页</p></div><a className="sw-pdf-link" href={`${API}/runs/${run.id}/file`} target="_blank" rel="noreferrer">打开原始 PDF ↗</a></div>
    <div className={`sw-status-banner sw-tone-${mainStatus.tone}`}><span className="sw-status-dot"/><div><strong>{mainStatus.text}</strong><p>任务运行状态：{run.status === 'completed' ? '已结束' : statusText(run.status)}{run.error ? ` · ${run.error}` : ''}</p></div><span className="sw-analysis-label">分析标记：{statusText(result.analysis_status)}</span></div>
    {resultReview && <div className="sw-review-banner"><b>使用边界</b><p>自动匹配到来源文字或数字，不代表表格行列、单位、报表范围及会计含义已由人工复核。请查看各事实的来源、匹配状态和限制后再使用。{unverifiedLayouts.length ? `本结果有 ${unverifiedLayouts.length} 条表格事实缺少可确认的布局对应关系，相关已存计算需要复核。` : ''}</p><span>证据状态：{statusText(evidenceReview.status)} · 人工复核：{evidenceReview.human_reviewed ? '已标记' : '未标记'}</span></div>}
    <article className="sw-headline"><span>模块五分析摘要</span><p>{result.headline || '本次未生成摘要，请查看已保存的事实、计算与专题。'}</p></article>
    <section className="sw-progress-panel"><div className="sw-panel-head"><div><span className="sw-eyebrow">运行记录</span><h3>本次处理进度</h3></div><span>{(run.steps || []).length} 项步骤</span></div><StepList steps={run.steps || []} ended={!BUSY.has(run.status)} /></section>

    <div className="sw-result-section-title"><div><span>01</span><div><h3>核心偿付指标</h3><p>按程序记录逐项展示结果和未计算原因。</p></div></div></div>
    {calculations.length ? <div className="sw-calculation-grid">{calculations.map((item, index) => (
      <article className={`sw-calculation-card ${item.status === 'calculated' ? '' : 'sw-calculation-review'}`} key={item.calculation_id || `${item.metric}-${index}`}>
        <div className="sw-calc-top"><h4>{item.name || item.metric || `计算项 ${index + 1}`}</h4><span className={`sw-chip ${item.status === 'calculated' ? 'sw-chip-good' : 'sw-chip-review'}`}>{statusText(item.status)}{calculationNeedsReview(item, facts) ? ' · 输入待复核' : ''}</span></div>
        <div className="sw-calc-value">{item.display_value != null ? `${item.display_value} ${item.display_unit || ''}` : item.value != null ? `${item.value} ${item.unit || ''}` : '未计算'}</div>
        {item.formula && <p className="sw-calc-formula">{item.formula}</p>}
        {item.input_labels?.length > 0 && <p className="sw-calc-inputs">输入：{item.input_labels.join('、')}</p>}
        {(item.note || item.failure_reason || item.reason || item.error) && <p className="sw-calc-note">{item.status === 'calculated' ? '口径说明：' : '原因/缺口：'}{[item.failure_reason, item.reason, item.error, item.note].filter(Boolean).join('；')}</p>}
        {item.input_fact_ids?.length > 0 && <small className="sw-calc-ids">事实编号：{item.input_fact_ids.join(' · ')}</small>}
        {item.calculation_id && <small className="sw-calc-ids">计算编号：{item.calculation_id}</small>}
      </article>
    ))}</div> : <div className="sw-empty">本次没有保存计算记录。</div>}

    <div className="sw-result-section-title"><div><span>02</span><div><h3>偿债专题结果</h3><p>债务、期限、资金来源、可用资金、支撑指标和约束事项分开展示。</p></div></div></div>
    <div className="sw-topic-grid">
      <SectionCard title="债务构成" section={result.debt_structure} runId={run.id} onOpenPage={onOpenPage} emptyText="未能形成债务构成明细；请看限制与覆盖状态。" />
      <SectionCard title="到期期限" section={result.maturity_profile} runId={run.id} onOpenPage={onOpenPage} emptyText="未能形成到期分布明细；不能据此判断期限不存在。" />
      <SectionCard title="可用资金" section={result.cash_availability} runId={run.id} onOpenPage={onOpenPage} emptyText="未能确认资金可动用情况；空项不表示可用资金为零。" />
      <SectionCard title="融资来源" section={result.funding_sources} runId={run.id} onOpenPage={onOpenPage} emptyText="未能形成融资来源明细。" />
      <SectionCard title="偿付支撑指标" section={result.support_metrics} runId={run.id} onOpenPage={onOpenPage} emptyText="未能形成支撑指标专题。" />
      <SectionCard title="约束与偿付义务" section={result.obligations} runId={run.id} onOpenPage={onOpenPage} emptyText="未能形成义务或约束事项；请查看待核项。" />
    </div>

    <div className="sw-result-section-title"><div><span>03</span><div><h3>资料覆盖与待核项</h3><p>尚未登记和检索后未找到是不同状态；任何空项都不代表零。</p></div></div></div>
    {coverage.length ? <div className="sw-coverage-grid">{coverage.map(([key, item]) => (
      <article className="sw-coverage-card" key={key}><div><strong>{labelText(key)}</strong><span className={`sw-chip ${item?.status === 'captured_semantics_confirmed' ? 'sw-chip-good' : 'sw-chip-review'}`}>{statusText(item?.status)}</span></div>{item?.note && <p>{item.note}</p>}{item?.fact_ids?.length > 0 && <small>事实：{item.fact_ids.join(' · ')}</small>}{item?.explanation && <p>{item.explanation}</p>}</article>
    ))}</div> : <div className="sw-empty">本次没有生成资料覆盖清单。</div>}

    {result.handoff_questions?.length > 0 && <section className="sw-plain-card"><h3>待核问题</h3><ValueView value={result.handoff_questions} runId={run.id} onOpenPage={onOpenPage} /></section>}
    {result.limitations?.length > 0 && <section className="sw-plain-card sw-limits"><h3>分析限制与缺口</h3><ul>{result.limitations.map((item, index) => <li key={index}>{typeof item === 'string' ? item : <ValueView value={item} runId={run.id} onOpenPage={onOpenPage} />}</li>)}</ul></section>}

    <div className="sw-result-section-title"><div><span>04</span><div><h3>事实账本与原文证据</h3><p>{facts.length} 条已保存事实；点击页码可回看提取原文。</p></div></div></div>
    {facts.length ? <div className="sw-fact-list">{facts.map((fact, index) => <article className="sw-fact-card" key={fact.fact_id || index}>
      <div className="sw-fact-head"><div><h4>{fact.label || '未命名事实'}</h4><span>{fact.original_label && fact.original_label !== fact.label ? `原文：${fact.original_label}` : ''}</span></div><span className={`sw-chip ${fact.validation?.calculation_eligible && (fact.source_context?.kind !== 'table' || fact.source_context?.layout_alignment === 'confirmed') ? 'sw-chip-good' : 'sw-chip-review'}`}>{fact.validation?.calculation_eligible && (fact.source_context?.kind !== 'table' || fact.source_context?.layout_alignment === 'confirmed') ? '可进入计算' : '不可直接计算 / 需复核'}</span></div>
      <div className="sw-fact-value">{fact.display_value_100m_cny != null ? `${fact.display_value_100m_cny} 亿元` : `${fact.value ?? '未知'} ${fact.unit || ''}`}</div>
      <div className="sw-fact-meta"><span>{fact.scope || '范围未注明'}</span><span>{fact.as_of_date || fact.period_end || fact.period_type || '期间未注明'}</span><span>{fact.measurement_basis || '金额口径未注明'}</span><span>自动检查：{statusText(fact.validation?.status)}</span><span>语义：{statusText(fact.validation?.semantic_context_status || fact.source_context?.semantic_status)}</span></div>
      {fact.note && <p className="sw-fact-note">{fact.note}</p>}
      {fact.source_context && <details className="sw-fact-context"><summary>表格行列与单位语境 · {fact.source_context.row_label || fact.source_context.kind || '语境说明'}</summary><ValueView value={fact.source_context} runId={run.id} onOpenPage={onOpenPage} /></details>}
      {fact.evidence?.length > 0 && <div className="sw-fact-evidence">{fact.evidence.map((evidence, evIndex) => <div className="sw-evidence" key={`${evidence.page}-${evIndex}`}><button type="button" className="sw-page-link" onClick={() => onOpenPage(evidence.page)}>PDF 第 {evidence.page} 页 ↗</button>{evidence.role && <span className="sw-evidence-role">{evidence.role}</span>}<blockquote>{evidence.quote || '未保存摘录'}</blockquote><small>文本/数字匹配：{evidence.matched ?? fact.validation?.quote_matched ?? '未提供'}{evidence.number_present !== undefined ? ` · 数字出现：${evidence.number_present}` : ''}</small></div>)}</div>}
      {fact.validation?.problems?.length > 0 && <div className="sw-fact-problems">待处理：{fact.validation.problems.join('；')}</div>}
      <small className="sw-fact-id">事实编号：{fact.fact_id} · 修订：{fact.revision || 1}</small>
    </article>)}</div> : <div className="sw-empty">没有已登记事实；页面将保留模型中断原因。</div>}

    <div className="sw-result-section-title"><div><span>05</span><div><h3>公司专题判断</h3><p>每项保留关联事实、计算编号和证据状态。</p></div></div></div>
    {findings.length ? <div className="sw-findings-grid">{findings.map((finding, index) => <article className="sw-finding-card" key={`${finding.topic || '专题'}-${index}`}><div><h4>{finding.topic || '专题判断'}</h4><span className="sw-chip sw-chip-review">{statusText(finding.evidence_status)}</span></div><p>{finding.statement || finding.claim || '未提供判断文字'}</p><ValueView value={{ evidence_fact_ids: finding.evidence_fact_ids, calculation_ids: finding.calculation_ids }} runId={run.id} onOpenPage={onOpenPage} /></article>)}</div> : <div className="sw-empty">本次没有保存专题判断。</div>}
    {result.additional_sections?.length > 0 && <section className="sw-plain-card"><h3>模块自行增加的专题</h3><ValueView value={result.additional_sections} runId={run.id} onOpenPage={onOpenPage} /></section>}
    <details className="sw-audit-details"><summary>证据审核状态与运行摘要</summary><div className="sw-audit-body"><ValueView value={evidenceReview} runId={run.id} onOpenPage={onOpenPage} /><div><b>提示词版本</b>：{result.prompt_version || result.module_version || '未提供'}</div><div><b>模型用量</b>：{result.usage?.prompt_tokens ?? '—'} 输入 / {result.usage?.completion_tokens ?? '—'} 输出 tokens</div><div><b>工具调用</b>：{result.tool_call_count ?? '—'} 次</div><div><b>已读页</b>：{(result.read_pages || []).join('、') || '无'}</div></div></details>
    <FollowupPanel run={run} />
  </div>
}

function PartialSnapshot({ result, run, onOpenPage }) {
  const facts = Array.isArray(result?.facts) ? result.facts : []
  const calculations = Array.isArray(result?.calculations) ? result.calculations : []
  if (!result) return null
  return <div className="sw-partial-snapshot"><b>目前已保存</b><p>{facts.length} 条事实 · {calculations.length} 项计算 · {Array.isArray(result.findings) ? result.findings.length : 0} 条专题。任务仍在运行，以下结果可能继续更新。</p><div className="sw-partial-lines">{facts.slice(0, 5).map((fact) => <div key={fact.fact_id}><strong>{fact.label}</strong><span>{fact.value ?? '未知'} {fact.unit || ''}</span>{fact.evidence?.[0]?.page && <button type="button" className="sw-page-link" onClick={() => onOpenPage(fact.evidence[0].page)}>第 {fact.evidence[0].page} 页</button>}</div>)}</div><small>运行编号：{run.id}</small></div>
}

function StepList({ steps, ended = false }) {
  if (!steps?.length) return <div className="sw-step-empty">{ended ? '这条历史记录没有保存逐步进度。' : '等待后端返回流程步骤…'}</div>
  return <div className="sw-steps">{steps.map((step, index) => <article className="sw-step" key={`${step.name}-${index}`}><span className={`sw-step-mark sw-step-${step.status === '已完成' ? 'done' : step.status === '进行中' ? 'active' : step.status === '需要处理' ? 'failed' : 'queued'}`}>{step.status === '已完成' ? '✓' : step.status === '进行中' ? '·' : step.status === '需要处理' ? '!' : index + 1}</span><div><strong>{step.name}</strong><span>{statusText(step.status)}</span>{step.detail && <p>{step.detail}</p>}</div></article>)}</div>
}

function FollowupPanel({ run }) {
  const [turns, setTurns] = useState([])
  const [question, setQuestion] = useState('')
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const load = useCallback(async () => {
    if (!run?.id) return
    try {
      const response = await fetch(`${API}/runs/${run.id}/conversation`)
      if (response.ok) setTurns((await response.json()).turns || [])
    } catch { /* the parent workbench keeps reporting current run health */ }
  }, [run?.id])
  useEffect(() => { setTurns([]); load() }, [load])
  useEffect(() => {
    if (!turns.some((turn) => BUSY.has(turn.status))) return undefined
    const timer = window.setInterval(load, 1300)
    return () => window.clearInterval(timer)
  }, [turns, load])
  const submit = async (event) => {
    event.preventDefault()
    if (!question.trim() || sending) return
    setError(''); setSending(true)
    try {
      const response = await fetch(`${API}/runs/${run.id}/conversation`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.trim() }) })
      if (!response.ok) throw new Error(await apiError(response))
      const turn = await response.json()
      setTurns((current) => [...current, turn]); setQuestion('')
    } catch (cause) { setError(cause.message || '追问提交失败。') }
    finally { setSending(false) }
  }
  if (!run?.result || run.status !== 'completed') return null
  return <section className="sw-followup" id="module-five-followup"><div className="sw-panel-head"><div><span className="sw-eyebrow">年报原文工具已接入</span><h3>继续追问模块五结果</h3></div><span>每次调用携带模块五指导</span></div><p className="sw-followup-intro">追问会附带本次债务事实、计算、专题和限制；需要具体来源时会先搜索并读取年报页面。</p>
    {turns.length > 0 && <div className="sw-turn-list">{turns.map((turn) => <article className="sw-turn" key={turn.id}><div className="sw-turn-question">{turn.question}</div>{BUSY.has(turn.status) ? <div className="sw-turn-pending">正在结合模块五结果与年报原文查找…</div> : turn.status === 'failed' ? <div className="sw-turn-error">{turn.error || '本轮追问未完成。'}</div> : <div className="sw-turn-answer">{turn.answer}</div>}</article>)}</div>}
    {error && <div className="sw-error-text">{error}</div>}
    <form className="sw-question-form" onSubmit={submit}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={1200} placeholder="例如：短期到期债务主要由哪些项目构成？原文依据在哪几页？"/><button className="sw-button sw-button-primary" disabled={sending || !question.trim() || turns.some((turn) => BUSY.has(turn.status))}>{sending ? '提交中…' : '发送追问'}</button></form>
  </section>
}

export default function SolvencyWorkbench() {
  const [health, setHealth] = useState({ api_configured: false })
  const [activeRun, setActiveRun] = useState(null)
  const [recentRuns, setRecentRuns] = useState([])
  const [selectedFile, setSelectedFile] = useState(null)
  const [uploadError, setUploadError] = useState('')
  const [pageError, setPageError] = useState('')
  const [busy, setBusy] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [savingKey, setSavingKey] = useState(false)
  const [keyError, setKeyError] = useState('')
  const [sourcePage, setSourcePage] = useState(null)
  const [recentLoading, setRecentLoading] = useState(false)

  const loadRun = useCallback(async (id) => {
    try {
      const response = await fetch(`${API}/runs/${id}`)
      if (!response.ok) throw new Error(await apiError(response))
      const run = await response.json()
      setActiveRun(run); setPageError('')
      return run
    } catch (cause) { setPageError(cause.message || '無法讀取本次运行记录。'); return null }
  }, [])

  const loadRecent = useCallback(async () => {
    setRecentLoading(true)
    try {
      const response = await fetch(`${API}/runs`)
      if (!response.ok) throw new Error(await apiError(response))
      const data = await response.json()
      const runs = (data.runs || []).filter((run) => run.analysis_module === 'solvency')
      setRecentRuns(runs)
      return runs
    } catch (cause) { setPageError(cause.message || '无法读取历史任务。'); return [] }
    finally { setRecentLoading(false) }
  }, [])

  useEffect(() => {
    fetch(`${API}/health`).then(async (response) => {
      if (!response.ok) throw new Error(await apiError(response))
      return response.json()
    }).then((data) => { setHealth(data); if (!data.api_configured) setModalOpen(true) }).catch(() => setHealth({ api_configured: false, unavailable: true }))
    loadRecent()
  }, [loadRecent])

  useEffect(() => {
    if (!activeRun && recentRuns.length) loadRun(recentRuns[0].id)
  }, [activeRun, recentRuns, loadRun])

  useEffect(() => {
    const timer = window.setInterval(async () => {
      if (!activeRun?.id || !BUSY.has(activeRun.status)) return
      const updated = await loadRun(activeRun.id)
      if (updated && !BUSY.has(updated.status)) loadRecent()
    }, 1400)
    return () => window.clearInterval(timer)
  }, [activeRun, loadRun, loadRecent])

  const mostRecentId = useMemo(() => recentRuns[0]?.id, [recentRuns])
  const openRun = async (id) => { setBusy(true); await loadRun(id); setBusy(false) }

  const startAnalysis = async (event) => {
    event.preventDefault()
    if (!selectedFile) { setUploadError('请先选择年报 PDF。'); return }
    if (!selectedFile.name.toLowerCase().endsWith('.pdf')) { setUploadError('请上传 PDF 文件。'); return }
    if (!health.api_configured) { setModalOpen(true); setUploadError('先配置本任务的 DeepSeek Key，再开始分析。'); return }
    setBusy(true); setUploadError(''); setPageError(''); setActiveRun(null)
    const form = new FormData(); form.append('file', selectedFile); form.append('analysis_module', 'solvency')
    try {
      const response = await fetch(`${API}/runs`, { method: 'POST', body: form })
      if (!response.ok) throw new Error(await apiError(response))
      const run = await response.json(); setActiveRun(run); setSelectedFile(null)
      await loadRecent()
    } catch (cause) { setUploadError(cause.message || '无法连接任务05服务，请使用本目录启动文件启动。') }
    finally { setBusy(false) }
  }

  const saveKey = async (key) => {
    setSavingKey(true); setKeyError('')
    try {
      const response = await fetch(`${API}/settings/deepseek-key`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: key.trim() }) })
      if (!response.ok) throw new Error(await apiError(response))
      setHealth((current) => ({ ...current, api_configured: true, unavailable: false })); setModalOpen(false)
    } catch (cause) { setKeyError(cause.message || 'Key 保存失败，请查看任务服务是否已启动。') }
    finally { setSavingKey(false) }
  }

  const apiHealthy = !health.unavailable

  return <div className="sw-app">
    <header className="sw-topbar"><div className="sw-brand"><span className="sw-logo">林</span><span><strong>林研</strong><small>FINLAB · MODULE 05</small></span></div><div className="sw-module-name"><span>独立工作台</span><b>债务与偿债能力</b><small>{health.module_version || MODULE_VERSION}</small></div><button className="sw-key-status" type="button" onClick={() => { setKeyError(''); setModalOpen(true) }}><span className={health.api_configured ? 'sw-led sw-led-on' : 'sw-led'}/>{health.api_configured ? 'DeepSeek 已配置' : '配置 DeepSeek Key'}<small>设置</small></button></header>
    <main className="sw-layout">
      <aside className="sw-sidebar">
        <section className="sw-upload-card"><span className="sw-eyebrow">偿债分析 · 固定入口</span><h1>债务与偿债能力</h1><p>上传上市公司年报，系统按模块五专用选页和债务表格布局流程分析。</p>
          <form onSubmit={startAnalysis}>
            <label className={`sw-file-drop ${selectedFile ? 'sw-file-selected' : ''}`} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const dropped = event.dataTransfer.files?.[0]; if (dropped) { setSelectedFile(dropped); setUploadError('') } }}><input type="file" accept="application/pdf,.pdf" onChange={(event) => { setSelectedFile(event.target.files?.[0] || null); setUploadError('') }}/><span className="sw-upload-icon">↑</span><strong>{selectedFile ? selectedFile.name : '选择或拖入年报 PDF'}</strong><small>{selectedFile ? `${(selectedFile.size / 1024 / 1024).toFixed(1)} MB · 原件保存在本机` : '仅支持可提取文字的 PDF · 单文件最大 25 MB'}</small></label>
            <div className="sw-selected-module"><span>分析模块</span><strong>模块五 · 债务与偿债能力</strong><small>版本 {health.module_version || MODULE_VERSION}</small></div>
            {uploadError && <div className="sw-error-text" role="alert">{uploadError}</div>}
            <button className="sw-button sw-button-primary sw-start-button" disabled={busy || !selectedFile || !health.api_configured}>{busy && !activeRun ? '正在提交…' : activeRun && BUSY.has(activeRun.status) ? '任务处理中…' : '开始偿债分析'}<span>↗</span></button>
          </form>
          <div className="sw-privacy-note"><span>◇</span><p>PDF 和运行数据库保存在本任务目录；模型接收的是选中页文本与表格布局。</p></div>
        </section>

        <section className="sw-history-card"><div className="sw-history-head"><div><span className="sw-eyebrow">本机记录</span><h2>最近分析</h2></div><span>{recentRuns.length} 条</span></div>
          {recentLoading && !recentRuns.length ? <p className="sw-history-empty">读取中…</p> : recentRuns.length ? <div className="sw-history-list">{recentRuns.slice(0, 12).map((run) => {
            const active = activeRun?.id === run.id
            const status = taskStatus(run)
            return <button type="button" className={`sw-history-item ${active ? 'sw-history-active' : ''}`} key={run.id} onClick={() => openRun(run.id)}><span className="sw-pdf-badge">PDF</span><span className="sw-history-text"><strong>{run.file_name}</strong><small>{run.stage || status.text}</small></span><span className={`sw-history-status sw-history-${status.tone}`}>{run.status === 'completed' ? statusText(run.analysis_status || run.result?.analysis_status) : statusText(run.status)}</span></button>
          })}</div> : <p className="sw-history-empty">还没有模块五分析记录。</p>}
        </section>
        <section className="sw-process-hint"><span>分析路径</span><strong>选页与表格布局 → 债务事实 → 程序计算 → 专题判断 → 原文回查</strong><small>模块五保留独立提示词、查页逻辑和计算边界。</small></section>
      </aside>

      <section className="sw-content">
        <div className="sw-page-heading"><div><span className="sw-eyebrow">工作台　/　模块五</span><h2>把债务结构和资金压力拆开看。</h2><p>完整呈现年报事实、程序计算、公司专题、约束条件与未决问题。</p></div><div className={`sw-service-state ${apiHealthy ? 'sw-service-on' : 'sw-service-off'}`}><span/>{apiHealthy ? '本机服务已连接' : '本机服务未连接'}<small>127.0.0.1:8105</small></div></div>
        {pageError && <div className="sw-error-box">{pageError} 请确认使用本任务目录的启动文件。</div>}
        <RunResult run={activeRun} onOpenPage={(number) => activeRun?.id && setSourcePage({ runId: activeRun.id, number })}/>
        {!activeRun && mostRecentId && <p className="sw-auto-history-note">已找到本机历史任务。点击左侧任一记录载入结果。</p>}
      </section>
    </main>
    {modalOpen && <KeyModal onClose={() => setModalOpen(false)} onSave={saveKey} saving={savingKey} error={keyError}/>}
    {sourcePage && <SourceModal page={sourcePage} onClose={() => setSourcePage(null)}/>}
  </div>
}
