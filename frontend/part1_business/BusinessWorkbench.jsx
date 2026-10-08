import { useCallback, useEffect, useState } from 'react'
import {
  BusinessResultPanel,
  ConversationPanel,
  DeepSeekKeyModal,
  FailedPanel,
  RunProgress,
  apiError,
  formatTime,
} from './V2Workbench.jsx'

const API = '/api'
const BUSY = new Set(['queued', 'running'])

function resultAssessment(run) {
  if (!run) return null
  if (run.status === 'failed') return { run: '运行失败', completion: '未完成', review: '查看失败原因' }
  if (BUSY.has(run.status)) return { run: '正在运行', completion: run.result ? '已有阶段性结果' : '等待分析结果', review: '' }

  const result = run.result || {}
  const checks = Array.isArray(result.coverage_checks) ? result.coverage_checks : []
  const supported = checks.filter((item) => item.status === 'supported').length
  const reviewItems = Array.isArray(result.interpretation_review_items) ? result.interpretation_review_items : []
  const contentArrays = [
    'business_flow', 'industry_context', 'strategy_competitiveness', 'revenue_totals',
    'revenue_segments', 'growth_drivers', 'topics', 'dependencies', 'major_changes',
    'risk_factors', 'industry_metrics', 'follow_up_checks',
  ]
  const hasContent = Boolean(result.business_summary || result.company || contentArrays.some((key) => result[key]?.length))
  const completion = !hasContent
    ? '资料不足'
    : checks.length >= 6 && supported === checks.length
      ? '分析完整 · 6 类均有可回查出处'
      : `部分完成 · ${supported}/${checks.length || 6} 类有可回查出处`
  const reviewRequired = reviewItems.length > 0
    || (result.analysis_review_status && result.analysis_review_status !== '六类内容均有可回查出处')
    || checks.some((item) => ['needs_review', 'candidate_only', 'search_miss', 'partial', 'not_generated'].includes(item.status))
  return {
    run: '运行结束',
    completion,
    review: reviewRequired ? `待复核${reviewItems.length ? ` · ${reviewItems.length} 条未映射解释` : ''}` : '未标记待复核项',
  }
}

function StatusSummary({ run }) {
  const assessment = resultAssessment(run)
  if (!assessment) return null
  return (
    <section className={`module-status-summary card ${run.status === 'failed' ? 'module-status-failed' : ''}`} aria-live="polite">
      <div className="module-status-heading"><strong>本次任务状态</strong><span>{run.file_name}</span></div>
      <div className="module-status-chips">
        <span className={BUSY.has(run.status) ? 'status-chip status-running' : run.status === 'failed' ? 'status-chip status-failed' : 'status-chip'}>{assessment.run}</span>
        <span className={`status-chip ${assessment.completion.includes('资料不足') ? 'status-insufficient' : assessment.completion.includes('部分完成') ? 'status-partial' : ''}`}>{assessment.completion}</span>
        {assessment.review && <span className={`status-chip ${assessment.review.startsWith('待复核') ? 'status-review' : ''}`}>{assessment.review}</span>}
      </div>
      <p>“运行结束”只表示任务流程停止；出处文字匹配只用于定位原文，不等于表格口径、经营解释或因果判断已被证实。空值按未披露处理，不当成 0。</p>
    </section>
  )
}

function UploadCard({ selectedFile, onChoose, onRun, busy, configured, onNew, error, dragActive, onDrag, onDrop }) {
  return (
    <section className="upload-card card">
      <div className="card-heading">
        <div><div className="eyebrow">从一份年报开始</div><h2>模块一分析</h2></div>
        <button className="quiet-button" type="button" onClick={onNew} disabled={busy}>新建分析</button>
      </div>
      <label
        className={`drop-zone ${selectedFile ? 'has-file' : ''} ${dragActive ? 'is-dragging' : ''}`}
        htmlFor="business-report-pdf"
        onDragOver={(event) => { event.preventDefault(); onDrag(true) }}
        onDragLeave={() => onDrag(false)}
        onDrop={onDrop}
      >
        <input id="business-report-pdf" className="file-input" type="file" accept="application/pdf,.pdf" onChange={onChoose} disabled={busy} />
        <span className={`upload-icon ${selectedFile ? 'upload-icon-ready' : ''}`} aria-hidden="true">{selectedFile ? '✓' : '↑'}</span>
        <strong className="drop-title">{selectedFile ? selectedFile.name : '拖入 PDF 文件，或点击选择'}</strong>
        <span className="drop-subtitle">只运行模块一 · 支持可提取文字的年报 PDF · 单文件最大 25 MB</span>
      </label>
      {!configured && <div className="config-notice"><span className="notice-dot" />开始分析前，请先配置 DeepSeek API Key。</div>}
      <button className="primary-button" type="button" disabled={busy || !selectedFile || !configured} onClick={onRun}>
        {busy ? <><span className="button-spinner" />正在分析</> : <>分析业务与经营背景 <span>↗</span></>}
      </button>
      {error && <div className="inline-error" role="alert">{error}</div>}
      <div className="privacy-note"><span aria-hidden="true">♢</span><span>PDF 保存在本机任务目录；仅分析选取的年报文字会发送给 DeepSeek。Key 只写入本任务的本地配置文件。</span></div>
    </section>
  )
}

function EmptyWorkspace() {
  return (
    <section className="welcome-card">
      <div className="welcome-illustration" aria-hidden="true"><div className="paper paper-back"><i /><i /><i /></div><div className="paper paper-front"><span className="paper-chart"><b /><b /><b /><b /><b /></span><i /><i /><i /></div><span className="sparkle sparkle-one">✦</span><span className="sparkle sparkle-two">✧</span></div>
      <div className="eyebrow"><span className="hero-dot" />模块一 · 业务与经营背景</div>
      <h2>先弄清公司<br /><em>靠什么经营。</em></h2>
      <p>从年报身份、业务链条、收入结构、行业与战略，到本年报特有的经营专题；每项尽量保留原文页码和证据边界。</p>
      <div className="welcome-tags"><span>收入表身份</span><span>动态经营专题</span><span>原文回查</span></div>
    </section>
  )
}

export default function BusinessWorkbench() {
  const [health, setHealth] = useState({ api_configured: false, loading: true })
  const [keyModalOpen, setKeyModalOpen] = useState(false)
  const [savingKey, setSavingKey] = useState(false)
  const [keyError, setKeyError] = useState('')
  const [selectedFile, setSelectedFile] = useState(null)
  const [dragActive, setDragActive] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [recentRuns, setRecentRuns] = useState([])
  const [activeRun, setActiveRun] = useState(null)
  const [loadingRun, setLoadingRun] = useState(false)
  const [conversation, setConversation] = useState([])
  const [question, setQuestion] = useState('')
  const [conversationError, setConversationError] = useState('')

  const loadRun = useCallback(async (id) => {
    try {
      const response = await fetch(`${API}/runs/${id}`)
      if (!response.ok) throw new Error(await apiError(response))
      const data = await response.json()
      if (data.analysis_module !== 'business') throw new Error('这条任务不是模块一结果。')
      setActiveRun(data)
      return data
    } catch (error) {
      setUploadError(error.message || '读取任务失败。')
      return null
    }
  }, [])

  const loadRecent = useCallback(async () => {
    try {
      const response = await fetch(`${API}/runs`)
      if (!response.ok) return
      const data = await response.json()
      setRecentRuns((data.runs || []).filter((run) => run.analysis_module === 'business'))
    } catch { /* retain the last successful task list */ }
  }, [])

  const loadConversation = useCallback(async (id) => {
    try {
      const response = await fetch(`${API}/runs/${id}/conversation`)
      if (!response.ok) return
      const data = await response.json()
      setConversation(data.turns || [])
    } catch { /* keep the current task's locally displayed thread */ }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetch(`${API}/health`).then((response) => response.json()).then((data) => {
      if (cancelled) return
      setHealth({ ...data, loading: false })
      if (!data.api_configured && !window.localStorage.getItem('finlab-business-key-prompt-dismissed')) setKeyModalOpen(true)
    }).catch(() => {
      if (!cancelled) setHealth({ api_configured: false, unavailable: true, loading: false })
    })
    loadRecent()
    return () => { cancelled = true }
  }, [loadRecent])

  useEffect(() => {
    if (!activeRun?.id || !BUSY.has(activeRun.status)) return undefined
    const id = activeRun.id
    const timer = window.setInterval(async () => {
      const updated = await loadRun(id)
      if (updated && !BUSY.has(updated.status)) {
        window.clearInterval(timer)
        loadRecent()
        loadConversation(id)
      }
    }, 1300)
    return () => window.clearInterval(timer)
  }, [activeRun?.id, activeRun?.status, loadRun, loadRecent, loadConversation])

  useEffect(() => {
    const id = activeRun?.id
    if (!id || !conversation.some((turn) => turn.status === 'queued' || turn.status === 'running')) return undefined
    const timer = window.setInterval(() => { loadConversation(id); loadRun(id) }, 1400)
    return () => window.clearInterval(timer)
  }, [activeRun?.id, conversation, loadConversation, loadRun])

  const chooseFile = (file) => {
    setUploadError('')
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setSelectedFile(null)
      setUploadError('请选择 PDF 文件。')
      return
    }
    if (file.size > 25 * 1024 * 1024) {
      setSelectedFile(null)
      setUploadError('文件超过 25 MB，请先压缩 PDF 后再上传。')
      return
    }
    setSelectedFile(file)
  }

  const runFile = async () => {
    if (!selectedFile) return
    setLoadingRun(true)
    setUploadError('')
    setActiveRun(null)
    setConversation([])
    setQuestion('')
    setConversationError('')
    const form = new FormData()
    form.append('file', selectedFile)
    form.append('analysis_module', 'business')
    try {
      const response = await fetch(`${API}/runs`, { method: 'POST', body: form })
      if (!response.ok) throw new Error(await apiError(response))
      const run = await response.json()
      setActiveRun(run)
      await loadRecent()
      await loadRun(run.id)
    } catch (error) {
      setUploadError(error.message || '无法连接本模块服务，请确认工作台已启动。')
    } finally {
      setLoadingRun(false)
    }
  }

  const openRun = async (id) => {
    setLoadingRun(true)
    setUploadError('')
    setConversation([])
    const loaded = await loadRun(id)
    if (loaded) await loadConversation(id)
    setQuestion('')
    setConversationError('')
    setLoadingRun(false)
  }

  const startNew = () => {
    setActiveRun(null)
    setConversation([])
    setQuestion('')
    setConversationError('')
    setUploadError('')
    setSelectedFile(null)
  }

  const askQuestion = async () => {
    if (!activeRun?.id || !question.trim()) return
    setConversationError('')
    try {
      const response = await fetch(`${API}/runs/${activeRun.id}/conversation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question.trim() }),
      })
      if (!response.ok) throw new Error(await apiError(response))
      const turn = await response.json()
      setConversation((current) => [...current.filter((item) => item.id !== turn.id), turn])
      setQuestion('')
    } catch (error) {
      setConversationError(error.message || '追问暂时无法提交。')
    }
  }

  const saveApiKey = async (apiKey) => {
    setSavingKey(true)
    setKeyError('')
    try {
      const response = await fetch(`${API}/settings/deepseek-key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey.trim() }),
      })
      if (!response.ok) throw new Error(await apiError(response))
      setHealth((current) => ({ ...current, api_configured: true, unavailable: false }))
      setKeyModalOpen(false)
      window.localStorage.removeItem('finlab-business-key-prompt-dismissed')
    } catch (error) {
      setKeyError(error.message || '保存失败，请检查本机服务。')
    } finally {
      setSavingKey(false)
    }
  }

  const closeKeyModal = () => {
    setKeyModalOpen(false)
    setKeyError('')
    if (!health.api_configured) window.localStorage.setItem('finlab-business-key-prompt-dismissed', '1')
  }
  const taskBusy = loadingRun || BUSY.has(activeRun?.status)
  const followupBusy = conversation.some((turn) => turn.status === 'queued' || turn.status === 'running')

  return (
    <div className="app-shell business-workbench">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="模块一工作台首页">
          <span className="brand-mark" aria-hidden="true">◇</span>
          <span className="brand-name">林研<span>业务与经营背景 · V1.0 独立工作台</span></span>
        </a>
        <nav className="top-nav" aria-label="当前模块"><span className="nav-current">模块一 · 业务与经营背景</span></nav>
        <button className="model-status model-status-button" type="button" onClick={() => { setKeyError(''); setKeyModalOpen(true) }}>
          <span className={health.api_configured ? 'status-led led-on' : 'status-led'} />
          {health.loading ? '正在检查 DeepSeek' : health.api_configured ? 'DeepSeek 已配置' : '配置 DeepSeek Key'}
          <span className="status-edit">设置</span>
        </button>
      </header>

      <main id="top" className="main-layout">
        <div className="page-intro">
          <div className="breadcrumb"><span>独立工作台</span><b>/</b><span className="breadcrumb-current">模块一：业务与经营背景</span></div>
          <div className="hero-copy">
            <div className="eyebrow"><span className="hero-dot" />分析模块 01 · 专业流程独立运行</div>
            <h1>读懂经营，<span>先认清证据。</span></h1>
            <p>上传年报 PDF，按模块一的专业流程提取业务事实、收入表、行业与战略信息，并生成这份年报自己的经营专题。</p>
          </div>
        </div>

        <div className="work-grid">
          <div className="left-column">
            <UploadCard
              selectedFile={selectedFile}
              onChoose={(event) => chooseFile(event.target.files?.[0])}
              onRun={runFile}
              busy={taskBusy}
              configured={!!health.api_configured}
              onNew={startNew}
              error={uploadError || (health.unavailable ? '模块服务暂不可用。请重新运行“启动模块一工作台”。' : '')}
              dragActive={dragActive}
              onDrag={setDragActive}
              onDrop={(event) => { event.preventDefault(); setDragActive(false); chooseFile(event.dataTransfer.files?.[0]) }}
            />
            {recentRuns.length > 0 && <section className="recent-card card">
              <div className="recent-heading"><h3>模块一历史任务</h3><span>{recentRuns.length} 条</span></div>
              <div className="recent-list">{recentRuns.slice(0, 12).map((run) => (
                <button className={`recent-item ${activeRun?.id === run.id ? 'recent-active' : ''}`} key={run.id} type="button" onClick={() => openRun(run.id)} disabled={loadingRun}>
                  <span className="recent-pdf-icon">PDF</span>
                  <span className="recent-file-name">{run.file_name}<small>{run.stage || '模块一 · 业务与经营背景'} · {formatTime(run.updated_at || run.created_at)}</small></span>
                  <span className={`recent-status recent-${run.status}`}>{run.status === 'completed' ? '完成' : run.status === 'failed' ? '失败' : '进行中'}</span>
                </button>
              ))}</div>
            </section>}
            <div className="workflow-note"><span className="workflow-count">01</span><span><b>模块一 · 业务与经营背景</b><small>保留本模块专业提示词、检索与程序计算流程</small></span><span className="workflow-arrow">↗</span></div>
          </div>

          <div className="right-column">
            {!activeRun && <EmptyWorkspace />}
            {activeRun && <StatusSummary run={activeRun} />}
            <RunProgress run={activeRun} />
            <FailedPanel run={activeRun} />
            {activeRun?.result && <BusinessResultPanel run={activeRun} result={activeRun.result} />}
            {!activeRun?.result && activeRun?.status === 'completed' && <div className="empty-result card">任务运行已结束，但没有保存可展示的模块一结果。请查看失败原因与分析步骤。</div>}
            <ConversationPanel
              run={activeRun}
              turns={conversation}
              question={question}
              onQuestionChange={setQuestion}
              onAsk={askQuestion}
              error={conversationError}
              busy={followupBusy}
            />
          </div>
        </div>
      </main>

      <footer className="footer"><span>林研 · 模块一独立工作台</span><span>V1.0 · 业务与经营背景 <b>·</b> 结论保留出处、边界和待复核状态</span></footer>
      <DeepSeekKeyModal
        open={keyModalOpen}
        saving={savingKey}
        error={keyError}
        configured={!!health.api_configured}
        onSave={saveApiKey}
        onClose={closeKeyModal}
      />
    </div>
  )
}
