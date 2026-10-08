import { useCallback, useEffect, useRef, useState } from 'react'

const API = '/api'
const BUSY = new Set(['queued', 'running'])

const stages = [
  { match: '已接收文件', title: '已接收文件' },
  { match: '读取 PDF', title: '读取 PDF 页面' },
  { match: '财报页面', title: '准备关键页面' },
  { match: 'DeepSeek', title: 'DeepSeek 正在分析' },
  { match: '分析完成', title: '整理结果' },
]

function formatTime(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

async function apiError(response) {
  try {
    const body = await response.json()
    return body.detail || body.error || `请求失败（${response.status}）`
  } catch {
    return `请求失败（${response.status}）`
  }
}

function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 40 40" fill="none">
        <path d="M20 3.8 36.2 13v14L20 36.2 3.8 27V13L20 3.8Z" fill="currentColor" opacity=".14" />
        <path d="M20 8.5 31.7 15v10L20 31.5 8.3 25V15L20 8.5Z" stroke="currentColor" strokeWidth="2.2" />
        <path d="M20 13v14m-6.1-10.5 12.2 7m0-7-12.2 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    </div>
  )
}

function PagePill({ page, runId }) {
  const label = `PDF 第 ${page} 页`
  return runId
    ? <a className="page-pill page-link" href={`${API}/runs/${runId}/file#page=${page}`} target="_blank" rel="noreferrer">{label}</a>
    : <span className="page-pill">{label}</span>
}

function factValueDisplay(fact) {
  const raw = String(fact.value ?? '')
  const numeric = Number(raw.replace(/,/g, ''))
  if (fact.unit === '元' && Number.isFinite(numeric) && Math.abs(numeric) >= 100_000_000) {
    return {
      value: new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(numeric / 100_000_000),
      unit: '亿元',
      original: `${raw} 元`,
    }
  }
  return { value: raw, unit: fact.unit, original: '' }
}

function CitationText({ text, runId }) {
  const value = String(text || '')
  const parts = []
  const pattern = /(PDF\s*第\s*(\d+)\s*页|第\s*(\d+)\s*页)/g
  let offset = 0
  let match
  while ((match = pattern.exec(value)) !== null) {
    if (match.index > offset) parts.push(value.slice(offset, match.index))
    const page = Number(match[2] || match[3])
    parts.push(<a className="citation-link" href={`${API}/runs/${runId}/file#page=${page}`} target="_blank" rel="noreferrer" key={`${match.index}-${page}`}>{match[0]}</a>)
    offset = pattern.lastIndex
  }
  if (offset < value.length) parts.push(value.slice(offset))
  return parts
}

function InlineAnswerText({ text, runId }) {
  const parts = String(text || '').split(/(\*\*.+?\*\*)/g)
  return <>{parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index}><CitationText text={part.slice(2, -2)} runId={runId} /></strong>
    }
    return <CitationText text={part} runId={runId} key={index} />
  })}</>
}

function markdownCells(line) {
  const cells = line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
  return cells
}

function isTableDivider(line) {
  const cells = markdownCells(line)
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell))
}

function MarkdownAnswer({ text, runId }) {
  const lines = String(text || '').replace(/\r/g, '').split('\n')
  const blocks = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index].trim()
    if (!line) { index += 1; continue }

    const heading = line.match(/^(#{1,3})\s+(.+)$/)
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2], key: index++ })
      continue
    }

    if (line.startsWith('>')) {
      const quote = []
      while (index < lines.length && lines[index].trim().startsWith('>')) quote.push(lines[index++].trim().replace(/^>\s?/, ''))
      blocks.push({ type: 'quote', text: quote.join('\n'), key: index })
      continue
    }

    const listMatch = line.match(/^([-*]|\d+\.)\s+(.+)$/)
    if (listMatch) {
      const ordered = /^\d/.test(listMatch[1])
      const items = []
      while (index < lines.length) {
        const item = lines[index].trim().match(ordered ? /^\d+\.\s+(.+)$/ : /^[-*]\s+(.+)$/)
        if (!item) break
        items.push(item[1])
        index += 1
      }
      blocks.push({ type: ordered ? 'ordered-list' : 'list', items, key: index })
      continue
    }

    if (line.includes('|') && index + 1 < lines.length && isTableDivider(lines[index + 1].trim())) {
      const headers = markdownCells(line)
      index += 2
      const rows = []
      while (index < lines.length && lines[index].trim().includes('|')) rows.push(markdownCells(lines[index++]))
      blocks.push({ type: 'table', headers, rows, key: index })
      continue
    }

    const paragraph = [line]
    index += 1
    while (index < lines.length && lines[index].trim()) {
      const next = lines[index].trim()
      if (/^#{1,3}\s+/.test(next) || next.startsWith('>') || /^([-*]|\d+\.)\s+/.test(next)) break
      if (next.includes('|') && index + 1 < lines.length && isTableDivider(lines[index + 1].trim())) break
      paragraph.push(next)
      index += 1
    }
    blocks.push({ type: 'paragraph', text: paragraph.join('\n'), key: index })
  }

  return <div className="answer-markdown">{blocks.map((block) => {
    if (block.type === 'heading') {
      const Tag = `h${Math.min(4, block.level + 2)}`
      return <Tag className="answer-heading" key={block.key}><InlineAnswerText text={block.text} runId={runId} /></Tag>
    }
    if (block.type === 'quote') return <blockquote className="answer-quote" key={block.key}><InlineAnswerText text={block.text} runId={runId} /></blockquote>
    if (block.type === 'list' || block.type === 'ordered-list') {
      const List = block.type === 'list' ? 'ul' : 'ol'
      return <List className="answer-list" key={block.key}>{block.items.map((item, itemIndex) => <li key={itemIndex}><InlineAnswerText text={item} runId={runId} /></li>)}</List>
    }
    if (block.type === 'table') return <div className="answer-table-scroll" key={block.key}><table className="answer-table"><thead><tr>{block.headers.map((cell, cellIndex) => <th key={cellIndex}><InlineAnswerText text={cell} runId={runId} /></th>)}</tr></thead><tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{block.headers.map((_, cellIndex) => <td key={cellIndex}><InlineAnswerText text={row[cellIndex] || ''} runId={runId} /></td>)}</tr>)}</tbody></table></div>
    return <p className="answer-paragraph" key={block.key}><InlineAnswerText text={block.text} runId={runId} /></p>
  })}</div>
}

function UploadPanel({ onRun, busy, apiConfigured, onNew, activeRun, analysisModule, onModuleChange }) {
  const inputRef = useRef(null)
  const [file, setFile] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!activeRun) setFile(null)
  }, [activeRun?.id])

  const acceptFile = (candidate) => {
    setError('')
    if (!candidate) return
    if (!candidate.name.toLowerCase().endsWith('.pdf')) {
      setError('请选择 PDF 文件。')
      return
    }
    if (candidate.size > 25 * 1024 * 1024) {
      setError('文件超过 25 MB，请先压缩后重试。')
      return
    }
    setFile(candidate)
  }

  const submit = async () => {
    if (!file) return
    setError('')
    await onRun(file, analysisModule, setError)
  }

  return (
    <section className="upload-card card">
      <div className="card-heading">
        <div>
          <div className="eyebrow">从一份年报开始</div>
          <h2>上传财报 PDF</h2>
        </div>
        {activeRun && <button className="quiet-button" onClick={onNew}>新建分析</button>}
      </div>

      <input
        ref={inputRef}
        className="file-input"
        type="file"
        accept="application/pdf,.pdf"
        onChange={(event) => {
          acceptFile(event.target.files?.[0])
          event.target.value = ''
        }}
      />
      <button
        className={`drop-zone ${dragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`}
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          acceptFile(event.dataTransfer.files?.[0])
        }}
        aria-label="选择或拖入一份 PDF 财报"
      >
        <span className={`upload-icon ${file ? 'upload-icon-ready' : ''}`}>
          {file ? (
            <svg viewBox="0 0 24 24" fill="none"><path d="m5 12.5 4.2 4.2L19 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 15.5v3A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5v-3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
          )}
        </span>
        <span className="drop-title">{file ? file.name : '拖入 PDF 文件，或点击选择'}</span>
        <span className="drop-subtitle">{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 已在本机选择` : '支持可提取文字的年报 PDF，单个文件最大 25 MB'}</span>
      </button>

      <div className="module-selector">
        <label htmlFor="analysis-module">选择分析任务</label>
        <select id="analysis-module" value={analysisModule} onChange={(event) => onModuleChange(event.target.value)} disabled={busy}>
          <option value="complete">完整财报分析：模块一 + 模块二</option>
          <option value="business">模块一：业务与经营背景</option>
          <option value="profit">模块二：盈利来源与变化</option>
        </select>
        <p>{analysisModule === 'profit'
          ? '拆解利润从哪些报表项目形成、同比变化的会计贡献和未解释差额；经营原因必须回到年报查证。'
          : analysisModule === 'business'
          ? '聚焦公司做什么、收入结构、经营变化和披露的增长因素。'
          : '默认先完整分析业务与经营背景，再拆解盈利来源和利润变化；两个模块会分别显示，结论都可回查年报。'}</p>
      </div>

      {error && <div className="inline-error" role="alert">{error}</div>}

      {!apiConfigured && (
        <div className="config-notice" role="status">
          <span className="notice-dot" />
          还没有配置 DeepSeek API Key。点击页面顶部的“配置 DeepSeek Key”即可设置。
        </div>
      )}

      <button className="primary-button" disabled={!file || busy || !apiConfigured} onClick={submit}>
        {busy ? <><span className="button-spinner" />正在分析</> : <>{analysisModule === 'profit' ? '开始模块二分析' : analysisModule === 'business' ? '开始模块一分析' : '开始完整分析（模块一 + 模块二）'} <span aria-hidden="true">↗</span></>}
      </button>

      <div className="privacy-note">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 2.5 16 5v4.3c0 3.7-2.5 6.7-6 8.2-3.5-1.5-6-4.5-6-8.2V5l6-2.5Z" stroke="currentColor" strokeWidth="1.4"/><path d="M7.5 10.1 9.2 12l3.5-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>
        PDF 原件保存在本机；所选页面的文字会发送给 DeepSeek API 用于本次分析。
      </div>
    </section>
  )
}

function RunProgress({ run }) {
  if (!run || !BUSY.has(run.status)) return null
  return (
    <section className="progress-card card" aria-live="polite">
      <div className="progress-topline">
        <span className="pulse-dot" />
        <span>{run.stage || '正在处理'}</span>
      </div>
      <div className="progress-track"><span /></div>
      {run.steps?.length ? (
        <ol className="analysis-steps">
          {run.steps.map((step) => <li className={`analysis-step step-${step.status}`} key={step.ordinal}>
            <span className="step-indicator" />
            <span><b>{step.name}</b>{step.detail && <small>{step.detail}</small>}</span>
            <em>{step.status}</em>
          </li>)}
        </ol>
      ) : <div className="progress-stages">{stages.slice(0, 4).map((stage) => <span key={stage.match}>{stage.title}</span>)}</div>}
      <p>正在阅读 <strong>{run.file_name}</strong>。第一次分析可能需要一点时间。</p>
      {run.page_count > 0 && (
        <div className="progress-pages">
          已识别 {run.page_count} 页；提交给模型的页面：{run.read_pages?.length ? run.read_pages.map((page) => `第 ${page} 页`).join('、') : '正在整理'}
        </div>
      )}
    </section>
  )
}

function ResultPanel({ run }) {
  if (!run || !run.result) return null
  const result = run.result
  if (result.module === 'combined') return <CombinedResultPanel run={run} result={result} />
  if (run.status !== 'completed') return null
  if (result.module === 'business') return <BusinessResultPanel run={run} result={result} />
  if (result.module === 'profit') return <ProfitResultPanel run={run} result={result} />
  return (
    <section className="result-wrap">
      <div className="result-header">
        <div>
          <div className="eyebrow">财务概览</div>
          <h2>{result.company || '上市公司'} <span>{result.period || ''}</span></h2>
        </div>
        <div className="result-actions">
          <a className="source-link" href={`${API}/runs/${run.id}/file`} target="_blank" rel="noreferrer">打开原始 PDF ↗</a>
          <span className="complete-badge"><span />已完成</span>
        </div>
      </div>

      <article className="summary-card card">
        <div className="summary-label"><span className="summary-spark">✳</span> DeepSeek 概览</div>
        <p>{result.summary}</p>
      </article>

      <div className="section-title-row">
        <div><span className="section-number">01</span><h3>关键数据</h3></div>
        <span className="muted-label">来自模型已读取的页面</span>
      </div>

      {result.facts?.length ? (
        <div className="facts-grid">
          {result.facts.map((fact, index) => (
            <article className="fact-card card" key={`${fact.name}-${index}`}>
              <div className="fact-name">{fact.name}</div>
              <div className="fact-value">{factValueDisplay(fact).value}<small>{factValueDisplay(fact).unit}</small></div>
              {factValueDisplay(fact).original && <div className="fact-original">原文：{factValueDisplay(fact).original}</div>}
              <div className="fact-meta">{[fact.period, fact.change].filter(Boolean).join(' · ') || '期间或变化未识别'}</div>
              {fact.note && <div className="fact-note">{fact.note}</div>}
              {fact.evidence_quote && <blockquote className="fact-evidence"><span>{fact.quote_verified ? '原文摘录已匹配' : '原文摘录待核对'}</span>{fact.evidence_quote}</blockquote>}
              {!!fact.source_pages?.length && <div className="fact-pages">{fact.source_pages.map((page) => <PagePill page={page} runId={run.id} key={page} />)}</div>}
            </article>
          ))}
        </div>
      ) : (
        <div className="empty-result card">本次页面中没有识别到可展示的关键指标，请查看下方说明或尝试另一份 PDF。</div>
      )}

      {!!result.observations?.length && (
        <article className="observations-card card">
          <div className="subsection-title"><span className="mini-icon">↗</span><h3>值得关注</h3></div>
          <ul>{result.observations.map((item, index) => {
            const text = typeof item === 'string' ? item : item.text
            const pages = typeof item === 'string' ? [] : item.source_pages || []
            return <li key={index}><CitationText text={text} runId={run.id} /> {pages.map((page) => <PagePill page={page} runId={run.id} key={page} />)}</li>
          })}</ul>
        </article>
      )}

      <details className="source-details card">
        <summary><span><span className="source-icon">▤</span>本次模型读取的 PDF 页面</span><span className="details-count">{result.read_pages?.length || 0} 页</span></summary>
        <div className="source-list">
          {result.read_pages?.map((page) => <PagePill page={page} runId={run.id} key={page} />)}
          <p>首轮候选页之外，系统可根据分析和追问按需检索全文；页码可点击回看原 PDF。</p>
        </div>
      </details>

      {!!result.uncertainties?.length && (
        <article className="uncertainty-card">
          <div className="uncertainty-title"><span>!</span>需要留意</div>
          <ul>{result.uncertainties.map((text, index) => <li key={index}>{text}</li>)}</ul>
        </article>
      )}

      {!!run.steps?.length && <details className="source-details card workflow-details">
        <summary><span><span className="source-icon">↻</span>本次分析步骤</span><span className="details-count">{run.steps.length} 步</span></summary>
        <ol className="completed-steps">{run.steps.map((step) => <li key={step.ordinal}><b>{step.name}</b><span>{step.detail || step.status}</span></li>)}</ol>
      </details>}

      <div className="run-footnote">
        <span>模型：{result.model || 'DeepSeek'}</span>
        <span>已读取 {result.read_pages?.length || 0} / {run.page_count} 页</span>
        {result.usage?.prompt_tokens != null && <span>本次用量 {result.usage.prompt_tokens + (result.usage.completion_tokens || 0)} tokens</span>}
        <span>文件校验值 {run.sha256?.slice(0, 12)}…</span>
      </div>
    </section>
  )
}

function CombinedResultPanel({ run, result }) {
  const modules = result.modules || {}
  const sections = [
    { key: 'business', id: 'module-one', title: '模块一 · 业务与经营背景', description: '经营模式、行业与战略、收入结构、经营变化、依赖与风险' },
    { key: 'profit', id: 'module-two', title: '模块二 · 盈利来源与变化', description: '盈利指标、利润变化桥、利润表事实、分部毛利、非经常性损益' },
  ]
  const completeCount = sections.filter((item) => modules[item.key]?.status === 'completed').length
  const overallLabel = completeCount === 2
    ? '两个模块均已完成'
    : run.status === 'running'
      ? `${completeCount}/2 个模块已完成 · ${run.stage || '正在分析'}`
      : `${completeCount}/2 个模块已完成`

  return <section className="combined-report">
    <header className="combined-header card">
      <div className="eyebrow">完整财报分析 · {result.period || '报告年度待确认'}</div>
      <h2>{result.company || '上市公司'} <span>{result.period || ''}</span></h2>
      <p>模块一整理公司业务和经营变化；模块二分析利润结构，并参考模块一的信息核查经营解释。每个模块的结果、出处和待核问题分开呈现。</p>
      <div className="combined-status-row"><span className={completeCount === 2 ? 'complete-badge' : 'complete-badge review-needed-badge'}><span />{overallLabel}</span><span>{run.file_name}</span><span>PDF 共 {run.page_count} 页</span></div>
      <nav className="combined-nav" aria-label="报告模块目录">
        {sections.map((item) => {
          const status = modules[item.key]?.status
          const label = status === 'completed' ? '已完成' : status === 'failed' ? '未完成' : status === 'running' ? '进行中' : '等待运行'
          return <a href={`#${item.id}`} key={item.key}><span className="combined-nav-copy"><strong>{item.title}</strong><small>{item.description}</small></span><span className="combined-nav-status">{label}</span></a>
        })}
      </nav>
    </header>
    {sections.map((item) => {
      const entry = modules[item.key] || {}
      if (entry.status === 'running' || entry.status === 'queued') {
        return <section className="module-progress-card card" id={item.id} key={item.key}>
          <div><div className="eyebrow">{item.title}</div><h3>{entry.status === 'running' ? '正在分析' : '等待运行'}</h3></div>
          <p>{entry.status === 'running' ? (run.stage || '本模块正在读取年报并整理结果。') : '前一个模块完成后，将接着运行本模块。'}</p>
        </section>
      }
      if (entry.status !== 'completed' || !entry.result) {
        return <section className="module-failure-card card" id={item.id} key={item.key}>
          <div><div className="eyebrow">{item.title}</div><h3>本模块未完成</h3></div>
          <p>{entry.error || '本次没有生成此模块的结果。'}</p>
          <a className="source-link" href={`${API}/runs/${run.id}/file`} target="_blank" rel="noreferrer">查看本次年报 ↗</a>
        </section>
      }
      return item.key === 'business'
        ? <BusinessResultPanel run={run} result={entry.result} key={item.key} />
        : <ProfitResultPanel run={run} result={entry.result} key={item.key} />
    })}
    <details className="source-details card combined-workflow">
      <summary><span><span className="source-icon">↻</span>完整分析的运行步骤</span><span className="details-count">{run.steps?.length || 0} 步</span></summary>
      <ol className="completed-steps">{(run.steps || []).map((step) => <li key={step.ordinal}><b>{step.name}</b><span>{step.detail || step.status}</span></li>)}</ol>
    </details>
  </section>
}

function EmptyModuleSection({ module, title }) {
  return <div className="empty-result card module-empty-state">
    <strong>本次没有整理出“{title}”条目</strong>
    <p>这不代表年报一定没有披露。请结合覆盖检查和待核问题查看是未检索到、材料不足，还是本次分析未生成。</p>
  </div>
}

function ProfitResultPanel({ run, result }) {
  const bridge = result.profit_bridge || {}
  const bridgeRows = bridge.detail_rows?.length ? bridge.detail_rows : bridge.contributions || []
  const coverageStatus = { supported: '有可回查内容', needs_review: '待核对', candidate_only: '找到候选页', search_miss: '未检索到' }
  const displayYuan = (value) => formatRawAmount(value, '元')
  const sourceLinks = (item) => item.source_pages?.map((page) => <PagePill page={page} runId={run.id} key={page} />)

  return <section className="result-wrap profit-result" id="module-two">
    <div className="result-header">
      <div><div className="eyebrow">模块二 · 盈利来源与变化</div><h2>{result.company || '上市公司'} <span>{result.period || ''}</span></h2></div>
      <div className="result-actions"><a className="source-link" href={`${API}/runs/${run.id}/file`} target="_blank" rel="noreferrer">打开原始 PDF ↗</a><span className="complete-badge"><span />分析已完成</span></div>
    </div>
    <div className="profit-meta card">报表范围：{result.reporting_scope || '未能确认'}　·　币种：{result.currency || '未能确认'}　·　金额按同一报告本期与上期比较</div>
    {result.module_one_context_status && <div className="module-context-note card">模块交接：{result.module_one_context_status}。此背景只用于定位核查方向，盈利结论仍以本模块核对的年报数据和出处为准。</div>}
    <article className="summary-card card"><div className="summary-label"><span className="summary-spark">✳</span> 盈利变化摘要</div><p>{result.summary || '已完成可用事实和程序计算；解释仍需结合证据复核。'}</p><EvidenceQuote item={result.summary_evidence} runId={run.id} /></article>

    <div className="section-title-row"><div><span className="section-number">01</span><h3>程序复算指标</h3></div><span className="muted-label">只在期间、范围、币种一致时计算</span></div>
    {result.profit_metrics?.length ? <div className="profit-metric-grid">{result.profit_metrics.map((item, index) => <article className="profit-metric card" key={item.name + index}>
      <span className="fact-name">{item.name}</span><strong>{item.current_value ?? '未计算'}{item.current_value != null ? '%' : ''}</strong>
      {item.previous_value != null && <span>上期 {item.previous_value}% · 变化 {item.change_percentage_points > 0 ? '+' : ''}{item.change_percentage_points} 个百分点</span>}
      {item.current_gross_profit_yuan != null && <span>本期毛利 {displayYuan(item.current_gross_profit_yuan)} · 上期 {displayYuan(item.previous_gross_profit_yuan)}</span>}
      <small>{item.status}</small>
    </article>)}</div> : <div className="empty-result card">没有足够的同口径数字计算毛利率或费用率。利润表明细仍会在下方展示。</div>}

    <div className="section-title-row"><div><span className="section-number">02</span><h3>{bridge.target_label || '利润'}变化桥</h3></div><span className="muted-label">会计加减贡献，不代表已证实的经营原因</span></div>
    <article className="profit-bridge-card card"><div className="profit-bridge-summary"><div><small>目标利润变化</small><strong>{bridge.target_change_yuan != null ? displayYuan(bridge.target_change_yuan) : '未计算'}</strong></div><div><small>已识别会计贡献</small><strong>{bridge.identified_contribution_yuan != null ? displayYuan(bridge.identified_contribution_yuan) : '未计算'}</strong></div><div><small>未分类差额</small><strong>{bridge.residual_yuan != null ? displayYuan(bridge.residual_yuan) : '未计算'}</strong></div></div>
      <p className="profit-bridge-note">{bridge.formula || '利润变化 − 已识别项目贡献 = 未分类差额'}。{bridge.status || '仍需结合年报核对。'}</p>
      {bridgeRows.length ? <div className="profit-table-scroll"><table className="profit-table"><thead><tr><th>项目</th><th>项目金额变化</th><th>对目标利润的会计贡献</th><th>是否纳入合计</th><th>出处</th></tr></thead><tbody>{bridgeRows.map((item, index) => <tr key={item.role + item.label + index} className={item.included_in_total === false ? 'profit-excluded-row' : ''}><td><strong>{item.label}</strong><small>{item.role}</small></td><td>{displayYuan(item.change_yuan)}</td><td>{displayYuan(item.profit_effect_yuan)}</td><td>{item.included_in_total === false ? '否 · 已含在小计或不可相加' : '是'}</td><td>{sourceLinks(item)}</td></tr>)}</tbody></table></div> : <div className="empty-result">没有通过出处、期间和报表范围校验的利润项目可纳入变化桥。</div>}
      {!!bridge.excluded_ambiguous_roles?.length && <p className="profit-warning">存在互相冲突的同类项目，已从合计中排除：{bridge.excluded_ambiguous_roles.join('、')}</p>}
    </article>

    <div className="section-title-row"><div><span className="section-number">03</span><h3>利润层次勾稽</h3></div><span className="muted-label">用程序复算，按报告显示精度判断差额</span></div>
    {result.profit_checks?.length ? <div className="profit-check-grid">{result.profit_checks.map((item) => <article className="profit-check card" key={item.name}><div><strong>{item.name}</strong><span>{item.status}</span></div><p>{item.formula}</p>{item.current_reported_yuan != null && <small>本期：报告 {displayYuan(item.current_reported_yuan)} · 复算 {displayYuan(item.current_calculated_yuan)} · 差额 {displayYuan(item.current_difference_yuan)}</small>}{item.previous_reported_yuan != null && <small>上期：报告 {displayYuan(item.previous_reported_yuan)} · 复算 {displayYuan(item.previous_calculated_yuan)} · 差额 {displayYuan(item.previous_difference_yuan)}</small>}{item.source_rows?.map((source, sourceIndex) => <EvidenceQuote item={source} runId={run.id} key={source.profit_role + sourceIndex} />)}</article>)}</div> : <EmptyModuleSection module="模块二" title="利润层次勾稽" />}

    <div className="section-title-row"><div><span className="section-number">04</span><h3>利润表事实</h3></div><span className="muted-label">原值保留，逐项回到 PDF</span></div>
    {result.profit_lines?.length ? <details className="profit-table-card card" open><summary>已提取 {result.profit_lines.length} 个利润表项目</summary><div className="profit-table-scroll"><table className="profit-table"><thead><tr><th>项目原名</th><th>本期</th><th>上期</th><th>变化</th><th>校验状态 / 出处</th></tr></thead><tbody>{result.profit_lines.map((item, index) => <tr key={item.profit_role + item.label + index}><td><strong>{item.label}</strong><small>{item.profit_role === 'unmapped' ? '待匹配' : item.profit_role}</small></td><td>{formatRawAmount(item.current_value, item.current_unit)}<small>{item.current_period || '期间未识别'}</small></td><td>{formatRawAmount(item.previous_value, item.previous_unit)}<small>{item.previous_period || '未提供比较数'}</small></td><td>{item.change_yuan != null ? displayYuan(item.change_yuan) : '未计算'}</td><td><span className={item.value_status === '可计算' ? 'profit-status-ok' : 'profit-status-review'}>{item.value_status}</span><EvidenceQuote item={item} runId={run.id} /></td></tr>)}</tbody></table></div></details> : <div className="empty-result card">没有提取到利润表项目。请查看覆盖状态和检索页。</div>}

    {!!result.profit_metrics?.length && result.profit_metrics.some((item) => item.name === '营业毛利率') && <article className="profit-explain card"><strong>毛利口径</strong><p>营业毛利 = 营业收入 − 对应营业成本。营业总收入不代替营业收入；分部毛利不等于分部净利润。</p></article>}

    {result.business_segments?.length ? <><div className="section-title-row"><div><span className="section-number">05</span><h3>业务分部毛利资料</h3></div><span className="muted-label">以年报披露的业务口径为准</span></div><div className="profit-table-card card"><div className="profit-table-scroll"><table className="profit-table"><thead><tr><th>分部/产品</th><th>本期</th><th>上期</th><th>状态与出处</th></tr></thead><tbody>{result.business_segments.map((item, index) => <tr key={item.label + index}><td><strong>{item.label}</strong><small>{item.profit_role}</small></td><td>{formatRawAmount(item.current_value, item.current_unit)}</td><td>{formatRawAmount(item.previous_value, item.previous_unit)}</td><td>{item.value_status}<EvidenceQuote item={item} runId={run.id} /></td></tr>)}</tbody></table></div></div></> : <><div className="section-title-row"><div><span className="section-number">05</span><h3>业务分部毛利资料</h3></div></div><EmptyModuleSection module="模块二" title="业务分部毛利资料" /></>}

    {result.nonrecurring_items?.length ? <><div className="section-title-row"><div><span className="section-number">06</span><h3>非经常性损益披露</h3></div><span className="muted-label">仅按报告明示项目归类</span></div><div className="profit-table-card card"><div className="profit-table-scroll"><table className="profit-table"><thead><tr><th>项目</th><th>本期金额</th><th>上期金额</th><th>出处</th></tr></thead><tbody>{result.nonrecurring_items.map((item, index) => <tr key={item.label + index}><td><strong>{item.label}</strong><small>{item.profit_role}</small></td><td>{formatRawAmount(item.current_value, item.current_unit)}</td><td>{formatRawAmount(item.previous_value, item.previous_unit)}</td><td>{sourceLinks(item)}</td></tr>)}</tbody></table></div></div></> : <><div className="section-title-row"><div><span className="section-number">06</span><h3>非经常性损益披露</h3></div></div><EmptyModuleSection module="模块二" title="非经常性损益披露" /></>}

    {result.findings?.length ? <><div className="section-title-row"><div><span className="section-number">07</span><h3>有证据支持的变化观察</h3></div><span className="muted-label">公司解释、程序贡献与分析判断分别呈现</span></div><div className="business-list">{result.findings.map((item, index) => <article className="business-list-card card" key={item.title + index}><div className="business-card-top"><h4>{item.title || '利润变化观察'}</h4><span className="business-status">{item.quote_verified ? '出处已匹配' : '出处待核'}</span></div>{item.observation && <p><b>年报观察：</b>{item.observation}</p>}{item.accounting_contribution && <p><b>会计贡献：</b>{item.accounting_contribution}</p>}{item.company_explanation && <p><b>公司解释：</b>{item.company_explanation}</p>}{item.analysis && <p><b>有限分析：</b>{item.analysis}</p>}{item.alternative_explanation && <p><b>其他可能/证据限制：</b>{item.alternative_explanation}</p>}{item.limitation && <div className="business-limitation">尚不能确认：{item.limitation}</div>}<EvidenceQuote item={item} runId={run.id} /></article>)}</div></> : <><div className="section-title-row"><div><span className="section-number">07</span><h3>有证据支持的变化观察</h3></div></div><EmptyModuleSection module="模块二" title="盈利变化观察" /></>}

    {result.follow_up_checks?.length ? <section className="business-followups card"><div className="subsection-title"><span className="mini-icon">↗</span><h3>08 · 交给后续模块继续核对</h3></div><div className="business-list">{result.follow_up_checks.map((item, index) => <article className="followup-item" key={item.question + index}><strong>{item.question}</strong><p>{item.reason}</p><span>{item.next_module}</span><EvidenceQuote item={item} runId={run.id} /></article>)}</div></section> : <section className="business-followups card"><div className="subsection-title"><span className="mini-icon">↗</span><h3>08 · 交给后续模块继续核对</h3></div><EmptyModuleSection module="模块二" title="模块间交接问题" /></section>}

    {result.coverage_checks?.length ? <article className="coverage-checks card"><div className="coverage-heading"><div><div className="summary-label">模块二覆盖检查</div><p>“有出处”表示至少一项能回查，不代表整项已完整覆盖。</p></div><span>{result.analysis_review_status || '待人工复核'}</span></div><div className="coverage-grid">{result.coverage_checks.map((item) => <div className={`coverage-item coverage-${item.status}`} key={item.key}><div><strong>{item.name}</strong><span>{coverageStatus[item.status] || item.status}</span></div><p>{item.note}</p>{item.candidate_pages?.map((page) => <PagePill page={page} runId={run.id} key={page} />)}</div>)}</div></article> : <EmptyModuleSection module="模块二" title="模块覆盖检查" />}

    {result.uncertainties?.length ? <article className="uncertainty-card"><div className="uncertainty-title"><span>!</span>需要留意</div><ul>{result.uncertainties.map((text, index) => <li key={index}>{text}</li>)}</ul></article> : <article className="uncertainty-card"><div className="uncertainty-title"><span>!</span>未单独列出待核问题</div><p>仍请查看覆盖检查和各分析分项；未列出不等于利润判断已经全部确认。</p></article>}
    <details className="source-details card"><summary><span><span className="source-icon">▤</span>本次模型读取的 PDF 页面</span><span className="details-count">{result.read_pages?.length || 0} 页</span></summary><div className="source-list">{result.read_pages?.map((page) => <PagePill page={page} runId={run.id} key={page} />)}<p>点击页码可回看原 PDF。模型引用只在程序能将摘录匹配到已读取页面时标记为已匹配。</p></div></details>
    {!!run.steps?.length && <details className="source-details card workflow-details"><summary><span><span className="source-icon">↻</span>本次分析步骤</span><span className="details-count">{run.steps.length} 步</span></summary><ol className="completed-steps">{run.steps.map((step) => <li key={step.ordinal}><b>{step.name}</b><span>{step.detail || step.status}</span></li>)}</ol></details>}
    <div className="run-footnote"><span>模型：{result.model || 'DeepSeek'}</span><span>已读取 {result.read_pages?.length || 0} / {run.page_count} 页</span>{result.usage?.prompt_tokens != null && <span>本次用量 {result.usage.prompt_tokens + (result.usage.completion_tokens || 0)} tokens</span>}<span>流程版本：{result.prompt_version || '模块二_v1'}</span></div>
  </section>
}

function formatRawAmount(value, unit) {
  if (value == null || value === '') return '未披露'
  const numeric = Number(String(value).replace(/,/g, ''))
  const formatted = Number.isFinite(numeric)
    ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(numeric)
    : String(value)
  return formatted + (unit ? ' ' + unit : '')
}

function EvidenceQuote({ item, runId }) {
  if (!item || (!item.evidence_quote && !item.source_pages?.length)) return null
  return (
    <div className="business-evidence">
      {item.evidence_quote && <blockquote><span>{item.quote_verified ? '原文摘录已匹配' : '原文摘录待核对'}</span>{item.evidence_quote}</blockquote>}
      {!!item.source_pages?.length && <div className="fact-pages">{item.source_pages.map((page) => <PagePill page={page} runId={runId} key={page} />)}</div>}
    </div>
  )
}

function BusinessResultPanel({ run, result }) {
  const revenue = result.revenue_total || {}
  const segments = result.revenue_segments || []
  const groups = [...new Set(segments.map((item) => item.basis || '未注明口径'))]
  const otherRevenueTotals = (result.revenue_totals || []).filter((item) =>
    item.metric_name !== revenue.metric_name
    || item.current_value !== revenue.current_value
    || item.current_unit !== revenue.current_unit,
  )
  const totalChange = revenue.calculated_change || {}
  const analysisReviewNeeded = Boolean(
    result.analysis_review_status
    && result.analysis_review_status !== '六类内容均有可回查出处',
  )
  const revenueGroups = groups.map((basis) => ({
    basis,
    items: segments.filter((item) => (item.basis || '未注明口径') === basis),
  }))
  return (
    <section className="result-wrap business-result" id="module-one">
      <div className="result-header">
        <div>
          <div className="eyebrow">模块一 · 业务与经营背景</div>
          <h2>{result.company || '上市公司'} <span>{result.period || ''}</span></h2>
          <div className="business-meta">{[result.reporting_scope, result.currency].filter(Boolean).join(' · ')}</div>
        </div>
        <div className="result-actions">
          <a className="source-link" href={'/api/runs/' + run.id + '/file'} target="_blank" rel="noreferrer">打开原始 PDF ↗</a>
          <span className={analysisReviewNeeded ? 'complete-badge review-needed-badge' : 'complete-badge'}>
            <span />{analysisReviewNeeded ? `流程完成 · ${result.analysis_review_status}` : '流程已完成'}
          </span>
        </div>
      </div>
      <article className="summary-card card">
        <div className="summary-label"><span className="summary-spark">✳</span> 业务画像</div>
        <p>{result.business_summary || result.summary || '本次没有生成公司业务摘要；请查看下面各分析分项与覆盖检查。'}</p>
        <EvidenceQuote item={result.business_summary_evidence} runId={run.id} />
        {result.business_summary && result.summary && result.summary !== result.business_summary && <div className="business-summary-note">{result.summary}<EvidenceQuote item={result.summary_evidence} runId={run.id} /></div>}
      </article>

      {result.coverage_checks?.length ? <section className="coverage-checks card">
        <div className="coverage-heading"><div><div className="summary-label">模块一覆盖检查</div><p>逐项显示有出处、待核对或检索缺口。</p></div><span>{result.analysis_review_status || '待人工复核'}</span></div>
        <div className="coverage-grid">{result.coverage_checks.map((item) => <article className={`coverage-item coverage-${item.status}`} key={item.key}>
          <div><strong>{item.name}</strong><span>{coverageStatusLabel(item.status)}</span></div>
          <p>{item.note}</p>
          {!!item.candidate_pages?.length && <div className="fact-pages">候选页：{item.candidate_pages.map((page) => <PagePill page={page} runId={run.id} key={page} />)}</div>}
        </article>)}</div>
        <small>程序只核对页码、摘录文字和计算条件；仍需人工确认表格含义、会计口径和因果解释。</small>
      </section> : <EmptyModuleSection module="模块一" title="模块覆盖检查" />}

      {result.business_flow?.length ? <>
        <div className="section-title-row"><div><span className="section-number">01</span><h3>公司如何经营</h3></div><span className="muted-label">只列年报披露的环节</span></div>
        <div className="business-flow-grid">
          {result.business_flow.map((item, index) => <article className="business-card card" key={item.stage + index}>
            <div className="business-card-top"><span className="business-index">{String(index + 1).padStart(2, '0')}</span><span className="business-status">{item.fact_type}</span></div>
            <h4>{item.stage}</h4><p>{item.description}</p><EvidenceQuote item={item} runId={run.id} />
          </article>)}
        </div>
      </> : <><div className="section-title-row"><div><span className="section-number">01</span><h3>公司如何经营</h3></div></div><EmptyModuleSection module="模块一" title="公司如何经营" /></>}

      {result.industry_context?.length ? <>
        <div className="section-title-row"><div><span className="section-number">02</span><h3>行业背景</h3></div><span className="muted-label">标明来源期间，不把行业描述当作公司事实</span></div>
        <div className="business-list">{result.industry_context.map((item, index) => <article className="business-list-card card" key={item.topic + index}>
          <div className="business-card-top"><h4>{item.topic}</h4><span className="business-status">{item.period || item.source_type}</span></div>
          <p>{item.company_statement}</p>{item.analysis && <p className="coverage-analysis">与公司业务的关系：{item.analysis}</p>}
          {item.limitation && <div className="business-limitation">适用限制：{item.limitation}</div>}<EvidenceQuote item={item} runId={run.id} />
        </article>)}</div>
      </> : <><div className="section-title-row"><div><span className="section-number">02</span><h3>行业背景</h3></div><span className="muted-label">需有年报出处和来源期间</span></div><EmptyModuleSection module="模块一" title="行业背景" /></>}

      {result.strategy_competitiveness?.length ? <>
        <div className="section-title-row"><div><span className="section-number">03</span><h3>公司战略与竞争特点</h3></div><span className="muted-label">区分公司表述、行动结果和分析判断</span></div>
        <div className="business-list">{result.strategy_competitiveness.map((item, index) => <article className="business-list-card card" key={item.aspect + index}>
          <h4>{item.aspect}</h4><p>公司表述：{item.management_statement || '年报未说明'}</p>
          {item.action_or_result && <p>披露的行动或结果：{item.action_or_result}</p>}{item.analysis && <p className="coverage-analysis">分析：{item.analysis}</p>}
          {item.limitation && <div className="business-limitation">尚未证实：{item.limitation}</div>}<EvidenceQuote item={item} runId={run.id} />
        </article>)}</div>
      </> : <><div className="section-title-row"><div><span className="section-number">03</span><h3>公司战略与竞争特点</h3></div></div><EmptyModuleSection module="模块一" title="公司战略与竞争特点" /></>}

      <div className="section-title-row"><div><span className="section-number">04</span><h3>收入结构与变化</h3></div><span className="muted-label">不同拆分口径分别展示，不跨组相加</span></div>
      <article className="revenue-total-card card">
        <div><span className="fact-name">{revenue.metric_name || '收入指标'}</span><div className="revenue-total-value">{formatRawAmount(revenue.current_value, revenue.current_unit)}</div><span className="business-period">{revenue.current_period || result.period}</span>{revenue.reported_yoy && <small className="reported-yoy">年报披露同比：{revenue.reported_yoy}</small>}</div>
        <div><span className="fact-name">上期</span><div className="revenue-prior-value">{formatRawAmount(revenue.previous_value, revenue.previous_unit)}</div><span className="business-period">{revenue.previous_period || '未披露'}</span></div>
        <div><span className="fact-name">程序复算同比</span><div className="revenue-total-value">{totalChange.change_percent != null ? totalChange.change_percent + '%' : '未计算'}</div><span className="business-period">{totalChange.difference_yuan != null ? '差额 ' + formatRawAmount(totalChange.difference_yuan, '元') : '年度、口径或单位不满足计算条件'}</span></div>
        <EvidenceQuote item={revenue} runId={run.id} />
      </article>
      {!!otherRevenueTotals.length && <section className="revenue-denominators card"><div className="subsection-title"><span className="mini-icon">＝</span><h3>其他已披露收入合计口径</h3></div>
        <p>每个合计单独保留；分部占比只使用同指标、同期间和同范围的分母。</p>
        <div className="revenue-total-list">{otherRevenueTotals.map((item, index) => <article key={item.metric_name + index}><div><strong>{item.metric_name}</strong><small>{item.reporting_scope} · {item.currency}</small></div><div>{formatRawAmount(item.current_value, item.current_unit)}<small>{item.current_period}</small></div><div>上期 {formatRawAmount(item.previous_value, item.previous_unit)}<small>{item.previous_period || '未披露'}</small></div>{item.reported_yoy && <div>年报披露同比：{item.reported_yoy}</div>}<EvidenceQuote item={item} runId={run.id} /></article>)}</div>
      </section>}
      {revenueGroups.length ? revenueGroups.map((group) => <RevenueGroupTable group={group} runId={run.id} key={group.basis} />) : <div className="empty-result card">报告中未找到可核实的分部收入表。建议查看年报中的主营业务分析，并确认是否披露了业务或地区拆分。</div>}

      {result.growth_drivers?.length ? <>
        <div className="section-title-row"><div><span className="section-number">05</span><h3>经营变化与驱动因素</h3></div><span className="muted-label">事实、公司解释与推断分别标记</span></div>
        <div className="business-list">{result.growth_drivers.map((item, index) => <article className="business-list-card card" key={item.driver + index}>
          <div className="business-card-top"><h4>{item.driver}</h4><span className="business-status">{item.fact_type}</span></div><p>{item.description}</p>
          {item.limitation && <div className="business-limitation">尚未证实：{item.limitation}</div>}<EvidenceQuote item={item} runId={run.id} />
        </article>)}</div>
      </> : <><div className="section-title-row"><div><span className="section-number">05</span><h3>经营变化与驱动因素</h3></div></div><EmptyModuleSection module="模块一" title="经营变化与驱动因素" /></>}

      {result.industry_metrics?.length ? <>
        <div className="section-title-row"><div><span className="section-number">06</span><h3>行业经营指标</h3></div><span className="muted-label">保留年报原名与单位</span></div>
        <div className="business-metric-grid">{result.industry_metrics.map((item, index) => <article className="business-card card" key={item.name + index}><span className="fact-name">{item.name}</span><div className="business-metric-value">{formatRawAmount(item.value, item.unit)}</div><span className="business-period">{item.period}</span><EvidenceQuote item={item} runId={run.id} /></article>)}</div>
      </> : <><div className="section-title-row"><div><span className="section-number">06</span><h3>行业经营指标</h3></div></div><EmptyModuleSection module="模块一" title="行业经营指标" /></>}
      <BusinessEvidenceList title="07 · 经营依赖与集中度" items={result.dependencies || []} runId={run.id} nameKey="name" detailKey="description" />
      <BusinessEvidenceList title="08 · 重大经营变化" items={result.major_changes || []} runId={run.id} nameKey="change" detailKey="business_effect" metaKey="period" />
      {result.follow_up_checks?.length ? <section className="business-followups card">
        <div className="subsection-title"><span className="mini-icon">↗</span><h3>09 · 交给后续模块继续查</h3></div>
        <div className="business-list">{result.follow_up_checks.map((item, index) => <article className="followup-item" key={item.question + index}><strong>{item.question}</strong><p>{item.reason}</p><span>{item.next_module}</span><EvidenceQuote item={item} runId={run.id} /></article>)}</div>
      </section> : <section className="business-followups card"><div className="subsection-title"><span className="mini-icon">↗</span><h3>09 · 交给后续模块继续查</h3></div><EmptyModuleSection module="模块一" title="后续核查问题" /></section>}
      <BusinessEvidenceList title="10 · 年报列示的经营风险" items={result.risk_factors || []} runId={run.id} nameKey="risk" detailKey="description" />
      {result.uncertainties?.length ? <article className="uncertainty-card"><div className="uncertainty-title"><span>!</span>需要留意</div><ul>{result.uncertainties.map((text, index) => <li key={index}>{text}</li>)}</ul></article> : <article className="uncertainty-card"><div className="uncertainty-title"><span>!</span>尚未单独列出待核问题</div><p>各节中的覆盖状态和空项仍需查看；未列出不代表本报告不存在风险或不确定性。</p></article>}
      <details className="source-details card">
        <summary><span><span className="source-icon">▤</span>本次模型读取的 PDF 页面</span><span className="details-count">{result.read_pages?.length || 0} 页</span></summary>
        <div className="source-list">{result.read_pages?.map((page) => <PagePill page={page} runId={run.id} key={page} />)}<p>页码可点击回看 PDF。引用只允许来自本次读取页面。</p></div>
      </details>
      {!!run.steps?.length && <details className="source-details card workflow-details"><summary><span><span className="source-icon">↻</span>本次分析步骤</span><span className="details-count">{run.steps.length} 步</span></summary><ol className="completed-steps">{run.steps.map((step) => <li key={step.ordinal}><b>{step.name}</b><span>{step.detail || step.status}</span></li>)}</ol></details>}
      <div className="run-footnote"><span>模型：{result.model || 'DeepSeek'}</span><span>已读取 {result.read_pages?.length || 0} / {run.page_count} 页</span>{result.usage?.prompt_tokens != null && <span>本次用量 {result.usage.prompt_tokens + (result.usage.completion_tokens || 0)} tokens</span>}<span>流程版本：{result.prompt_version}</span></div>
    </section>
  )
}

const REVENUE_ROWS_PER_PAGE = 20

function RevenueGroupTable({ group, runId }) {
  const [page, setPage] = useState(0)
  const pageCount = Math.max(1, Math.ceil(group.items.length / REVENUE_ROWS_PER_PAGE))
  const visibleItems = group.items.slice(page * REVENUE_ROWS_PER_PAGE, (page + 1) * REVENUE_ROWS_PER_PAGE)
  useEffect(() => setPage(0), [group.items.length])

  return <section className="revenue-group">
    <div className="revenue-group-heading"><h4>{group.basis}口径</h4>{group.items.length > REVENUE_ROWS_PER_PAGE && <span>共 {group.items.length} 项 · 第 {page + 1}/{pageCount} 页</span>}</div>
    <div className="revenue-table-wrap card"><table className="revenue-table">
      <thead><tr><th>业务分部</th><th>本期收入</th><th>本期占比</th><th>上期收入</th><th>上期占比</th><th>同比</th><th>程序复算同比</th></tr></thead>
      <tbody>{visibleItems.map((item, index) => <tr key={item.name + index}>
        <td><strong>{item.name}</strong>{item.gross_margin_current && <small>本期毛利率 {item.gross_margin_current}</small>}{item.company_explanation && <small>{item.company_explanation}</small>}{item.comparability_note && <em>{item.comparability_note}</em>}</td>
        <td>{formatRawAmount(item.current_value, item.current_unit)}<small>{item.current_period}</small></td>
        <td>{item.current_share_percent != null ? item.current_share_percent + '%' : <><span>未计算</span><small>{item.current_share_note}</small></>}</td>
        <td>{formatRawAmount(item.previous_value, item.previous_unit)}<small>{item.previous_period}</small></td>
        <td>{item.previous_share_percent != null ? item.previous_share_percent + '%' : <><span>未计算</span><small>{item.previous_share_note}</small></>}</td>
        <td>{item.reported_yoy || '年报未提供'}</td>
        <td>{item.calculated_change?.change_percent != null ? item.calculated_change.change_percent + '%' : '未计算'}</td>
      </tr>)}</tbody>
    </table>
    {visibleItems.map((item, index) => <div className="business-row-evidence" key={item.name + '-evidence-' + index}>
      <strong>{item.name} · 分项原文</strong><EvidenceQuote item={item} runId={runId} />
      {!!item.current_share_denominator && <div className="denominator-evidence"><strong>本期占比分母：{item.current_share_denominator.metric_name}</strong><EvidenceQuote item={item.current_share_denominator} runId={runId} /></div>}
      {!!item.previous_share_denominator && <div className="denominator-evidence"><strong>上期占比分母：{item.previous_share_denominator.metric_name}</strong><EvidenceQuote item={item.previous_share_denominator} runId={runId} /></div>}
    </div>)}
    {pageCount > 1 && <div className="revenue-pagination"><button type="button" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>上一页</button><span>{(page * REVENUE_ROWS_PER_PAGE) + 1}—{Math.min((page + 1) * REVENUE_ROWS_PER_PAGE, group.items.length)} / {group.items.length}</span><button type="button" disabled={page + 1 >= pageCount} onClick={() => setPage((value) => value + 1)}>下一页</button></div>}
    </div>
  </section>
}

function coverageStatusLabel(status) {
  return ({
    supported: '有可回查出处',
    needs_review: '需要人工核对',
    candidate_only: '命中候选页',
    search_miss: '检索未命中',
    partial: '部分覆盖',
    not_generated: '尚未生成',
  })[status] || '待人工复核'
}

function BusinessEvidenceList({ title, items, runId, nameKey, detailKey, metaKey }) {
  return <section className="business-evidence-section card"><div className="subsection-title"><span className="mini-icon">↗</span><h3>{title}</h3></div>
    {items?.length ? <div className="business-list">{items.map((item, index) => <article className="business-list-card" key={item[nameKey] + index}><div className="business-card-top"><h4>{item[nameKey]}</h4>{metaKey && <span className="business-period">{item[metaKey]}</span>}</div><p>{item[detailKey] || '年报未说明具体影响。'}</p><EvidenceQuote item={item} runId={runId} /></article>)}</div> : <EmptyModuleSection module="模块一" title={title} />}
  </section>
}

function ConversationPanel({ run, turns, question, onQuestionChange, onAsk, error, busy }) {
  if (!run || run.status !== 'completed' || !run.result) return null
  return (
    <section className="conversation-card card">
      <div className="subsection-title"><span className="mini-icon">↳</span><h3>继续追问这份年报</h3></div>
      <p className="conversation-intro">可问具体指标、变化原因或原文位置。系统会沿用本次任务，并按需查阅其他页。</p>
      {turns.length > 0 && <div className="conversation-list" aria-live="polite">
        {turns.map((turn) => <article className="conversation-turn" key={turn.id}>
          <div className="conversation-question">{turn.question}</div>
          {turn.status === 'queued' || turn.status === 'running'
            ? <div className="conversation-pending"><span className="button-spinner" />正在结合年报查找依据…</div>
            : turn.status === 'failed'
              ? <div className="conversation-error">{turn.error || '这次追问没有完成。'}</div>
              : <div className="conversation-answer"><MarkdownAnswer text={turn.answer} runId={run.id} /></div>}
        </article>)}
      </div>}
      <form className="question-form" onSubmit={(event) => { event.preventDefault(); onAsk() }}>
        <textarea
          value={question}
          onChange={(event) => onQuestionChange(event.target.value)}
          placeholder="例如：经营现金流和净利润的变化一致吗？"
          maxLength={1200}
          rows={2}
          disabled={busy}
        />
        <div className="question-controls"><span>{question.length}/1200</span><button className="primary-button" type="submit" disabled={busy || !question.trim()}>{busy ? '正在查阅…' : '追问'}</button></div>
      </form>
      {error && <div className="inline-error" role="alert">{error}</div>}
    </section>
  )
}

function FailedPanel({ run }) {
  if (!run || run.status !== 'failed') return null
  return (
    <section className="failed-card card" role="alert">
      <div className="failed-icon">!</div>
      <div>
        <div className="failed-title">这次分析没有完成</div>
        <p>{run.error || '处理失败，请重新提交文件。'}</p>
        <span className="failed-file">{run.file_name} · {formatTime(run.created_at)}</span>
      </div>
    </section>
  )
}

function EmptyPanel({ run }) {
  if (run) return null
  return (
    <section className="welcome-card">
      <div className="welcome-illustration" aria-hidden="true">
        <div className="paper paper-back"><i /><i /><i /></div>
        <div className="paper paper-front"><span className="paper-chart"><b /><b /><b /><b /><b /></span><i /><i /><i /></div>
        <span className="sparkle sparkle-one">✦</span><span className="sparkle sparkle-two">✧</span>
      </div>
      <div className="eyebrow">简单 · 可追溯 · 由 AI 阅读</div>
      <h2>让财报里的重点<br /><em>清楚浮现。</em></h2>
      <p>上传一份上市公司年报，DeepSeek 会阅读关键页面，整理主要财务数据和变化。</p>
      <div className="welcome-tags"><span>营业收入</span><span>归母净利润</span><span>经营现金流</span></div>
    </section>
  )
}

function DeepSeekKeyModal({ open, saving, error, onSave, onClose, configured }) {
  const [apiKey, setApiKey] = useState('')
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (!open) {
      setApiKey('')
      setVisible(false)
    }
  }, [open])

  if (!open) return null

  return (
    <div className="modal-backdrop">
      <section className="key-modal card" role="dialog" aria-modal="true" aria-labelledby="key-modal-title">
        <button className="modal-close" type="button" onClick={onClose} aria-label="关闭配置窗口">×</button>
        <div className="modal-mark"><BrandMark /></div>
        <div className="eyebrow">开始使用前</div>
        <h2 id="key-modal-title">{configured ? '更新 DeepSeek Key' : '先连接 DeepSeek'}</h2>
        <p className="modal-description">配置后，工作台会调用 DeepSeek 阅读你上传的财报，并把结果显示在这里。</p>

        <form onSubmit={(event) => { event.preventDefault(); onSave(apiKey) }}>
          <label className="key-label" htmlFor="deepseek-api-key">DeepSeek API Key</label>
          <div className="key-input-wrap">
            <input
              id="deepseek-api-key"
              type={visible ? 'text' : 'password'}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="粘贴你的 API Key"
              autoComplete="off"
              spellCheck="false"
              autoFocus
            />
            <button className="key-visibility" type="button" onClick={() => setVisible((value) => !value)}>
              {visible ? '隐藏' : '显示'}
            </button>
          </div>
          {error && <div className="key-error" role="alert">{error}</div>}
          <div className="key-privacy">
            <span aria-hidden="true">▣</span>
            密钥保存在本机项目配置中，不存入浏览器缓存。调用时由本机服务发送给 DeepSeek。
          </div>
          <div className="modal-actions">
            {!configured && <button className="quiet-button" type="button" onClick={onClose}>稍后配置</button>}
            <button className="primary-button key-save-button" type="submit" disabled={saving || !apiKey.trim()}>
              {saving ? <><span className="button-spinner" />正在保存</> : '保存并继续'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

export default function App() {
  const [health, setHealth] = useState({ api_configured: false })
  const [activeRun, setActiveRun] = useState(null)
  const [recentRuns, setRecentRuns] = useState([])
  const [loadingRun, setLoadingRun] = useState(false)
  const [keyModalOpen, setKeyModalOpen] = useState(false)
  const [savingKey, setSavingKey] = useState(false)
  const [keyError, setKeyError] = useState('')
  const [conversation, setConversation] = useState([])
  const [analysisModule, setAnalysisModule] = useState('complete')
  const [question, setQuestion] = useState('')
  const [conversationError, setConversationError] = useState('')

  const loadRun = useCallback(async (id) => {
    try {
      const response = await fetch(`${API}/runs/${id}`)
      if (!response.ok) throw new Error(await apiError(response))
      const data = await response.json()
      setActiveRun(data)
      return data
    } catch (error) {
      setActiveRun((current) => current?.id === id ? { ...current, status: 'failed', error: error.message } : current)
      return null
    }
  }, [])

  useEffect(() => {
    if (activeRun?.id) setAnalysisModule(activeRun.analysis_module || 'overview')
  }, [activeRun?.id, activeRun?.analysis_module])

  const loadRecent = useCallback(async () => {
    try {
      const response = await fetch(`${API}/runs`)
      if (!response.ok) return
      const data = await response.json()
      setRecentRuns(data.runs || [])
    } catch { /* the top-level server status reports an unavailable backend */ }
  }, [])

  const loadConversation = useCallback(async (id) => {
    try {
      const response = await fetch(`${API}/runs/${id}/conversation`)
      if (!response.ok) return
      const data = await response.json()
      setConversation(data.turns || [])
    } catch { /* conversation history stays local to this task */ }
  }, [])

  useEffect(() => {
    fetch(`${API}/health`).then((response) => response.json()).then((data) => {
      setHealth(data)
      if (!data.api_configured && !window.localStorage.getItem('finlab-key-prompt-dismissed')) setKeyModalOpen(true)
    }).catch(() => setHealth({ api_configured: false, unavailable: true }))
    loadRecent()
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
    }, 1200)
    return () => window.clearInterval(timer)
  }, [activeRun?.id, activeRun?.status, loadRun, loadRecent, loadConversation])

  useEffect(() => {
    const id = activeRun?.id
    if (!id || !conversation.some((turn) => turn.status === 'queued' || turn.status === 'running')) return undefined
    const timer = window.setInterval(() => loadConversation(id), 1300)
    return () => window.clearInterval(timer)
  }, [activeRun?.id, conversation, loadConversation])

  const runFile = async (file, selectedModule, setError) => {
    setLoadingRun(true)
    setActiveRun(null)
    setConversation([])
    setQuestion('')
    setConversationError('')
    const form = new FormData()
    form.append('file', file)
    form.append('analysis_module', selectedModule)
    try {
      const response = await fetch(`${API}/runs`, { method: 'POST', body: form })
      if (!response.ok) throw new Error(await apiError(response))
      const run = await response.json()
      setActiveRun(run)
      await loadRecent()
    } catch (error) {
      setError(error.message || '无法连接服务，请确认后端已经启动。')
    } finally {
      setLoadingRun(false)
    }
  }

  const openRun = async (id) => {
    setLoadingRun(true)
    await loadRun(id)
    await loadConversation(id)
    setQuestion('')
    setConversationError('')
    setLoadingRun(false)
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
      setConversationError(error.message || '暂时无法提交追问，请稍后再试。')
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
      window.localStorage.removeItem('finlab-key-prompt-dismissed')
    } catch (error) {
      setKeyError(error.message || '保存失败，请检查本机服务后重试。')
    } finally {
      setSavingKey(false)
    }
  }

  const closeKeyModal = () => {
    setKeyModalOpen(false)
    setKeyError('')
    if (!health.api_configured) window.localStorage.setItem('finlab-key-prompt-dismissed', '1')
  }

  const busy = loadingRun || BUSY.has(activeRun?.status)

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="完整财报分析首页">
          <BrandMark />
          <span className="brand-name">林研<span>FINLAB · V3.0.1</span></span>
        </a>
        <nav className="top-nav" aria-label="主导航"><span className="nav-current">完整财报分析</span><a className="nav-coming" href="#module-one">模块一 · 业务与经营</a><a className="nav-coming" href="#module-two">模块二 · 盈利与变化</a></nav>
        <button className="model-status model-status-button" type="button" onClick={() => { setKeyError(''); setKeyModalOpen(true) }}>
          <span className={health.api_configured ? 'status-led led-on' : 'status-led'} />
          {health.api_configured ? 'DeepSeek 已配置' : '配置 DeepSeek Key'}
          <span className="status-edit">设置</span>
        </button>
      </header>

      <main id="top" className="main-layout">
        <div className="page-intro">
          <div className="breadcrumb"><span>工作台</span><b>/</b><span className="breadcrumb-current">财务分析</span></div>
          <div className="hero-copy">
            <div className="eyebrow"><span className="hero-dot" />AI 财报分析工作台</div>
            <h1>先看经营，<span>再拆盈利。</span></h1>
            <p>上传一份年报，分别得到模块一经营分析和模块二盈利分析。每块都保留数据、依据、出处和待核问题。</p>
          </div>
        </div>

        <div className="work-grid">
          <div className="left-column">
            <UploadPanel
              onRun={runFile}
              busy={busy}
              apiConfigured={!!health.api_configured}
              activeRun={activeRun}
              analysisModule={analysisModule}
              onModuleChange={setAnalysisModule}
              onNew={() => { setActiveRun(null); setConversation([]); setQuestion(''); setConversationError('') }}
            />
            {recentRuns.length > 0 && (
              <section className="recent-card card">
                <div className="recent-heading"><h3>最近的分析</h3><span>{recentRuns.length} 条</span></div>
                <div className="recent-list">
                  {recentRuns.slice(0, 6).map((run) => (
                    <button className={`recent-item ${activeRun?.id === run.id ? 'recent-active' : ''}`} key={run.id} onClick={() => openRun(run.id)}>
                      <span className="recent-pdf-icon">PDF</span>
                      <span className="recent-file-name">{run.file_name}<small>{run.analysis_module === 'complete' ? '完整分析 · 模块一 + 模块二' : run.analysis_module === 'profit' ? '模块二' : run.analysis_module === 'business' ? '模块一' : '旧版财报概览'}</small></span>
                      <span className={`recent-status ${run.analysis_module === 'complete' && String(run.stage || '').startsWith('部分完成') ? 'recent-partial' : `recent-${run.status}`}`}>{run.analysis_module === 'complete' && String(run.stage || '').startsWith('部分完成') ? '部分完成' : run.status === 'completed' ? '完成' : run.status === 'failed' ? '失败' : '进行中'}</span>
                    </button>
                  ))}
                </div>
              </section>
            )}
            <div className="workflow-note"><span className="workflow-count">01—02</span><span><b>一份年报，两个完整模块</b><small>业务与经营背景 · 盈利来源与变化</small></span><span className="workflow-arrow">↗</span></div>
          </div>

          <div className="right-column">
            {!activeRun && <EmptyPanel />}
            <RunProgress run={activeRun} />
            <FailedPanel run={activeRun} />
            <ResultPanel run={activeRun} />
            <ConversationPanel
              run={activeRun}
              turns={conversation}
              question={question}
              onQuestionChange={setQuestion}
              onAsk={askQuestion}
              error={conversationError}
              busy={conversation.some((turn) => turn.status === 'queued' || turn.status === 'running')}
            />
          </div>
        </div>
      </main>

      <footer className="footer"><span>林研 · 金融投研工作台</span><span>V3.0.1 · 模块一与模块二分别呈现 <b>·</b> 引用可回查，结论仍需人工复核</span></footer>
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
