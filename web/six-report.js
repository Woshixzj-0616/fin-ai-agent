/* The two six-module panes use the original V4 report and workpaper data models. */
window.FinScopeSixReport = (() => {
  const sectionMap = {
    business: [['business_summary','经营概览'],['business_flow','业务流程'],['revenue_total','营业收入总额'],['revenue_totals','收入变化'],['revenue_segments','收入分项'],['revenue_metric_bridges','收入指标勾稽'],['growth_drivers','增长驱动'],['industry_context','行业背景'],['strategy_competitiveness','战略与竞争力'],['interpretation_review_items','解释待复核'],['topics','专题'],['risk_factors','经营风险'],['coverage_checks','覆盖检查'],['follow_up_checks','后续核查'],['uncertainties','不确定事项']],
    profit: [['summary','盈利概览'],['profit_lines','利润表关键项目'],['profit_metrics','盈利指标'],['profit_trajectory','利润变化'],['profit_bridge','利润变化桥'],['profit_checks','勾稽核对'],['business_segments','业务盈利'],['nonrecurring_items','非经常性损益'],['findings','重点发现'],['analysis_blocks','专题分析'],['coverage_checks','覆盖检查'],['follow_up_checks','后续核查']],
    assets: [['summary','资产概览'],['asset_map','资产结构'],['receivables','应收及资金占用'],['inventory','存货'],['long_term_assets','长期资产'],['efficiency','经营效率'],['findings','重点发现'],['quality_issues','质量提醒'],['facts','事实底表'],['calculations','程序计算'],['evidence_notes','证据说明'],['handoff','交接线索'],['limitations','限制与待核实']],
    cashflow: [['overall_view','现金流概览'],['sections','分析板块'],['topics','专题'],['facts','事实底表'],['calculations','程序计算'],['review_flags','复核提示'],['follow_up_questions','后续核查问题'],['limitations','限制与待核实']],
    solvency: [['headline','偿债概览'],['debt_structure','债务结构'],['maturity_profile','到期安排'],['funding_sources','资金来源'],['cash_availability','可用现金'],['support_metrics','偿债指标'],['obligations','其他义务'],['findings','重点发现'],['facts','事实底表'],['calculations','程序计算'],['evidence_review','证据复核'],['limitations','限制与待核实']],
    disclosure: [['executive_summary','披露概览'],['overview','披露总览'],['audit_profile','审计信息'],['findings','披露发现'],['impacts','可能影响'],['coverage','检查范围'],['calculations','程序计算'],['run_quality','分析完整度'],['limitations','限制与待核实']]
  };
  const labels = {value:'数值',unit:'单位',current_value:'本期数',previous_value:'上期数',current_period:'本期期间',previous_period:'上期期间',period_label:'报告期间',reporting_scope:'报表口径',measurement_basis:'计量口径',summary:'摘要',analysis:'分析',conclusion:'结论',observation:'观察',interpretation:'解释',follow_up:'后续核查',limitations:'限制',source_pages:'来源页码',evidence_refs:'证据引用',page:'原文页',quote:'年报摘录',formula:'计算公式',output:'计算结果',title:'标题',label:'项目',name:'名称',metric:'指标',status:'状态',note:'说明',fact_id:'事实编号',calculation_id:'计算编号',original_label:'报表项目',risk:'风险',detail:'详情'};
  const node=(tag,cls,text)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined&&text!==null)el.textContent=String(text);return el};
  const titleFor=(key)=>labels[key]||String(key).replaceAll('_',' ');
  const isPage=(key,value)=>/^(page|page_number|pdf_page)$/.test(key)&&Number.isInteger(Number(value))&&Number(value)>0;
  function pages(parent,refs,onPage){
    const numbers=[...new Set((Array.isArray(refs)?refs:[refs]).map(ref=>Number(ref&&typeof ref==='object'?(ref.page||ref.page_number):ref)).filter(value=>Number.isInteger(value)&&value>0))];
    if(!numbers.length)return;
    const wrap=node('span','six-pro-pages');
    numbers.forEach(page=>{const button=node('button','',`年报 ${page} 页 ↗`);button.type='button';button.onclick=()=>onPage(page);wrap.append(button)});
    parent.append(wrap);
  }
  function valueNode(parent,label,value,onPage,depth=0){
    if(value===null||value===undefined||value==='')return;
    if(typeof value!=='object'){
      const row=node('div','six-pro-leaf'),key=node('span','',titleFor(label)),body=node('div','');
      if(isPage(label,value)){const button=node('button','six-pro-page-link',`${value} · 查看原文`);button.onclick=()=>onPage(Number(value));body.append(button)}
      else body.textContent=typeof value==='boolean'?(value?'是':'否'):String(value);
      row.append(key,body);parent.append(row);return;
    }
    if(Array.isArray(value)&&!value.length)return;
    if(!Array.isArray(value)&&!Object.keys(value).length)return;
    const details=node('details',`six-pro-node six-pro-depth-${Math.min(depth,3)}`);details.open=depth===0;
    details.append(node('summary','',`${titleFor(label)} · ${Array.isArray(value)?value.length:Object.keys(value).length} 项`));
    const content=node('div','six-pro-children');
    if(Array.isArray(value)){
      value.forEach((item,index)=>{
        if(item&&typeof item==='object'){
          const card=node('article','six-pro-record');
          card.append(node('h5','',item.title||item.name||item.label||item.metric||item.original_label||item.fact_id||item.calculation_id||`条目 ${index+1}`));
          for(const [key,entry] of Object.entries(item))valueNode(card,key,entry,onPage,depth+1);
          if(item.evidence_refs)pages(card,item.evidence_refs,onPage);
          if(item.source_pages)pages(card,item.source_pages,onPage);
          card.append();content.append(card);
        }else valueNode(content,`条目 ${index+1}`,item,onPage,depth+1);
      });
    }else{
      for(const [key,entry] of Object.entries(value)){
        if(['evidence_refs','source_pages','overall_source_pages'].includes(key)){pages(content,entry,onPage);continue}
        valueNode(content,key,entry,onPage,depth+1);
      }
    }
    details.append(content);parent.append(details);
  }
  function renderModule(root,payload,moduleId,onPage){
    root.classList.add('six-pro-workpaper');
    const fields=sectionMap[moduleId]||[];
    const summary=payload.summary||payload.business_summary||payload.overall_view||payload.headline||payload.executive_summary;
    const summaryText=typeof summary==='string'?summary:summary?.summary||summary?.text||'';
    if(summaryText)root.append(node('p','six-pro-summary',summaryText.length>260?summaryText.slice(0,260)+'…':summaryText));
    let count=0;
    for(const [key,label] of fields){
      const value=payload[key];
      if(value===undefined||value===null||value===''||(Array.isArray(value)&&!value.length))continue;
      const section=node('details','six-pro-section');section.open=key==='findings';section.append(node('summary','',label));
      valueNode(section,label,value,onPage);
      root.append(section);count++;
    }
    if(!count)root.append(node('p','six-pro-empty','该模块未交付可阅读的结构化分析。'));
    const raw=node('details','six-pro-raw');raw.append(node('summary','','查看完整原始结构化结果'));
    raw.append(node('pre','',JSON.stringify(payload,null,2)));root.append(raw);
  }
  function renderEditorial(root,report,research,onPage,onModule,reportId){
    root.replaceChildren();root.classList.add('six-editorial');root.classList.remove('six-markdown');
    const hero=node('header','six-editorial-hero');
    const kicker=node('div','six-editorial-kicker');kicker.append(node('span','','FIN SCOPE / COMPANY REVIEW'),node('span','',report.status||'本地研究样稿'));hero.append(kicker);
    hero.append(node('h1','',report.title),node('p','six-editorial-subtitle',report.subtitle));
    const meta=node('div','six-editorial-meta');[report.company,report.period,report.scope].filter(Boolean).forEach(value=>meta.append(node('span','',value)));hero.append(meta);root.append(hero);
    const body=node('div','six-editorial-body');
    const lead=node('section','six-editorial-lead');lead.append(node('small','','核心判断 / EXECUTIVE VIEW'),node('h2','',report.headline||report.sections?.at(-1)?.title||'综合判断'),node('p','',report.lead));body.append(lead);
    const highlights=node('div','six-editorial-highlights');
    (report.highlights||[]).forEach(item=>{const card=node('article','');card.append(node('small','',item.label),node('h3','',item.title),node('p','',item.detail));pages(card,item.pages,onPage);highlights.append(card)});body.append(highlights);
    const snapshot=node('section','six-editorial-snapshot');snapshot.append(node('small','','FINANCIAL SNAPSHOT'),node('h2','','关键财务数据'));
    const tableWrap=node('div','six-editorial-scroll'),table=node('table',''),head=node('thead',''),tr=node('tr','');
    ['指标',report.period||'本期','上期','变化','来源'].forEach(text=>tr.append(node('th','',text)));head.append(tr);table.append(head);
    const tbody=node('tbody','');(report.metrics||[]).forEach(item=>{const row=node('tr','');row.append(node('th','',item.label),node('td','',`${item.current||'—'} ${item.unit||''}`),node('td','',item.previous||'—'),node('td',String(item.change||'').startsWith('−')?'negative':'positive',item.change||'—'));const source=node('td','');pages(source,item.pages,onPage);row.append(source);tbody.append(row)});table.append(tbody);tableWrap.append(table);snapshot.append(tableWrap);body.append(snapshot);
    const toc=node('nav','six-editorial-toc');toc.append(node('small','','阅读目录'));const links=node('div','');(report.sections||[]).forEach(section=>{const a=node('a','',`${section.number}  ${section.title}`);a.href=`#six-editorial-${section.id}`;links.append(a)});toc.append(links);body.append(toc);
    (report.sections||[]).forEach(section=>{
      const wrap=node('section','six-editorial-section');wrap.id=`six-editorial-${section.id}`;
      wrap.append(node('small','',`${section.number} / ANALYSIS`),node('h2','',section.title),node('p','six-editorial-section-intro',section.summary));
      for(const block of section.blocks||[]){
        if(block.type==='table'){
          const figure=node('figure','six-editorial-figure');figure.append(node('figcaption','',block.title));const scroll=node('div','six-editorial-scroll'),t=node('table',''),thead=node('thead',''),heading=node('tr','');
          (block.columns||[]).forEach(col=>heading.append(node('th','',col)));thead.append(heading);t.append(thead);
          const rows=node('tbody','');(block.rows||[]).forEach(cells=>{const row=node('tr','');cells.forEach((cell,i)=>row.append(node(i===0?'th':'td','',cell)));rows.append(row)});t.append(rows);scroll.append(t);figure.append(scroll);
          const footer=node('footer','');footer.append(node('span','',block.unit||''));pages(footer,block.pages,onPage);figure.append(footer);wrap.append(figure);
        }else{const para=node('div',block.type==='note'?'six-editorial-note':'six-editorial-paragraph');para.append(node('p','',block.text));pages(para,block.pages,onPage);wrap.append(para)}
      }
      body.append(wrap);
    });
    const footer=node('footer','six-editorial-footer');footer.append(node('b','','资料与计算口径'),node('p','',`${report.source_title||''}。${report.method||''}`));const pdf=node('a','','打开完整年报 PDF ↗');pdf.href=`/api/six/reports/${reportId}/file`;pdf.target='_blank';pdf.rel='noopener';footer.append(pdf);body.append(footer);
    const work=node('details','six-editorial-workpapers');work.append(node('summary','','查看六模块研究底稿与复核过程'),node('p','',research.editorial_audit?.scope_note||'可回看模块分析与年报原文。'));const buttons=node('div','');(research.modules||[]).forEach(item=>{const btn=node('button','',`${item.module_label||item.module_id} ↗`);btn.onclick=()=>onModule(item.module_id);buttons.append(btn)});work.append(buttons);body.append(work);
    root.append(body);
  }
  function enhanceMarkdown(root){
    const original=[...root.children],chapters=original.filter(el=>el.tagName==='H2'&&/^\d{2}\s/.test(el.textContent.trim()));
    if(!chapters.length)return;
    const first=original.indexOf(chapters[0]),intro=original.slice(1,first),hero=node('header','six-editorial-hero'),body=node('div','six-editorial-body');
    const kicker=node('div','six-editorial-kicker');kicker.append(node('span','','FINSCOPE / FINANCIAL RESEARCH'),node('span','','本地财报研究'));hero.append(kicker);
    hero.append(node('h1','',original[0]?.tagName==='H1'?original[0].textContent:'财报分析报告'));
    const leadTitle=intro.find(el=>el.tagName==='P'&&el.textContent.trim()==='核心判断');
    const leadText=leadTitle&&intro[intro.indexOf(leadTitle)+1];
    const lead=node('section','six-editorial-lead');lead.append(node('small','','核心判断 / EXECUTIVE VIEW'));
    if(leadText?.tagName==='P'){lead.append(leadText);intro.splice(intro.indexOf(leadText),1)}
    if(leadTitle)intro.splice(intro.indexOf(leadTitle),1);
    body.append(lead);
    const highlightsHeading=intro.find(el=>el.tagName==='P'&&el.textContent.trim()==='重要发现');
    const highlightsList=highlightsHeading&&intro[intro.indexOf(highlightsHeading)+1];
    if(highlightsList?.tagName==='OL'){
      const grid=node('div','six-editorial-highlights');
      [...highlightsList.children].forEach((item,index)=>{const card=node('article','');card.append(node('small','',`发现 ${String(index+1).padStart(2,'0')}`));const title=item.querySelector('strong');if(title){card.append(node('h3','',title.textContent));title.remove()}const p=node('p','');while(item.firstChild)p.append(item.firstChild);card.append(p);grid.append(card)});
      body.append(grid);intro.splice(intro.indexOf(highlightsList),1);
    }
    if(highlightsHeading)intro.splice(intro.indexOf(highlightsHeading),1);
    const snapshotHeading=intro.find(el=>el.tagName==='P'&&el.textContent.trim()==='关键财务数据');
    const snapshotTable=snapshotHeading&&intro[intro.indexOf(snapshotHeading)+1];
    if(snapshotTable?.classList.contains('six-table-wrap')){
      const snapshot=node('section','six-editorial-snapshot');snapshot.append(node('small','','FINANCIAL SNAPSHOT'),node('h2','','关键财务数据'));snapshotTable.classList.add('six-editorial-scroll');snapshot.append(snapshotTable);body.append(snapshot);
      intro.splice(intro.indexOf(snapshotTable),1);
    }
    if(snapshotHeading)intro.splice(intro.indexOf(snapshotHeading),1);
    if(intro.length){const notes=node('section','six-generated-context');notes.append(node('h2','','报告口径与待核事项'));intro.forEach(el=>notes.append(el));body.append(notes)}
    const toc=node('nav','six-editorial-toc');toc.append(node('small','','阅读目录'));const links=node('div','');chapters.forEach((chapter,index)=>{const a=node('a','',chapter.textContent);a.href=`#six-generated-chapter-${index+1}`;links.append(a)});toc.append(links);body.append(toc);
    chapters.forEach((chapter,index)=>{
      const start=original.indexOf(chapter),end=index+1<chapters.length?original.indexOf(chapters[index+1]):original.length,wrap=node('section','six-editorial-section six-generated-chapter');wrap.id=`six-generated-chapter-${index+1}`;
      wrap.append(node('small','',`${String(index+1).padStart(2,'0')} / ANALYSIS`),chapter);
      let previous=chapter.textContent.trim();
      for(const el of original.slice(start+1,end)){
        if(el.tagName==='H2'&&el.textContent.trim()===previous)continue;
        if(el.tagName==='H2'&&el.textContent.trim()!==previous){el.classList.add('six-generated-subhead');previous=el.textContent.trim()}
        if(el.classList.contains('six-table-wrap'))el.classList.add('six-editorial-scroll');
        wrap.append(el);
      }
      body.append(wrap);
    });
    root.replaceChildren(hero,body);root.classList.add('six-editorial','six-generated-report');root.classList.remove('six-markdown');
  }
  return {renderModule,renderEditorial,enhanceMarkdown};
})();
