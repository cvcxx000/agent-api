const $ = (id) => document.getElementById(id);
const NL = String.fromCharCode(10);
const LS = { token: 'agent.token', system: 'agent.system', session: 'agent.session', turns: 'agent.turns' };
let sessionId = localStorage.getItem(LS.session) || null;
let busy = false;
let ctrl = null;
let curBot = null;
let curRaw = '';
let subTimer = null;
let sheetMode = '';
const toolMap = new Map();

let providersCache = [];

function esc(s){
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function isDigit(ch){
  const c = String(ch).charCodeAt(0);
  return c >= 48 && c <= 57;
}
function inlineMd(s){
  const parts = String(s).split('`');
  let out = '';
  for (let i = 0; i < parts.length; i++){
    out += (i % 2 === 1) ? '<code>' + esc(parts[i]) + '</code>' : boldMd(parts[i]);
  }
  return out;
}
function boldMd(s){
  const parts = String(s).split('**');
  let out = '';
  for (let i = 0; i < parts.length; i++){
    out += (i % 2 === 1) ? '<b>' + esc(parts[i]) + '</b>' : esc(parts[i]);
  }
  return out;
}
function codeBlock(raw){
  let body = String(raw);
  const p = body.indexOf(NL);
  if (p >= 0 && body.slice(0, p).length <= 20 && body.slice(0, p).indexOf(' ') < 0){
    body = body.slice(p + 1);
  }
  while (body.slice(-1) === NL){ body = body.slice(0, -1); }
  return '<div class="code">' + esc(body) + '</div>';
}
function textBlocks(txt){
  const lines = String(txt).split(NL);
  let html = '';
  let para = [];
  let inList = false;
  function flushPara(){
    if (para.length){ html += '<p>' + inlineMd(para.join(' ')) + '</p>'; para = []; }
  }
  function closeList(){
    if (inList){ html += '</ul>'; inList = false; }
  }
  for (let i = 0; i < lines.length; i++){
    const t = lines[i].trim();
    if (!t){ flushPara(); closeList(); continue; }
    if (t.slice(0, 2) === '- ' || t.slice(0, 2) === '* '){
      flushPara();
      if (!inList){ html += '<ul>'; inList = true; }
      html += '<li>' + inlineMd(t.slice(2)) + '</li>';
      continue;
    }
    if (t.charAt(0) === '#'){
      let lvl = 0;
      while (t.charAt(lvl) === '#'){ lvl += 1; }
      if (lvl > 0 && lvl < 7){
        flushPara(); closeList();
        html += '<div class="h h' + lvl + '">' + inlineMd(t.slice(lvl).trim()) + '</div>';
        continue;
      }
    }
    const dot = t.indexOf('. ');
    if (dot > 0 && dot < 4 && isDigit(t.charAt(0))){
      flushPara(); closeList();
      html += '<p>' + inlineMd(t) + '</p>';
      continue;
    }
    para.push(t);
  }
  flushPara(); closeList();
  return html;
}
function md(text){
  const parts = String(text || '').split('```');
  let out = '';
  for (let i = 0; i < parts.length; i++){
    out += (i % 2 === 1) ? codeBlock(parts[i]) : textBlocks(parts[i]);
  }
  return out;
}

function wrapEl(){ return $('wrap'); }
function scrollDown(){ $('log').scrollTop = $('log').scrollHeight; }
function note(text, cls){
  const d = document.createElement('div');
  d.className = 'sysmsg ' + (cls || '');
  d.textContent = text;
  wrapEl().appendChild(d);
  scrollDown();
  return d;
}
function showSession(){
  $('sid').textContent = 'session: ' + (sessionId ? sessionId : '新建');
}
function addUser(text){
  const d = document.createElement('div');
  d.className = 'msg user';
  d.innerHTML = '<div class="who">你</div><div class="bubble">' + esc(text) + '</div>';
  wrapEl().appendChild(d);
  scrollDown();
  return d;
}
function newBot(raw){
  const d = document.createElement('div');
  d.className = 'msg bot';
  d.innerHTML = '<div class="who">Agent</div><div class="text"></div>';
  wrapEl().appendChild(d);
  const t = d.querySelector('.text');
  t.innerHTML = md(raw || '');
  scrollDown();
  return t;
}
function argsText(args){
  if (typeof args === 'string'){ return args; }
  if (args == null){ return '{}'; }
  try { return JSON.stringify(args, null, 2); } catch (e) { return String(args); }
}
function addTool(id, name, args){
  const d = document.createElement('details');
  d.className = 'tool';
  d.open = true;
  d.innerHTML = '<summary><span class="tag">工具</span><b class="nm">' + esc(name) +
    '</b><span class="st">运行中…</span></summary>' +
    '<pre class="arg">' + esc(argsText(args)) + '</pre><pre class="out">等待输出…</pre>';
  wrapEl().appendChild(d);
  scrollDown();
  toolMap.set(id || name, { el: d, st: d.querySelector('.st'), out: d.querySelector('.out'), t0: Date.now() });
  return d;
}
function fillTool(id, name, output, ok){
  const rec = toolMap.get(id || name);
  if (!rec){ return; }
  const secs = ((Date.now() - rec.t0) / 1000).toFixed(1);
  rec.st.textContent = (ok === false ? '失败 ' : '完成 ') + secs + 's';
  rec.st.className = 'st ' + (ok === false ? 'bad' : 'ok');
  let text = output;
  if (typeof text !== 'string'){
    try { text = JSON.stringify(text, null, 2); } catch (e) { text = String(text); }
  }
  rec.out.textContent = text || '(空输出)';
  if (ok === false){ rec.el.open = true; }
  scrollDown();
}

function headers(){
  const h = { 'Content-Type': 'application/json' };
  const t = localStorage.getItem(LS.token);
  if (t){ h['X-API-Token'] = t; }
  return h;
}
async function apiGet(path){
  const resp = await fetch(path, { headers: headers() });
  if (!resp.ok){ throw new Error('HTTP ' + resp.status + ' ' + (await resp.text()).slice(0, 160)); }
  return resp.json();
}
async function apiPost(path, body){
  const resp = await fetch(path, { method: 'POST', headers: headers(), body: JSON.stringify(body || {}) });
  const text = await resp.text();
  let data = null;
  try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
  if (!resp.ok){ throw new Error('HTTP ' + resp.status + ' ' + text.slice(0, 160)); }
  return data;
}

async function apiDelete(path){
  const resp = await fetch(path, { method: 'DELETE', headers: headers() });
  const text = await resp.text();
  let data = null;
  try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
  if (!resp.ok){ throw new Error('HTTP ' + resp.status + ' ' + text.slice(0, 160)); }
  return data;
}
async function refreshStatus(){
  try{
    const j = await fetch('/health').then(resp => resp.json());
    $('status').textContent = '在线 · ' + (j.mock_llm ? 'Mock 模式（未配 Key）' : '已连真实模型');
  }catch (e){
    $('status').textContent = '离线';
  }
}
async function refreshProviderSummary(){
  try{
    const j = await apiGet('/api/providers');
    providersCache = j.providers || [];
    const n = providersCache.length;
    const on = providersCache.filter(p => p.enabled).length;
    const cur = (j.current && j.current.model) ? j.current.model : '未配置';
    $('providerSub').textContent = n + ' 家 · ' + on + ' 家启用 · ' + cur;
    $('curModel').textContent = (j.current && j.current.base_url)
      ? (cur + ' @ ' + j.current.base_url) : '未配置';
  }catch (e){
    $('providerSub').textContent = '读取失败：' + e.message;
  }
}

function openDrawer(on){
  $('drawer').classList.toggle('on', on);
  $('mask').classList.toggle('on', on);
  if (on){ loadSessions(); refreshProviderSummary(); }
}
let sheetStack = [];
let sheetRender = null;
function openSheet(mode, title, sub, render){
  sheetMode = mode;
  sheetRender = render || null;
  $('sheetTitle').innerHTML = esc(title) + '<small>' + esc(sub || '') + '</small>';
  $('sheet').classList.add('on');
  if (mode === 'subagents'){ startSubPoll(); } else { stopSubPoll(); }
  if (render){ render(); }
}
function pushSheet(mode, title, sub, render){
  sheetStack.push({ mode: mode, title: title, sub: sub, render: render });
  openSheet(mode, title, sub, render);
}
function closeSheet(){
  $('sheet').classList.remove('on');
  stopSubPoll();
  sheetMode = '';
  sheetRender = null;
  sheetStack = [];
}
function sheetBack(){
  sheetStack.pop();
  const top = sheetStack[sheetStack.length - 1];
  if (!top){ closeSheet(); return; }
  openSheet(top.mode, top.title, top.sub, top.render);
}
function sheetWrap(){
  $('sheetWrap').innerHTML = '';
  return $('sheetWrap');
}
