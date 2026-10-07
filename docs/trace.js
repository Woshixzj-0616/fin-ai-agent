/* 两套网页共用真实事件的展示组件。所有来源文本经过 HTML 转义。 */
window.announcementFieldLabels = {pledgor:'质押股东',shares:'股份数量',pledgee:'质权人',start_date:'质押起始日',end_date:'质押到期日',purpose:'用途',ratio_percent:'占总股本比例',project_name:'项目名称',winner:'中标主体',customer:'招标或采购方',amount:'金额',announcement_date:'通知或公告日期',term:'期限',seller:'转让方',buyer:'受让方',change_date:'股权变动日期',change_method:'变动方式'};
window.announcementStatusLabels = {extracted:'已提取',needs_review:'需复核',missing:'缺失',conflict:'冲突',no_fields:'未提取到字段'};
window.renderExecutionTrace = function (container, steps, linkEvidence) {
  const escape = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  container.innerHTML = (steps || []).map(s => {
    const chips = (s.evidence_ids || []).map(id => `<button class="trace-evidence" type="button" data-id="${escape(id)}">${escape(id)}</button>`).join(' ');
    return `<details class="trace-step"><summary>${escape(s.step)}. ${escape(s.title)} <span class="muted">· ${escape(s.actor)}</span></summary><p>${escape(s.rule)}</p>${chips}<pre>${escape(JSON.stringify(s.detail || {}, null, 2))}</pre></details>`;
  }).join('') || '<p class="muted">本次记录没有可展示步骤。</p>';
  container.querySelectorAll('.trace-evidence').forEach(b => b.addEventListener('click', () => linkEvidence(b.dataset.id)));
};
