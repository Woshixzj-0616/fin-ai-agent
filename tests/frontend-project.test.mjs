import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

let fixtureNumber = 0;

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this.attributes = {};
    this.queries = {};
    this.value = '';
    this.files = [];
    this.style = { removeProperty() {}, setProperty() {} };
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(' '); }
  set textContent(value) { this.text = String(value ?? ''); this.replaceChildren(); }
  get firstChild() { return this.children[0] || null; }
  get nextSibling() {
    return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1] || null;
  }
  append(...nodes) {
    for (const node of nodes) {
      node.remove?.();
      this.children.push(node);
      node.parentNode = this;
    }
  }
  replaceChildren(...nodes) {
    for (const node of this.children) node.parentNode = null;
    this.children = [];
    this.append(...nodes);
  }
  insertBefore(node, before) {
    if (!before) { this.append(node); return; }
    node.remove();
    this.children.splice(this.children.indexOf(before), 0, node);
    node.parentNode = this;
  }
  remove() {
    if (this.parentNode) {
      const index = this.parentNode.children.indexOf(this);
      if (index >= 0) this.parentNode.children.splice(index, 1);
      this.parentNode = null;
    }
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  async dispatch(type, event = {}) {
    await Promise.all((this.listeners[type] || []).map(handler => handler({
      preventDefault() {}, target: this, ...event,
    })));
  }
  closest(selector) {
    if (selector === '[data-run-id]' && this.dataset.runId) return this;
    return this.parentNode?.closest(selector) || null;
  }
  querySelector(selector) {
    return this.queries[selector] ||= new Element(selector === 'button[type="submit"]' ? 'button' : 'span');
  }
  querySelectorAll() { return []; }
  focus() { globalThis.document.activeElement = this; }
  reset() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function settle() {
  for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve));
}

async function fixture({ first = 'A', status = 'succeeded', result = {} } = {}) {
  const roots = new Map();
  const node = selector => {
    if (!roots.has(selector)) roots.set(selector, new Element());
    return roots.get(selector);
  };
  globalThis.document = {
    body: new Element('body'),
    querySelector: node,
    createElement: tag => new Element(tag),
    addEventListener() {},
  };
  globalThis.window = { addEventListener() {} };
  globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
  globalThis.requestAnimationFrame = callback => setImmediate(callback);
  node('#task-command').value = 'financial';
  const fields = new Map();
  node('#task-form').elements = { namedItem(name) {
    if (!fields.has(name)) fields.set(name, new Element('input'));
    return fields.get(name);
  } };
  const sources = [];
  globalThis.EventSource = class {
    static CLOSED = 2;
    constructor(url) { this.url = url; this.readyState = 1; sources.push(this); }
    close() { this.closed = true; this.readyState = 2; }
  };
  const requests = [];
  const pending = new Map();
  const record = id => ({
    run_id: id, status, input_summary: { name: `公司${id}` },
    current_phase: 'report', result_summary: { available: status === 'succeeded' },
  });
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    requests.push({ method, url });
    let body;
    const waiting = pending.get(`${method} ${url}`);
    if (waiting) body = await waiting.promise;
    else if (url === '/api/runs') body = { runs: [record(first), record(first === 'A' ? 'B' : 'A')] };
    else if (url.includes('/ask')) body = { answer: '默认回答' };
    else if (url.includes('/search')) body = { status: 'ok', hits: [{ text: '公司B检索结果' }] };
    else {
      const id = url.split('/')[3].split('?')[0];
      body = record(id);
      if (url.includes('include_result=1')) body.result = { report_md: `报告${id}`, evidence: [], rows: [], ...result };
    }
    return { ok: true, status: 200, async text() { return JSON.stringify(body); } };
  };
  const uri = pathToFileURL(resolve('web/js/main.js'));
  await import(`${uri}?fixture=${fixtureNumber++}`);
  await settle();
  return {
    node, sources, requests, record,
    defer(method, url) {
      const result = deferred();
      pending.set(`${method} ${url}`, result);
      return result;
    },
    async select(id) {
      const button = node('#run-list').children.find(child => child.dataset.runId === id);
      assert.ok(button, `运行 ${id} 必须在列表中`);
      await node('#run-list').dispatch('click', { target: button });
      await settle();
    },
  };
}

test('切换运行后，较慢的旧详情响应不能覆盖当前结果', async () => {
  const f = await fixture({ first: 'B' });
  const old = f.defer('GET', '/api/runs/A');
  await f.select('A');
  await f.select('B');
  old.resolve(f.record('A'));
  await settle();
  assert.match(f.node('#status-region').textContent, /公司B/);
  assert.match(f.node('#results-view').textContent, /报告B/);
});

test('旧运行的加载错误不能污染新运行', async () => {
  const f = await fixture({ first: 'B' });
  const old = f.defer('GET', '/api/runs/A');
  await f.select('A');
  await f.select('B');
  old.reject(new Error('旧运行A的请求失败'));
  await settle();
  assert.equal(f.node('#error-region').hidden, true);
  assert.match(f.node('#status-region').textContent, /公司B/);
});

test('旧追问回复不能混入另一公司的追问记录或清空新输入', async () => {
  const f = await fixture();
  const reply = f.defer('POST', '/api/runs/A/ask');
  f.node('#ask-input').value = 'A的营业收入';
  const waiting = f.node('#ask-form').dispatch('submit');
  await settle();
  await f.select('B');
  f.node('#ask-input').value = 'B的新问题';
  reply.resolve({ answer: '仅属于公司A的答案' });
  await waiting;
  await settle();
  assert.doesNotMatch(f.node('#ask-content').textContent, /仅属于公司A/);
  assert.equal(f.node('#ask-input').value, 'B的新问题');
});

test('旧检索响应不能覆盖新运行的检索结果', async () => {
  const f = await fixture();
  const old = f.defer('POST', '/api/runs/A/search');
  f.node('#search-input').value = '营业收入';
  const waiting = f.node('#search-form').dispatch('submit');
  await settle();
  await f.select('B');
  await f.node('#search-form').dispatch('submit');
  old.resolve({ status: 'ok', hits: [{ text: '过期的公司A命中' }] });
  await waiting;
  await settle();
  assert.match(f.node('#ask-content').textContent, /公司B检索结果/);
  assert.doesNotMatch(f.node('#ask-content').textContent, /过期的公司A/);
});

test('取消旧运行的响应不能把用户切回旧运行', async () => {
  const f = await fixture({ status: 'running' });
  const old = f.defer('POST', '/api/runs/A/cancel');
  const waiting = f.node('#cancel-run').dispatch('click');
  await settle();
  await f.select('B');
  old.resolve({ run_id: 'A', status: 'cancelled' });
  await waiting;
  await settle();
  assert.match(f.node('#status-region').textContent, /公司B/);
});

test('关闭的 SSE 连接不会再向工作台发送消息', async () => {
  const f = await fixture({ status: 'running' });
  const old = f.sources.at(-1);
  await f.select('B');
  const current = f.sources.at(-1);
  old.onmessage({ data: JSON.stringify({ run_id: 'A', seq: 99, status: 'succeeded' }), lastEventId: '99' });
  await settle();
  assert.match(f.node('#status-region').textContent, /公司B/);
  assert.equal(Boolean(current.closed), false);
});

test('当前 SSE 若收到其他运行的终态，也不能切换结果', async () => {
  const f = await fixture({ status: 'running' });
  const source = f.sources.at(-1);
  source.onmessage({ data: JSON.stringify({ run_id: 'B', seq: 99, status: 'succeeded' }), lastEventId: '99' });
  await settle();
  assert.match(f.node('#status-region').textContent, /公司A/);
  assert.equal(Boolean(source.closed), false);
});

test('非结果更新保留用户已经展开的报告节点', async () => {
  const f = await fixture();
  const result = f.node('#results-view');
  const report = result.children.at(-1);
  f.node('#ask-input').value = '营业收入';
  await f.node('#ask-form').dispatch('submit');
  await settle();
  assert.equal(result.children.at(-1), report);
});

test('结果展示区分百分点、每股单位和上年末基期，保留受阻派生原因', async () => {
  const f = await fixture({ result: {
    rows: [{metric_name:'加权ROE',current:'17.89',previous:'17.63',normalized_unit:'%',
      change:{status:'ok',value:'0.26',unit:'个百分点'}},
    {metric_name:'每股收益',current:'36.18',previous:'33.19',normalized_unit:'元/股'},
    {metric_name:'总资产',current:'100',previous:'90',normalized_unit:'元',previous_period_end:'2024-12-31',
      comparison_label:'较上年末变化',change:{status:'ok',value:'11.111111111111111111111111',unit:'%'}}],
    qoq_blocked:[{company_code:'600519',metric:'revenue',period_start:'2025-04-01',period_end:'2025-06-30',
      attempted_value:'38788396531.06',reasons:['会计政策未声明'],sources:[{document_id:'half',page:5},{document_id:'q1',page:1}]}],
    checks:{mode:'local_supplemental',checks:[{claim_id:'C2',status:'确认错误',original_sentence:'待核查句',reason:'与原文不符',
      suggestion:'修改为原值',correction:{before:'1965.34亿元',after:'1708.99亿元'},evidence_ids:[]}]},
    explanation:{model_call_status:'not_used',steps:[{seq:1,event_type:'calculation',actor:'program',title:'本地计算',
      explanation:'按原文精度与披露9.10%核对一致',payload:{arguments:{metric:'revenue'}}}]},
  } });
  const text = f.node('#results-view').textContent;
  assert.match(text,/0.26个百分点/);
  assert.doesNotMatch(text,/0.26%/);
  assert.match(text,/36.18 元\/股/);
  assert.match(text,/较上年末变化/);
  assert.match(text,/未获可比性确认/);
  assert.match(text,/会计政策未声明/);
  assert.match(text,/1965.34亿元 → 1708.99亿元/);
  assert.match(text,/本次未调用大模型/);
  assert.match(f.node('#trace-view').textContent,/按原文精度与披露9.10%核对一致/);
  assert.match(f.node('#trace-view').textContent,/"metric": "revenue"/);
});

test('长轨迹按窗口展示，加载旧事件时不丢失数据或重建已有节点', async () => {
  const f = await fixture({status:'running'});
  const source = f.sources.at(-1);
  for (let seq = 1; seq <= 250; seq++) source.onmessage({data:JSON.stringify({run_id:'A',seq,status:'running',title:`事件${seq}`})});
  await settle();
  const root = f.node('#trace-view');
  let list = root.children.find(child=>child.tagName==='OL');
  assert.equal(list.children.length,100);
  const last = list.children.at(-1);
  await root.children.find(child=>child.tagName==='BUTTON').dispatch('click');
  assert.equal(list.children.length,200);
  assert.equal(list.children.at(-1),last);
  await root.children.find(child=>child.tagName==='BUTTON').dispatch('click');
  assert.equal(list.children.length,250);
  assert.equal(list.children.at(-1),last);
});
