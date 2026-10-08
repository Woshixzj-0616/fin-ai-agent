import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const front = path.join(root, 'frontend')
const modules = [
  path.join(front, 'node_modules'),
  'D:\\ChatGPT项目\\金融AI智能体_V3.1.3\\frontend\\node_modules',
  'D:\\ChatGPT项目\\金融AI智能体\\frontend\\node_modules',
].find((directory) => existsSync(path.join(directory, 'vite', 'package.json')))
const require = createRequire(pathToFileURL(path.join(modules, 'react', 'package.json')))
const esbuild = require('esbuild')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const source = readFileSync(path.join(front, 'src', 'V2Workbench.jsx'), 'utf8')
const compiled = await esbuild.transform(source, { loader: 'jsx', format: 'cjs', jsx: 'automatic', target: 'node20' })
const componentModule = { exports: {} }
new Function('require', 'module', 'exports', compiled.code)(require, componentModule, componentModule.exports)
const { BusinessResultPanel } = componentModule.exports
const runId = '778677724c2a47b3ad7f4e57cc604974'
const attemptId = 'cc901df03365'
const saved = JSON.parse(readFileSync(path.join(root, 'data', '开发运行', runId, attemptId, 'result.json'), 'utf8'))
const result = saved.result
assert.ok(result && result.module === 'business', '历史样例应为模块一结果')
const html = renderToStaticMarkup(React.createElement(BusinessResultPanel, {
  run: { id: runId, file_name: '已保存年报', page_count: 270, steps: [] },
  result,
}))
const requiredLabels = [
  '收入结构与变化',
  '本年报动态经营专题',
  '行业与战略解释 · 待映射复核',
  '分角色证据与核对状态',
  '经营依赖与集中度',
  '重大经营变化',
]
for (const label of requiredLabels) assert.ok(html.includes(label), `结果页缺少：${label}`)
assert.equal((html.match(/class="[^"]*\btopic-card\b/g) || []).length, result.topics.length, '动态专题应逐项完整展示')
assert.equal((html.match(/class="business-row-evidence/g) || []).length, result.revenue_segments.length, '每条收入分部应保留行证据区')
const firstTableId = result.revenue_segments[0]?.table_id
assert.ok(!firstTableId || !html.includes(firstTableId), '普通用户界面不应暴露内部 table_id')
if (result.revenue_total?.total_origin == null) {
  assert.ok(html.includes('合计来源未记录，需复核'), '旧结果未记录合计来源时必须明确待复核')
  assert.ok(!html.includes('合计来源：年报列示值'), '不能把缺失来源误显示成年报列示值')
}

const enriched = structuredClone(result)
enriched.revenue_total = {
  ...enriched.revenue_total,
  total_origin: 'derived_from_reported_complete_segments',
  components: [{ name: '测试分项', current_value: '12.5', current_unit: '万元', source_pages: [20] }],
  reconciliation_to_reported_total: {
    status: 'differs_from_reported_total',
    reported_total_value: '100',
    reported_total_unit: '万元',
    difference_value: '2.5',
    unit: '万元',
    reported_total_source_pages: [21],
  },
}
enriched.revenue_segments[0] = { ...enriched.revenue_segments[0], share_change_percentage_points: '1.25' }
enriched.revenue_metric_bridges = [{
  table_name: '测试原表', table_dimension: '整体', higher_metric: '营业总收入', higher_value: '100', higher_unit: '万元',
  lower_metric: '营业收入', lower_value: '90', lower_unit: '万元', difference_yuan: '100000', unit: '元',
  current_period: '2024年度', reporting_scope: '合并口径', evidence_status: 'both_reported_rows_title_header_unit_and_values_matched', source_pages: [22],
}]
const enrichedHtml = renderToStaticMarkup(React.createElement(BusinessResultPanel, {
  run: { id: runId, file_name: '字段展示样例', page_count: 270, steps: [] },
  result: enriched,
}))
for (const label of ['1.25 个百分点', '程序计算明细与年报合计勾稽', '测试分项', '程序加总与年报合计有差异', '测试原表', '100 万元', '100,000 元']) {
  assert.ok(enrichedHtml.includes(label), `专业字段展示检查失败：${label}`)
}
console.log(JSON.stringify({
  report: result.company,
  revenue_rows: result.revenue_segments.length,
  dynamic_topics: result.topics.length,
  visible_sections: requiredLabels.length,
  internal_table_id_hidden: !firstTableId || !html.includes(firstTableId),
  legacy_total_origin_shown_as_review: result.revenue_total?.total_origin == null,
  enriched_revenue_fields_rendered: true,
  result_html_bytes: Buffer.byteLength(html, 'utf8'),
}, null, 2))
