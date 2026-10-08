import { useCallback, useEffect, useRef, useState } from 'react'

const API = '/api'
const BUSY = new Set(['queued', 'running'])
const LABELS = {
  complete: '分析完整',
  partial: '部分完成',
  insufficient_material: '资料不足',
  failed: '分析失败',
  pending: '等待运行',
  running: '正在分析',
}

async function responseError(response) {
  try {
    const body = await response.json()
    return body.detail || body.error || `请求失败（${response.status}）`
  } catch {
    return `请求失败（${response.status}）`
  }
}

function display(value) {
  if (value === null || value === undefined || value === '') return '未提供 / 未计算'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function displayTime(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function pageLink(runId, page) {
  return `${API}/runs/${runId}/file#page=${page}`
}

function Evidence({ item, runId }) {
  const [source, setSource] = useState('')
  const [loading, setLoading] = useState(false)
  if (!item || typeof item !== 'object') return null
  const page = Number(item.page)
  const verification = item.verification || item.evidence_status || '待核验'
  const verified = verification === 'quote_present' || verification === 'topic_and_quote_verified' || verification === 'verified_quote'
  const inspect = async () => {
    if (!runId || !page) return
    if (source) { setSource(''); return }
    setLoading(true)
    try {
      const response = await fetch(`${API}/runs/${runId}/pages/${page}`)
      if (!response.ok) throw new Error(await responseError(response))
      const body = await response.json()
      setSource(body.text || '该页没有可提取文字。')
    } catch (error) {
      setSource(error.message || '无法读取该页。')
    } finally {
      setLoading(false)
    }
  }
  return (
    <article className="disc-evidence">
      <div className="disc-evidence-head">
        <strong>{page ? `PDF 物理第 ${page} 页` : '未提供页码'}</strong>
        <span className={`disc-evidence-state ${verified ? 'is-match' : 'is-review'}`}>{verificationLabel(verification)}</span>
        {page > 0 && runId && <a href={pageLink(runId, page)} target="_blank" rel="noreferrer">在原始 PDF 打开 ↗</a>}
        {page > 0 && runId && <button type="button" onClick={inspect} disabled={loading}>{loading ? '读取中…' : source ? '收起原文' : '查看提取原文'}</button>}
      </div>
      {item.quote && <blockquote>{item.quote}</blockquote>}
      {item.supports && <p><b>声称支持：</b>{item.supports}</p>}
      {item.reference_scope && <p><b>引用范围：</b>{item.reference_scope}</p>}
      {item.reason && <p><b>校验说明：</b>{item.reason}</p>}
      {source && <pre className="disc-source-text">{source}</pre>}
      <small>摘录文字可回查只表示文字出现在所引页，不等于审计判断、会计处理或因果解释已获证实。</small>
    </article>
  )
}

function verificationLabel(value) {
  return ({
    quote_present: '摘录文字可回查',
    verified_quote: '摘录文字可回查',
    topic_and_quote_verified: '主题与摘录可回查',
    quote_not_found: '摘录未找到',
    quote_not_found_or_page_not_read: '摘录未核对成功',
    quote_uses_ellipsis: '摘录含省略号',
    quote_too_short: '摘录过短',
    page_not_seen_by_model: '该页未被分析读取',
    invalid_page: '页码无效',
    topic_or_scope_not_verified: '主题或范围未证实',
    not_independently_verified: '未独立核实',
    linked_finding_reference_only: '仅为关联专题线索',
    direct_reference_checked: '直接引用已核对',
    partially_verified: '部分摘录可回查',
    direct_reference_unverified: '影响依据未核实',
    missing_reference: '缺少直接依据',
  })[value] || value || '待人工复核'
}

function ObjectFields({ value, exclude = [] }) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return <p className="disc-empty">未提供。</p>
  const entries = Object.entries(value).filter(([key, item]) => !exclude.includes(key) && item !== undefined)
  if (!entries.length) return <p className="disc-empty">未提供。</p>
  return <div className="disc-field-grid">{entries.map(([key, item]) => (
    <div className="disc-field" key={key}><span>{key.replaceAll('_', ' ')}</span><pre>{display(item)}</pre></div>
  ))}</div>
}

function EvidenceList({ items, runId, empty = '没有提交可回查摘录。' }) {
  if (!Array.isArray(items) || !items.length) return <p className="disc-empty">{empty}</p>
  return <div className="disc-evidence-list">{items.map((item, index) => <Evidence key={`${item.page}-${index}`} item={item} runId={runId} />)}</div>
}

function Section({ number, title, hint, children }) {
  return <section className="disc-section card">
    <div className="disc-section-title"><div><span>{number}</span><h2>{title}</h2></div>{hint && <small>{hint}</small>}</div>
    {children}
  </section>
}

function DisclosureResult({ run }) {
  if (!run?.result) return null
  const result = run.result
  const audit = result.audit_profile || {}
  const findings = Array.isArray(result.findings) ? result.findings : []
  const impacts = Array.isArray(result.impacts) ? result.impacts : []
  const coverage = Array.isArray(result.coverage) ? result.coverage : []
  const calculations = Array.isArray(result.calculations) ? result.calculations : []
  const gaps = result.run_quality?.critical_gaps || result.checkpoint?.completion_issues || []
  const partial = run.analysis_status === 'partial'
  return <div className="disc-result-stack">
    <section className={`disc-status-card card ${partial ? 'is-partial' : 'is-complete'}`}>
      <div><div className="disc-eyebrow">{run.status === 'completed' ? '运行已结束' : '任务状态'}</div><h2>{LABELS[run.analysis_status] || run.analysis_status || '状态待确认'}</h2>
        <p>{partial ? (run.error || result.partial_reason || '完整性门禁未通过，以下展示已保存的检查点。') : run.review_status === 'open_items' ? '结果已生成，但仍有来源、覆盖或口径项目需要人工复核。' : run.analysis_status === 'insufficient_material' ? '没有足够的可提取正文形成分析。' : '已完成模块六分析；模型分析仍需结合年报原文复核。'}</p>
      </div>
      <div className="disc-status-badges"><span>运行：{run.status === 'completed' ? '已结束' : run.status === 'failed' ? '失败' : '进行中'}</span><span>复核：{run.review_status === 'open_items' ? '有待复核项' : '专业判断需人工复核'}</span><span>{result.module_version || '模块版本待确认'}</span></div>
    </section>

    {gaps.length > 0 && <section className="disc-gap-card card"><h3>未完成或待处理项</h3><ul>{gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></section>}
    {result.partial_reason && !gaps.includes(result.partial_reason) && <div className="disc-reason card"><b>中断原因：</b>{result.partial_reason}</div>}

    <Section number="01" title="年报身份与审计意见" hint="意见分类必须由意见原文支持">
      <div className="disc-context-line"><b>{result.report_context?.company || result.report_context?.company_name || '公司身份待确认'}</b><span>{result.report_context?.code || result.report_context?.stock_code || ''}</span><span>{result.report_context?.period || result.report_context?.report_year || ''}</span></div>
      <p className="disc-summary">{result.executive_summary || '当前没有形成完整摘要。'}</p>
      <div className="disc-opinion-pill"><span>审计意见</span><strong>{audit.opinion_type || '未识别'}</strong><em>{verificationLabel(audit.opinion_verification_status || audit.evidence_status)}</em></div>
      <ObjectFields value={audit} exclude={['evidence']} />
      <EvidenceList items={audit.evidence} runId={run.id} empty="没有提供审计意见的原文摘录；不能依据事务所或签字信息替代意见证据。" />
      {result.report_context?.identity_evidence && <><h3 className="disc-subtitle">报告身份原文</h3><Evidence item={result.report_context.identity_evidence} runId={run.id} /></>}
    </Section>

    <Section number="02" title={`实际发现的专题 · ${findings.length} 项`} hint="按结果原顺序完整展示，不预设专题数量">
      {findings.length ? <div className="disc-findings">{findings.map((finding, index) => <article className="disc-finding" key={finding.finding_id || index}>
        <div className="disc-finding-head"><div><span>专题 {String(index + 1).padStart(2, '0')}</span><h3>{finding.title || finding.topic || '未命名专题'}</h3></div><em className={finding.evidence_status === 'verified_quote' ? 'is-match' : 'is-review'}>{finding.importance || verificationLabel(finding.evidence_status)}</em></div>
        <div className="disc-context-line"><span>{finding.topic || '专题类型未提供'}</span>{Array.isArray(finding.related_modules) && <span>关联模块：{finding.related_modules.join('、') || '未列出'}</span>}</div>
        <div className="disc-subfields">
          {['disclosed_facts', 'analysis', 'known_effects', 'management_explanation', 'audit_response', 'uncertainty'].map((key) => finding[key] !== undefined && <div key={key}><b>{({disclosed_facts:'年报披露事实',analysis:'分析解读',known_effects:'已知影响',management_explanation:'管理层解释',audit_response:'审计应对',uncertainty:'不确定性与限制'})[key]}</b><p>{display(finding[key])}</p></div>)}
        </div>
        <EvidenceList items={finding.evidence} runId={run.id} />
      </article>)}</div> : <p className="disc-empty">当前检查点没有已提交专题。若运行部分完成，请结合上方中断原因和已读页判断。</p>}
    </Section>

    <Section number="03" title={`对其他模块的影响 · ${impacts.length} 条`} hint="影响线索用于后续核查，不能直接替代目标模块结论">
      {impacts.length ? <div className="disc-impact-list">{impacts.map((impact, index) => <article className="disc-impact" key={impact.impact_id || index}>
        <div className="disc-impact-head"><h3>{impact.target_module || '目标模块未指定'}</h3><em>{verificationLabel(impact.evidence_status)}</em></div>
        <ObjectFields value={impact} exclude={['evidence_refs']} />
        <EvidenceList items={impact.evidence_refs} runId={run.id} empty="没有直接影响证据；关联专题线索不能证明影响项目中的金额或方向。" />
      </article>)}</div> : <p className="disc-empty">没有提交跨模块复核线索；这不代表其他模块无需自行核验。</p>}
    </Section>

    <Section number="04" title={`检查范围 · ${coverage.length} 项`} hint="区分已发现、明确无披露、检索未命中和未检查">
      {coverage.length ? <div className="disc-coverage-list">{coverage.map((item, index) => <article className="disc-coverage" key={`${item.topic}-${index}`}>
        <div className="disc-impact-head"><h3>{item.topic || '未命名范围'}</h3><span className={`disc-coverage-state state-${item.status || 'not_checked'}`}>{coverageLabel(item.status)}</span></div>
        <ObjectFields value={item} exclude={['evidence']} />
        <EvidenceList items={item.evidence} runId={run.id} empty="没有主题相符的原文证据。" />
      </article>)}</div> : <p className="disc-empty">没有形成可展示的覆盖清单；不能据此推断已检查过这些主题。</p>}
    </Section>

    <Section number="05" title={`程序计算 · ${calculations.length} 项`} hint="保留空值；空值不代表 0">
      {calculations.length ? <div className="disc-calculation-list">{calculations.map((item, index) => <article className="disc-calculation" key={item.name || index}>
        <h3>{item.name || item.metric_name || `计算 ${index + 1}`}</h3><ObjectFields value={item} exclude={[]} />
        <EvidenceList items={[item.earlier_source, item.later_source].filter(Boolean)} runId={run.id} empty="计算没有来源摘录。" />
      </article>)}</div> : <p className="disc-empty">本次没有完成金额计算。空白不表示金额为零。</p>}
    </Section>

    <Section number="06" title="证据质量与运行边界" hint="摘录匹配不等于专业结论已证实">
      {result.run_quality && <ObjectFields value={result.run_quality} exclude={['critical_gaps']} />}
      {Array.isArray(result.quality_flags) && result.quality_flags.length > 0 && <div className="disc-gap-card"><h3>口径冲突提示</h3><ul>{result.quality_flags.map((item, index) => <li key={index}>{display(item)}</li>)}</ul></div>}
      <h3 className="disc-subtitle">分析限制</h3>
      {result.limitations?.length ? <ul className="disc-limitation-list">{result.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p className="disc-empty">未提交额外限制说明。</p>}
      <h3 className="disc-subtitle">与其他模块合读</h3><p className="disc-guide">{result.reading_guide || '请将本模块线索回到年报原文核对，并由对应专业模块验证财务影响。'}</p>
    </Section>

    <div className="disc-final-actions"><a href={`${API}/runs/${run.id}/file`} target="_blank" rel="noreferrer">打开本次上传的原始年报 ↗</a><span>程序只核对摘录文字和结构门槛；专业判断仍需人工复核。</span></div>
  </div>
}

function coverageLabel(status) {
  return ({
    confirmed_present: '发现披露',
    explicit_no_disclosure: '原文明示无事项',
    searched_no_hit: '检索未命中',
    not_checked: '未检查 / 证据不足',
  })[status] || status || '未说明'
}

function DeepSeekModal({ onClose, onSave, busy, error }) {
  const [key, setKey] = useState('')
  const [visible, setVisible] = useState(false)
  return <div className="disc-modal-backdrop" role="presentation">
    <form className="disc-key-modal" onSubmit={(event) => { event.preventDefault(); onSave(key) }}>
      <span className="disc-eyebrow">本机配置</span><h2>连接 DeepSeek</h2>
      <p>密钥只保存到本工作台目录的 .env 文件，不会显示在分析页面或运行记录中。</p>
      <label htmlFor="deepseek-key">DeepSeek API Key</label>
      <div className="disc-key-input"><input id="deepseek-key" type={visible ? 'text' : 'password'} autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} placeholder="粘贴 API Key"/><button type="button" onClick={() => setVisible((value) => !value)}>{visible ? '隐藏' : '显示'}</button></div>
      {error && <div className="disc-inline-error" role="alert">{error}</div>}
      <div className="disc-key-actions"><button type="button" className="disc-quiet" onClick={onClose}>稍后设置</button><button className="disc-primary" type="submit" disabled={busy || !key.trim()}>{busy ? '正在保存…' : '保存到本机'}</button></div>
    </form>
  </div>
}

function DisclosureWorkspace() {
  const inputRef = useRef(null)
  const [health, setHealth] = useState({ api_configured: false, unavailable: false })
  const [recent, setRecent] = useState([])
  const [activeRun, setActiveRun] = useState(null)
  const [conversation, setConversation] = useState([])
  const [file, setFile] = useState(null)
  const [question, setQuestion] = useState('')
  const [pageError, setPageError] = useState('')
  const [keyModal, setKeyModal] = useState(false)
  const [keyError, setKeyError] = useState('')
  const [savingKey, setSavingKey] = useState(false)
  const [loading, setLoading] = useState(false)
  const [asking, setAsking] = useState(false)

  const loadHealth = useCallback(async () => {
    try {
      const response = await fetch(`${API}/health`)
      if (!response.ok) throw new Error('无法读取工作台状态。')
      const data = await response.json()
      setHealth({ ...data, unavailable: false })
      return data
    } catch {
      setHealth((current) => ({ ...current, unavailable: true }))
      return null
    }
  }, [])

  const loadRecent = useCallback(async () => {
    try {
      const response = await fetch(`${API}/runs`)
      if (!response.ok) return
      const data = await response.json()
      setRecent(data.runs || [])
    } catch { /* Keep the current page usable when history is unavailable. */ }
  }, [])

  const loadRun = useCallback(async (id) => {
    const response = await fetch(`${API}/runs/${id}`)
    if (!response.ok) throw new Error(await responseError(response))
    const run = await response.json()
    setActiveRun(run)
    return run
  }, [])

  const loadConversation = useCallback(async (id) => {
    const response = await fetch(`${API}/runs/${id}/conversation`)
    if (!response.ok) return
    const body = await response.json()
    setConversation(body.turns || [])
  }, [])

  useEffect(() => {
    let alive = true
    loadHealth().then((data) => { if (alive && data && !data.api_configured) setKeyModal(true) })
    loadRecent()
    return () => { alive = false }
  }, [loadHealth, loadRecent])

  useEffect(() => {
    if (!activeRun?.id) return undefined
    const hasPendingTurn = conversation.some((turn) => turn.status === 'queued' || turn.status === 'running')
    if (!BUSY.has(activeRun.status) && !hasPendingTurn) return undefined
    const timer = window.setInterval(async () => {
      try {
        const run = await loadRun(activeRun.id)
        await loadConversation(activeRun.id)
        if (!BUSY.has(run.status)) await loadRecent()
      } catch { /* The last saved state remains on screen. */ }
    }, 1400)
    return () => window.clearInterval(timer)
  }, [activeRun?.id, activeRun?.status, conversation, loadConversation, loadRecent, loadRun])

  const submitReport = async (event) => {
    event.preventDefault()
    if (!file) return
    setPageError('')
    setLoading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('analysis_module', 'disclosure')
      const response = await fetch(`${API}/runs`, { method: 'POST', body: form })
      if (!response.ok) throw new Error(await responseError(response))
      const run = await response.json()
      setActiveRun(run)
      setConversation([])
      setFile(null)
      inputRef.current.value = ''
      await loadRecent()
    } catch (error) {
      setPageError(error.message || '无法提交年报，请确认本机工作台已启动。')
    } finally {
      setLoading(false)
    }
  }

  const openRun = async (id) => {
    setPageError('')
    try {
      await loadRun(id)
      await loadConversation(id)
      setFile(null)
    } catch (error) {
      setPageError(error.message || '无法读取这条历史记录。')
    }
  }

  const askQuestion = async (event) => {
    event.preventDefault()
    if (!activeRun?.id || !question.trim()) return
    setAsking(true)
    setPageError('')
    try {
      const response = await fetch(`${API}/runs/${activeRun.id}/conversation`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.trim() }),
      })
      if (!response.ok) throw new Error(await responseError(response))
      const turn = await response.json()
      setConversation((current) => [...current.filter((item) => item.id !== turn.id), turn])
      setQuestion('')
    } catch (error) {
      setPageError(error.message || '追问暂时无法提交。')
    } finally {
      setAsking(false)
    }
  }

  const saveApiKey = async (value) => {
    setSavingKey(true)
    setKeyError('')
    try {
      const response = await fetch(`${API}/settings/deepseek-key`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: value.trim() }),
      })
      if (!response.ok) throw new Error(await responseError(response))
      setHealth((current) => ({ ...current, api_configured: true, unavailable: false }))
      setKeyModal(false)
    } catch (error) {
      setKeyError(error.message || '保存失败，请检查本机目录权限。')
    } finally {
      setSavingKey(false)
    }
  }

  const newAnalysis = () => { setActiveRun(null); setConversation([]); setQuestion(''); setPageError('') }
  const busy = loading || BUSY.has(activeRun?.status)
  const canAsk = activeRun?.status === 'completed' && ['complete', 'partial'].includes(activeRun?.analysis_status) && activeRun?.result

  return <div className="disc-app">
    <header className="disc-topbar">
      <a className="disc-brand" href="#top"><span className="disc-brand-mark">林</span><span><b>林研</b><small>FINLAB · V3.6.2</small></span></a>
      <div className="disc-top-title"><span>模块六</span><b>审计、披露与特殊事项</b></div>
      <button className="disc-model-button" type="button" onClick={() => { setKeyError(''); setKeyModal(true) }}><i className={health.api_configured ? 'connected' : ''}/>{health.api_configured ? 'DeepSeek 已配置' : '配置 DeepSeek Key'}<small>设置</small></button>
    </header>

    <main id="top" className="disc-layout">
      <aside className="disc-sidebar">
        <section className="disc-upload-card card">
          <div className="disc-eyebrow">模块六独立工作台 · {health.module_version || 'V3.6.2'}</div>
          <h1>先读审计意见，<span>再追披露细节。</span></h1>
          <p>仅运行模块六，逐项展示审计意见、实际发现、跨模块影响、检查范围和证据限制。</p>
          <form onSubmit={submitReport}>
            <input ref={inputRef} type="file" accept="application/pdf,.pdf" onChange={(event) => setFile(event.target.files?.[0] || null)} />
            <button className={`disc-dropzone ${file ? 'selected' : ''}`} type="button" onClick={() => inputRef.current?.click()}>
              <span>{file ? '✓' : '↑'}</span><b>{file?.name || '选择一份年报 PDF'}</b><small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 文件留在本机` : '支持带文字层 PDF，单份最大 25 MB'}</small>
            </button>
            {pageError && <div className="disc-inline-error" role="alert">{pageError}</div>}
            {health.unavailable && <div className="disc-inline-error" role="alert">无法连接 8106 工作台服务；请运行目录中的启动文件。</div>}
            <button className="disc-primary" type="submit" disabled={!file || busy || !health.api_configured}>{busy ? '正在分析模块六…' : '开始模块六分析 ↗'}</button>
          </form>
          <div className="disc-private-note">PDF、历史和日志保存在当前 V3.6.2 目录；仅必要页面文本发送至 DeepSeek。</div>
        </section>

        <section className="disc-history-card card">
          <div className="disc-history-heading"><b>本工作台的分析记录</b><small>{recent.length} 条</small></div>
          {recent.length ? recent.slice(0, 12).map((item) => <button className={`disc-history-item ${activeRun?.id === item.id ? 'selected' : ''}`} key={item.id} type="button" onClick={() => openRun(item.id)}>
            <span className="disc-pdf-icon">PDF</span><span className="disc-history-copy"><b>{item.file_name}</b><small>{displayTime(item.created_at)} · 模块六</small></span><em className={`state-${item.analysis_status}`}>{LABELS[item.analysis_status] || (BUSY.has(item.status) ? '进行中' : item.status)}</em>
          </button>) : <p className="disc-empty">还没有本工作台的分析记录。</p>}
          {activeRun && <button className="disc-quiet disc-new-run" type="button" onClick={newAnalysis}>新建分析</button>}
        </section>
      </aside>

      <section className="disc-main">
        <div className="disc-breadcrumb">工作台　/　模块六　/　披露与风险</div>
        {activeRun && BUSY.has(activeRun.status) && <section className="disc-progress card" aria-live="polite">
          <div className="disc-progress-heading"><i className="disc-spinner"/><div><b>{activeRun.stage || '正在准备分析'}</b><small>{activeRun.file_name}</small></div></div>
          {activeRun.steps?.length ? <ol>{activeRun.steps.map((step) => <li key={step.ordinal} className={`step-${step.status}`}><span/><div><b>{step.name}</b><small>{step.detail || step.status}</small></div></li>)}</ol> : <p>正在读取 PDF、定位审计报告，并按需查阅附注。</p>}
          {activeRun.read_pages?.length > 0 && <small>已读取 PDF 第 {activeRun.read_pages.join('、')} 页</small>}
        </section>}
        {activeRun && <DisclosureResult run={activeRun} />}
        {activeRun?.status === 'failed' && !activeRun.result && <section className="disc-gap-card card"><h2>本次运行失败</h2><p>{activeRun.error || '后台未能完成分析。'}</p></section>}
        {activeRun?.result && <section className="disc-chat card">
          <div className="disc-section-title"><div><span>↳</span><h2>追问本次年报</h2></div><small>每轮会带入模块六指导、当前结果和新读取原文</small></div>
          <p>可以追问审计意见、某项披露、证据页码或跨模块影响。系统会在本份年报中搜索并读取相关页面。</p>
          <div className="disc-chat-list">{conversation.map((turn) => <article key={turn.id}>
            <div className="disc-chat-question">{turn.question}</div>
            {turn.status === 'queued' || turn.status === 'running' ? <div className="disc-chat-pending"><i className="disc-spinner"/>正在搜索年报并组织回答…</div> : turn.status === 'failed' ? <div className="disc-chat-failed">{turn.error || '追问未完成。'}</div> : <div className="disc-chat-answer">{turn.answer}</div>}
          </article>)}</div>
          <form className="disc-question-form" onSubmit={askQuestion}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：审计意见原文具体怎么表述？对应哪一页？" maxLength={1200} rows={3} disabled={!canAsk || asking}/><div><small>{question.length}/1200</small><button className="disc-primary" type="submit" disabled={!canAsk || asking || !question.trim()}>{asking ? '正在提交…' : '提交追问'}</button></div></form>
          {!canAsk && <small className="disc-chat-hint">首次分析保存为完整或部分结果后，可继续追问。</small>}
        </section>}
        {!activeRun && <section className="disc-welcome card"><span>MODULE 06 · DISCLOSURE</span><h2>从审计意见与原文证据开始。</h2><p>上传年报后，模块六会保留自己的检查步骤，完整呈现实际发现和未覆盖范围。没有形成完整结论时，会展示已保存的检查点与中断原因。</p><div><b>审计意见</b><i>→</i><b>实际专题</b><i>→</i><b>模块影响</b><i>→</i><b>范围与限制</b></div></section>}
        <footer className="disc-footer">林研 · 模块六工作台 <span>摘录核对不等于专业结论证实；投资与会计判断请由专业人员复核。</span></footer>
      </section>
    </main>
    {keyModal && <DeepSeekModal onClose={() => setKeyModal(false)} onSave={saveApiKey} busy={savingKey} error={keyError}/>}
  </div>
}

export default DisclosureWorkspace
