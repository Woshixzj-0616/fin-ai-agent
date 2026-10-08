import { useCallback, useEffect, useMemo, useState } from 'react'

const API = '/api'
const OUTCOMES = ['全部状态', '发现错误', '证据不足', '已核对一致', '超出范围']

async function readJson(response) {
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || '请求没有完成，请稍后重试。')
  return data
}

function PageLink({ runId, evidence, documents }) {
  const doc = documents.find((item) => item.id === evidence.document_id)
  if (!doc) return null
  const role = evidence.evidence_role === 'analyst' ? '研报' : '财报'
  const url = `${API}/reviews/${runId}/documents/${doc.id}/file#page=${evidence.page_number}`
  return (
    <a className="review-page-link" href={url} target="_blank" rel="noreferrer">
      <span>{role} · PDF 第 {evidence.page_number} 页</span>
      {evidence.quote && <blockquote>{evidence.quote}</blockquote>}
      {!evidence.quote_verified && <small>摘录与原文未匹配，请人工核对</small>}
    </a>
  )
}

function QuestionBox({ runId, claim }) {
  const [open, setOpen] = useState(false)
  const [question, setQuestion] = useState('')
  const [items, setItems] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const hasPending = items.some((item) => item.status === 'queued' || item.status === 'running')

  const refresh = useCallback(async () => {
    const data = await readJson(await fetch(`${API}/reviews/${runId}/claims/${claim.id}/questions`))
    setItems(data.questions || [])
  }, [runId, claim.id])

  useEffect(() => {
    if (open) refresh().catch(() => {})
  }, [open, refresh])

  useEffect(() => {
    if (!open || !hasPending) return undefined
    const timer = window.setInterval(() => refresh().catch(() => {}), 1300)
    return () => window.clearInterval(timer)
  }, [open, hasPending, refresh])

  async function submit(event) {
    event.preventDefault()
    if (!question.trim() || busy) return
    setBusy(true); setError('')
    try {
      await readJson(await fetch(`${API}/reviews/${runId}/claims/${claim.id}/questions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }),
      }))
      setQuestion('')
      await refresh()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  return (
    <div className="claim-question">
      <button type="button" className="review-link-button" onClick={() => setOpen(!open)}>{open ? '收起追问' : '追问这条结果'}</button>
      {open && <>
        <form onSubmit={submit} className="question-form">
          <input value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={1200} placeholder="例如：为什么判为冲突？" />
          <button disabled={busy || !question.trim()}>{busy ? '发送中…' : '提问'}</button>
        </form>
        {error && <p className="review-error">{error}</p>}
        {items.map((item) => <div className="review-answer" key={item.id}><b>{item.question}</b><p>{item.status === 'queued' || item.status === 'running' ? '正在结合已保存的原文回答…' : item.answer || item.status}</p>{item.token_usage && <small>本次调用：输入 {item.token_usage.prompt_tokens || 0} / 输出 {item.token_usage.completion_tokens || 0} tokens</small>}</div>)}
      </>}
    </div>
  )
}

function ClaimCard({ run, claim }) {
  const label = claim.outcome || (claim.processing_status === 'running' ? '处理中' : claim.processing_status === 'failed' ? '失败' : '待处理')
  const category = claim.category === 'financial_fact' ? '财务事实' : claim.category === 'prediction' ? '预测' : claim.category === 'investment_opinion' ? '投资观点' : '其他表述'
  return (
    <article className="claim-card">
      <div className="claim-topline"><span className={`outcome-pill ${label === '发现错误' ? 'is-error' : label === '证据不足' || label === '失败' ? 'is-review' : label === '已核对一致' ? 'is-ok' : ''}`}>{label}</span><span>{category} · 研报 PDF 第 {claim.source_page} 页</span></div>
      <div className="claim-two-col">
        <section><h4>研报原句</h4><blockquote className="claim-quote">{claim.source_quote}</blockquote><p className="claim-extracted">核查内容：{claim.claim_text}</p></section>
        <section><h4>核查说明</h4><p className="claim-rationale">{claim.rationale || claim.error || '等待处理。'}</p>
          {claim.calculation && <div className="claim-calculation"><b>{claim.trace?.some((item) => ['calculate_financial_change', 'convert_financial_amount'].includes(item.tool)) ? '程序计算' : '计算说明（未由程序复算）'}</b><span>{claim.calculation}</span></div>}
          {claim.suggestion && <div className="claim-suggestion"><b>修改建议</b><span>{claim.suggestion}</span></div>}
        </section>
      </div>
      <div className="claim-evidence">{claim.evidence?.map((evidence) => <PageLink key={`${evidence.document_id}-${evidence.page_number}-${evidence.id}`} runId={run.id} evidence={evidence} documents={run.documents} />)}</div>
      {claim.processing_status === 'failed' && <p className="review-error">{claim.error}</p>}
      <QuestionBox runId={run.id} claim={claim} />
    </article>
  )
}

export default function ReviewWorkspace() {
  const [health, setHealth] = useState({ api_configured: false })
  const [keyOpen, setKeyOpen] = useState(false)
  const [key, setKey] = useState('')
  const [keyError, setKeyError] = useState('')
  const [savingKey, setSavingKey] = useState(false)
  const [company, setCompany] = useState('')
  const [year, setYear] = useState('2024')
  const [analystDate, setAnalystDate] = useState('')
  const [financialDate, setFinancialDate] = useState('')
  const [financialVersion, setFinancialVersion] = useState('')
  const [versionConfirmed, setVersionConfirmed] = useState(false)
  const [analystFile, setAnalystFile] = useState(null)
  const [financialFile, setFinancialFile] = useState(null)
  const [run, setRun] = useState(null)
  const [recent, setRecent] = useState([])
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState('全部状态')
  const [error, setError] = useState('')

  const loadRun = useCallback(async (id) => {
    const data = await readJson(await fetch(`${API}/reviews/${id}`))
    setRun(data)
    return data
  }, [])

  const loadRecent = useCallback(async () => {
    const data = await readJson(await fetch(`${API}/reviews`))
    setRecent(data.reviews || [])
  }, [])

  useEffect(() => {
    fetch(`${API}/health`).then((r) => readJson(r)).then((data) => { setHealth(data); if (!data.api_configured) setKeyOpen(true) }).catch(() => {})
    loadRecent().catch(() => {})
  }, [loadRecent])

  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return undefined
    const timer = window.setInterval(() => {
      loadRun(run.id).then(loadRecent).catch(() => {})
    }, 1400)
    return () => window.clearInterval(timer)
  }, [run?.id, run?.status, loadRun, loadRecent])

  const visibleClaims = useMemo(() => {
    if (!run) return []
    return filter === '全部状态' ? run.claims : run.claims.filter((item) => item.outcome === filter || (!item.outcome && filter === '证据不足' && item.processing_status === 'failed'))
  }, [run, filter])

  async function saveKey(event) {
    event.preventDefault(); setSavingKey(true); setKeyError('')
    try {
      await readJson(await fetch(`${API}/settings/deepseek-key`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: key }) }))
      setHealth((old) => ({ ...old, api_configured: true })); setKey(''); setKeyOpen(false)
    } catch (err) { setKeyError(err.message) } finally { setSavingKey(false) }
  }

  async function submit(event) {
    event.preventDefault()
    if (!analystFile || !financialFile || busy) return
    setBusy(true); setError('')
    try {
      const body = new FormData()
      body.set('analyst_report', analystFile)
      body.set('financial_report', financialFile)
      body.set('company', company); body.set('report_year', year)
      body.set('analyst_publish_date', analystDate); body.set('financial_publish_date', financialDate)
      body.set('financial_version', financialVersion || '用户确认上传版本')
      body.set('version_temporally_confirmed', String(versionConfirmed))
      const data = await readJson(await fetch(`${API}/reviews`, { method: 'POST', body }))
      setRun(data); await loadRecent()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  async function continueRun() {
    if (!run) return
    setError('')
    try { await readJson(await fetch(`${API}/reviews/${run.id}/continue`, { method: 'POST' })); await loadRun(run.id) }
    catch (err) { setError(err.message) }
  }

  function selectFile(role, file) {
    if (!file) return
    if (role === 'analyst') setAnalystFile(file)
    else setFinancialFile(file)
  }

  return (
    <div className="review-shell">
      <header className="review-header">
        <div><span className="review-kicker">DOCUMENT REVIEW · V3.0.1</span><h1>研报核查工作台</h1><p>把分析师说法逐条对照原始财报，打开出处再判断。</p></div>
        <button className="review-key-button" onClick={() => setKeyOpen(true)}><i className={health.api_configured ? 'connected' : ''} />DeepSeek {health.api_configured ? '已连接' : '配置 Key'}</button>
      </header>

      <main className="review-layout">
        <aside className="review-sidebar">
          <section className="review-panel">
            <div className="review-panel-heading"><span>新建核查</span><small>两份 PDF</small></div>
            <form onSubmit={submit} className="review-form">
              <label>公司名称<input required value={company} onChange={(e) => setCompany(e.target.value)} placeholder="例如：宁德时代" /></label>
              <label>报告年度<input required inputMode="numeric" pattern="[0-9]{4}" maxLength={4} value={year} onChange={(e) => setYear(e.target.value)} /></label>
              <label className="file-picker">分析师研报 PDF<input type="file" accept="application/pdf,.pdf" onChange={(e) => selectFile('analyst', e.target.files?.[0])} /><span>{analystFile?.name || '选择原始研报 PDF'}</span></label>
              <label className="file-picker">对应年度财报 PDF<input type="file" accept="application/pdf,.pdf" onChange={(e) => selectFile('financial', e.target.files?.[0])} /><span>{financialFile?.name || '选择年报 PDF'}</span></label>
              <div className="review-date-grid">
                <label>研报发表日期<input type="date" value={analystDate} onChange={(e) => setAnalystDate(e.target.value)} /></label>
                <label>年报披露日期<input type="date" value={financialDate} onChange={(e) => setFinancialDate(e.target.value)} /></label>
              </div>
              <label>年报版本说明<input value={financialVersion} onChange={(e) => setFinancialVersion(e.target.value)} placeholder="例如：2024 年原始年报" /></label>
              <label className="review-checkbox"><input type="checkbox" checked={versionConfirmed} onChange={(e) => setVersionConfirmed(e.target.checked)} /><span>我已核对两份材料属于同一公司与年度，并确认年报版本适用于研报发表时点。日期留空时，确定性结论会降为待复核。</span></label>
              <button className="review-submit" disabled={!health.api_configured || !analystFile || !financialFile || !company.trim() || !/^\d{4}$/.test(year) || busy}>{busy ? '正在提交…' : '开始逐条核查'}<span>↗</span></button>
            </form>
            {error && <p className="review-error">{error}</p>}
            <p className="review-privacy">PDF 保存在本机；提取出的相关文字片段会发送给 DeepSeek。扫描件 OCR 当前不支持。</p>
          </section>
          {recent.length > 0 && <section className="review-panel review-history"><div className="review-panel-heading"><span>最近任务</span><small>{recent.length}</small></div>{recent.map((item) => <button key={item.id} className={`history-item ${run?.id === item.id ? 'selected' : ''}`} onClick={() => loadRun(item.id).catch((err) => setError(err.message))}><b>{item.metadata?.company || '未命名公司'} · {item.metadata?.report_year || ''}</b><span>{item.stage}</span></button>)}</section>}
        </aside>

        <section className="review-results">
          {!run ? <div className="review-empty"><span>01 / 02</span><h2>研报原句 · 财报依据</h2><p>上传一份研报和对应年报。每条结论都保留双方页码；材料日期或版本不明确时，系统会提示待复核。</p><div className="review-flow"><b>上传材料</b><i>→</i><b>提取说法</b><i>→</i><b>核对证据</b><i>→</i><b>复核结果</b></div></div> : <>
            <div className="review-result-heading"><div><span className="review-kicker">{run.status === 'running' || run.status === 'queued' ? '正在处理' : '任务结果'}</span><h2>{run.metadata.company} · {run.metadata.report_year} 年度核查</h2><p>{run.stage}</p></div>{(run.status === 'failed' || run.stage?.includes('可继续') || run.claims?.some((item) => ['pending', 'failed'].includes(item.processing_status))) && <button className="review-continue" onClick={continueRun}>继续处理</button>}</div>
            <div className="review-documents">{run.documents.map((doc) => <div key={doc.id}><span>{doc.role === 'analyst' ? '研报' : '财报'}</span><b>{doc.file_name}</b><small>{doc.page_count ? `${doc.page_count} 页 · 可读 ${doc.readable_pages} 页` : doc.extraction_status}</small></div>)}</div>
            {(run.status === 'running' || run.status === 'queued') && <div className="review-progress"><i /><span>{run.stage}…</span></div>}
            {run.error && <p className="review-error">{run.error}</p>}
            <div className="review-counts">{Object.entries(run.counts || {}).filter(([name]) => name !== '处理中' && name !== '待处理').map(([name, count]) => <div key={name}><b>{count}</b><span>{name}</span></div>)}</div>
            {run.usage && <p className="review-usage">已记录 tokens：输入 {run.usage.prompt_tokens || 0}，输出 {run.usage.completion_tokens || 0}。核查项用量已记录 {run.usage.claims_with_recorded_usage || 0}/{run.usage.claim_count || 0}，追问已记录 {run.usage.questions_with_recorded_usage || 0}/{run.usage.question_count || 0}。{(run.usage.claims_with_recorded_usage || 0) < (run.usage.claim_count || 0) && '有未完成或旧结果缺少调用用量的条目，当前统计不完整。'}</p>}
            {run.stage?.includes('仅处理前') && <p className="review-limitation">{run.stage}。尚未处理的页面不会计入核查结果。</p>}
            <div className="review-filter-row"><h3>逐条核查 <small>{run.claims?.length || 0} 条</small></h3><select value={filter} onChange={(e) => setFilter(e.target.value)}>{OUTCOMES.map((name) => <option key={name}>{name}</option>)}</select></div>
            <div className="claim-list">{visibleClaims.map((claim) => <ClaimCard run={run} claim={claim} key={claim.id} />)}{!visibleClaims.length && <div className="review-no-claims">{run.status === 'running' ? '研报还在读取，候选说法出现后会显示在这里。' : '当前筛选下没有核查项。'}</div>}</div>
            <p className="review-footnote">核查覆盖的是页面中逐条列出的主张，不代表整份研报都已核实。数字摘录、口径和财务判断仍应回到原 PDF 人工复核。</p>
          </>}
        </section>
      </main>

      {keyOpen && <div className="review-modal-backdrop"><form className="review-key-modal" onSubmit={saveKey}><button type="button" className="review-modal-close" onClick={() => setKeyOpen(false)}>×</button><span className="review-kicker">LOCAL MODEL SETUP</span><h2>连接 DeepSeek</h2><p>Key 只保存在当前 V3 工作目录的本机配置中，不会发送到 Git。</p><label>DeepSeek API Key<input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={health.api_configured ? '已配置；输入新 Key 可替换' : '粘贴 API Key'} autoComplete="off" /></label>{keyError && <p className="review-error">{keyError}</p>}<button className="review-submit" disabled={!key.trim() || savingKey}>{savingKey ? '保存中…' : '保存并连接'}</button></form></div>}
    </div>
  )
}
