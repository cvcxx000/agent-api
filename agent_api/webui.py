"""单文件 Web 聊天界面：GET / 与 /ui 返回同一个页面，直接调本服务的 /api/chat/stream。

页面自带：会话列表、模型切换、技能载入、工作流运行、子任务监控、文件浏览、Markdown 导出。
全部是标准库 + 原生 HTML/JS，不依赖任何前端框架和外链 CDN。
"""

from __future__ import annotations

import os

UI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#faf9f5">
<title>Agent</title>
<style>
:root{
  --bg:#faf9f5; --panel:#f2efe8; --card:#fffdfa; --ink:#23211d; --dim:#7a756c;
  --line:#e4dfd3; --accent:#c96442; --accent-soft:#f3e3db; --code:#fbfaf7;
  --ok:#4a7a58; --err:#a33b25; --warn:#a5760f;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%;margin:0}
body{
  background:var(--bg); color:var(--ink);
  font:15px/1.65 -apple-system,"PingFang SC","Noto Sans CJK SC",system-ui,sans-serif;
  display:flex; flex-direction:column; overflow:hidden;
}
header{
  display:flex; align-items:center; gap:8px; padding:10px 12px;
  border-bottom:1px solid var(--line); background:rgba(250,249,245,.92);
  backdrop-filter:blur(8px); position:sticky; top:0; z-index:5; flex:none;
}
header .icon{
  width:36px; height:34px; border:1px solid var(--line); background:#fff; color:var(--ink);
  border-radius:10px; font-size:16px; line-height:1; cursor:pointer; flex:none;
}
header .title{font-weight:600; letter-spacing:.02em; flex:1; min-width:0}
header .title small{display:block; font-weight:400; color:var(--dim); font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
header .pill{
  border:1px solid var(--line); background:#fff; color:var(--ink);
  border-radius:10px; padding:7px 10px; font-size:13px; cursor:pointer; flex:none;
}
header .pill:active{transform:scale(.97)}
header .badge{
  border:1px solid #d9bfae; background:var(--accent-soft); color:var(--accent);
  border-radius:10px; padding:6px 8px; font-size:11px; cursor:pointer; flex:none; display:none;
}
header .badge.on{display:block}

#mask{position:fixed; inset:0; background:rgba(35,33,29,.28); opacity:0; pointer-events:none; transition:opacity .18s; z-index:20}
#mask.on{opacity:1; pointer-events:auto}
#drawer{
  position:fixed; top:0; left:0; bottom:0; width:min(86vw,360px); z-index:21;
  background:var(--card); border-right:1px solid var(--line);
  transform:translateX(-102%); transition:transform .2s ease;
  display:flex; flex-direction:column; padding:14px 14px calc(14px + env(safe-area-inset-bottom));
  overflow-y:auto;
}
#drawer.on{transform:none}

#sheet{
  position:fixed; inset:0; z-index:22; background:var(--bg);
  transform:translateY(102%); transition:transform .22s ease;
  display:flex; flex-direction:column;
}
#sheet.on{transform:none}
#sheet header{position:static}
#sheetBody{flex:1; overflow-y:auto; padding:14px 14px calc(20px + env(safe-area-inset-bottom))}
#sheetBody .wrap{max-width:760px; margin:0 auto}

.dr-head{font-size:12px; color:var(--dim); letter-spacing:.08em; margin:4px 0 8px; display:flex; justify-content:space-between}
.dr-sep{height:1px; background:var(--line); margin:14px 0}
.dr-label{font-size:11px; color:var(--dim); margin:10px 0 4px}
#sessList{display:flex; flex-direction:column; gap:6px}
.sess{
  border:1px solid var(--line); border-radius:10px; padding:8px 10px; background:#fff;
  display:flex; align-items:center; gap:8px; cursor:pointer;
}
.sess.on{border-color:#d9bfae; background:var(--accent-soft)}
.sess .body{flex:1; min-width:0}
.sess .t{font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.sess .m{font-size:11px; color:var(--dim); overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.sess .del{border:0; background:transparent; color:var(--dim); font-size:15px; cursor:pointer; padding:2px 4px; flex:none}

.grid{display:grid; grid-template-columns:repeat(2,1fr); gap:8px}
.grid button{
  border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:10px;
  padding:10px 8px; font-size:13px; cursor:pointer; text-align:left;
}
.grid button b{display:block; font-size:13px}
.grid button span{font-size:11px; color:var(--dim)}

#drawer input,#drawer textarea,#drawer select,.panel input,.panel select{
  width:100%; border:1px solid var(--line); border-radius:10px; background:#fff;
  padding:8px 10px; font:13px/1.5 inherit; color:var(--ink);
}
#drawer textarea{resize:vertical; min-height:64px}
#drawer .act,.panel .act{
  width:100%; border:1px solid var(--line); background:#fff; color:var(--ink);
  border-radius:10px; padding:9px 10px; font-size:13px; cursor:pointer; margin-top:8px;
}
#drawer .act.primary,.panel .act.primary{background:var(--accent); border-color:var(--accent); color:#fff}
.row2{display:flex; gap:8px}
.row2 .act{margin-top:8px}
.dr-tip{font-size:11px; color:var(--dim); line-height:1.5; margin-top:10px}

#log{flex:1; overflow-y:auto; padding:18px 14px 8px; scroll-behavior:smooth}
.wrap{max-width:760px; margin:0 auto}
.msg{margin:0 0 16px; display:flex; flex-direction:column; gap:6px}
.msg .who{font-size:11px; color:var(--dim); letter-spacing:.08em; text-transform:uppercase}
.user .bubble{
  align-self:flex-end; max-width:85%; background:var(--accent-soft);
  border-radius:14px 14px 4px 14px; padding:10px 13px; white-space:pre-wrap;
}
.bot .text{white-space:normal; word-break:break-word}
.bot .text p{margin:0 0 8px}
.bot .text .h{font-weight:600; margin:10px 0 6px}
.bot .text .h1{font-size:19px}
.bot .text .h2{font-size:17px}
.bot .text .h3{font-size:15px}
.bot .text ul{margin:0 0 8px; padding-left:20px}
.bot .text li{margin:2px 0}
.bot .text code{background:var(--panel); border-radius:5px; padding:1px 5px; font:13px ui-monospace,SFMono-Regular,Menlo,monospace}
.tool{
  border:1px solid var(--line); border-radius:12px; background:#fff; margin:0 0 10px; overflow:hidden;
}
.tool summary{
  cursor:pointer; padding:8px 12px; font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
  display:flex; gap:8px; align-items:center; list-style:none;
}
.tool summary::-webkit-details-marker{display:none}
.tool .tag{background:var(--panel); border-radius:6px; padding:1px 6px; color:var(--dim); flex:none}
.tool .nm{font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.tool .st{margin-left:auto; color:var(--dim); flex:none}
.tool .st.ok{color:var(--ok)}
.tool .st.bad{color:var(--err)}
.tool pre{
  margin:0; padding:10px 12px; border-top:1px solid var(--line); background:var(--code);
  font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
  white-space:pre-wrap; word-break:break-word; color:#3a3730; max-height:300px; overflow:auto;
}
.tool .arg{color:var(--dim)}
.code{
  background:var(--code); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin:8px 0; overflow:auto; max-height:340px;
  font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre;
}
.sysmsg{color:var(--dim); font-size:12px; text-align:center; margin:10px 0}
.err{color:var(--err); font-size:13px; margin:4px 0}

.panel{
  border:1px solid var(--line); border-radius:12px; background:#fff; padding:10px 12px; margin:0 0 10px;
}
.panel h3{margin:0 0 4px; font-size:14px}
.panel .meta{font-size:11px; color:var(--dim); margin-bottom:6px; word-break:break-all}
.panel .desc{font-size:12.5px; color:#57504a; margin-bottom:8px; white-space:pre-wrap}
.panel .acts{display:flex; gap:8px; flex-wrap:wrap}
.panel .mini{
  border:1px solid var(--line); background:#fff; border-radius:8px; padding:5px 10px;
  font-size:12px; cursor:pointer; color:var(--ink);
}
.panel .mini.warn{border-color:#e8cfa8; color:var(--warn)}
.panel .mini.primary{background:var(--accent); border-color:var(--accent); color:#fff}
.panel .out{
  margin-top:8px; border-top:1px dashed var(--line); padding-top:8px;
  font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
  white-space:pre-wrap; word-break:break-word; max-height:280px; overflow:auto; color:#57504a;
}
.tagline{display:inline-block; font-size:11px; padding:1px 6px; border-radius:5px; background:var(--panel); color:var(--dim); margin-left:6px}
.tagline.run{background:#efe6d4; color:var(--warn)}
.tagline.ok{background:#e3efe6; color:var(--ok)}
.tagline.bad{background:#f5e0dc; color:var(--err)}

footer{
  border-top:1px solid var(--line); background:rgba(250,249,245,.94);
  padding:10px 12px calc(12px + env(safe-area-inset-bottom)); flex:none;
}
.composer{max-width:760px; margin:0 auto; display:flex; gap:8px; align-items:flex-end}
#input{
  flex:1; resize:none; min-height:44px; max-height:38vh;
  padding:11px 12px; font:15px/1.5 inherit; color:var(--ink);
  border:1px solid var(--line); border-radius:14px; background:#fff; outline:none;
}
#input:focus{border-color:#cfc6b4}
#send{
  width:52px; height:44px; border:0; border-radius:12px; cursor:pointer;
  background:var(--accent); color:#fff; font-size:16px; flex:none;
}
#send.stop{background:#7a756c}
.hint{max-width:760px; margin:6px auto 0; font-size:11px; color:var(--dim); display:flex; gap:10px}
#sid{overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
@media(max-width:520px){
  #log{padding:14px 12px 6px}
  .user .bubble{max-width:92%}
}
</style>
</head>
<body>
<header>
  <button class="icon" id="btnMenu" aria-label="菜单">☰</button>
  <div class="title">Agent<small id="status">连接中…</small></div>
  <button class="badge" id="badge">子任务 0/3</button>
  <button class="pill" id="btnNew">新会话</button>
</header>

<div id="mask"></div>

<aside id="drawer">
  <div class="dr-head"><span>会话列表</span><span id="drCount"></span></div>
  <div id="sessList"></div>
  <div class="dr-sep"></div>

  <div class="dr-head"><span>功能</span></div>
  <div class="grid">
    <button id="goSkills"><b>技能</b><span>载入 SKILL.md</span></button>
    <button id="goWorkflows"><b>工作流</b><span>跑 JSON 流程</span></button>
    <button id="goSubagents"><b>子任务</b><span>最多 3 个并行</span></button>
    <button id="goFiles"><b>文件</b><span>浏览与预览</span></button>
  </div>

  <div class="dr-sep"></div>
  <div class="dr-head"><span>模型</span></div>
  <div class="dr-label">当前：<span id="curModel">读取中…</span></div>
  <select id="selPreset">
    <option value="">选择预设…</option>
  </select>
  <div class="dr-label">BASE URL</div>
  <input id="inpBase" placeholder="https://api.openai.com/v1" autocomplete="off">
  <div class="dr-label">模型 ID</div>
  <input id="inpModel" placeholder="gpt-4o-mini" autocomplete="off">
  <div class="dr-label">API Key（留空 = 不改动）</div>
  <input id="inpKey" type="password" placeholder="sk-..." autocomplete="off">
  <div class="dr-label">每分钟请求上限（留空 = 不改动，0 = 不限）</div>
  <input id="inpRate" placeholder="例如 7" autocomplete="off">
  <div class="dr-label"><input type="checkbox" id="chkMock" style="width:auto"> 强制 Mock（不调真模型）</div>
  <div class="row2">
    <button class="act primary" id="btnApplyModel">应用模型</button>
    <button class="act" id="btnTestModel">测一句</button>
  </div>

  <div class="dr-sep"></div>
  <div class="dr-head"><span>对话设置</span></div>
  <div class="dr-label">API_TOKEN（服务端没设就留空）</div>
  <input id="inpToken" autocomplete="off" placeholder="X-API-Token">
  <div class="dr-label">系统提示词（新建会话时生效）</div>
  <textarea id="inpSystem" placeholder="例如：只用中文回答，结论先行"></textarea>
  <div class="dr-label">单轮最大工具轮数</div>
  <input id="inpTurns" placeholder="留空 = 服务端默认">
  <button class="act primary" id="btnSave">保存设置</button>

  <div class="dr-sep"></div>
  <button class="act" id="btnExport">导出当前会话（Markdown）</button>
  <button class="act" id="btnClearView">清空当前画面</button>
  <div class="dr-tip">会话存在服务端 data/sessions；浏览器只记 session id、token 和设置。模型切换会重建客户端，不写入 .env，重启后回到 .env 的配置。</div>
</aside>

<section id="sheet">
  <header>
    <button class="icon" id="sheetBack" aria-label="返回">←</button>
    <div class="title" id="sheetTitle">面板<small id="sheetSub"></small></div>
    <button class="pill" id="sheetRefresh">刷新</button>
  </header>
  <main id="sheetBody"><div class="wrap" id="sheetWrap"></div></main>
</section>

<main id="log"><div class="wrap" id="wrap"></div></main>

<footer>
  <div class="composer">
    <textarea id="input" rows="1" placeholder="让 Agent 干活…（Enter 发送，Shift+Enter 换行）"></textarea>
    <button id="send" title="发送">↑</button>
  </div>
  <div class="hint"><span id="sid">session: -</span><span id="tip">Enter 发送</span></div>
</footer>

<script>
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

const PRESETS = [
  { label: 'OpenAI · gpt-4o-mini', base: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  { label: 'DeepSeek · deepseek-chat', base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { label: '商汤日日新 · Token Plan', base: 'https://token.sensenova.cn/v1', model: 'sensenova-u1.5-lite' },
  { label: '商汤大装置 · SenseChat-5', base: 'https://api.sensenova.cn/compatible-mode/v1', model: 'SenseChat-5' },
  { label: '通义千问 · qwen-plus', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { label: '本地 Ollama · qwen2.5', base: 'http://127.0.0.1:11434/v1', model: 'qwen2.5' },
];

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
function addTool(id, name, args){
  const d = document.createElement('details');
  d.className = 'tool';
  d.open = true;
  d.innerHTML = '<summary><span class="tag">工具</span><b class="nm">' + esc(name) +
    '</b><span class="st">运行中…</span></summary>' +
    '<pre class="arg">' + esc(args || '{}') + '</pre><pre class="out">等待输出…</pre>';
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

async function refreshStatus(){
  try{
    const j = await fetch('/health').then(resp => resp.json());
    $('status').textContent = '在线 · ' + (j.mock_llm ? 'Mock 模式（未配 Key）' : '已连真实模型');
  }catch (e){
    $('status').textContent = '离线';
  }
}
async function refreshConfig(){
  try{
    const j = await apiGet('/api/config');
    $('curModel').textContent = (j.mock_llm ? 'Mock' : j.model) + ' @ ' + j.base_url;
    if (!$('inpBase').value){ $('inpBase').value = j.base_url; }
    if (!$('inpModel').value){ $('inpModel').value = j.model; }
  }catch (e){
    $('curModel').textContent = '读取失败：' + e.message;
  }
}

function openDrawer(on){
  $('drawer').classList.toggle('on', on);
  $('mask').classList.toggle('on', on);
  if (on){ loadSessions(); refreshConfig(); }
}
function openSheet(mode, title, sub){
  sheetMode = mode;
  $('sheetTitle').innerHTML = esc(title) + '<small>' + esc(sub || '') + '</small>';
  $('sheet').classList.add('on');
  if (mode === 'subagents'){ startSubPoll(); }
}
function closeSheet(){
  $('sheet').classList.remove('on');
  stopSubPoll();
  sheetMode = '';
}
function sheetWrap(){
  $('sheetWrap').innerHTML = '';
  return $('sheetWrap');
}

async function loadSessions(){
  try{
    const j = await apiGet('/api/sessions');
    const list = (j.sessions || []).filter(s => s.kind === 'main');
    const box = $('sessList');
    box.innerHTML = '';
    $('drCount').textContent = list.length + ' 个';
    if (!list.length){
      const e = document.createElement('div');
      e.className = 'dr-tip';
      e.textContent = '还没有会话。发第一句话就会自动建一个。';
      box.appendChild(e);
      return;
    }
    list.forEach(s => {
      const row = document.createElement('div');
      row.className = 'sess' + (s.id === sessionId ? ' on' : '');
      const body = document.createElement('div');
      body.className = 'body';
      const t = document.createElement('div');
      t.className = 't';
      t.textContent = s.title || '(未命名会话)';
      const m = document.createElement('div');
      m.className = 'm';
      m.textContent = s.id + ' · ' + (s.message_count || 0) + ' 条 · ' +
        String(s.updated_at || '').replace('T', ' ').slice(0, 16);
      body.appendChild(t);
      body.appendChild(m);
      const del = document.createElement('button');
      del.className = 'del';
      del.textContent = '✕';
      del.title = '删除';
      del.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        if (!confirm('删除这个会话？不可恢复。')){ return; }
        try{
          const resp = await fetch('/api/sessions/' + encodeURIComponent(s.id), { method: 'DELETE', headers: headers() });
          if (!resp.ok){ throw new Error('HTTP ' + resp.status); }
          if (s.id === sessionId){ startNew(); }
          loadSessions();
        }catch (e){ note('删除失败：' + e.message, 'err'); }
      });
      row.appendChild(body);
      row.appendChild(del);
      row.addEventListener('click', () => switchSession(s.id));
      box.appendChild(row);
    });
  }catch (e){
    note('会话列表加载失败：' + e.message, 'err');
  }
}

async function switchSession(id){
  try{
    const j = await apiGet('/api/sessions/' + encodeURIComponent(id) + '/messages?limit=400');
    sessionId = id;
    localStorage.setItem(LS.session, id);
    wrapEl().innerHTML = '';
    toolMap.clear();
    curBot = null;
    curRaw = '';
    (j.messages || []).forEach(m => {
      if (m.role === 'user'){
        addUser(String(m.content || ''));
      } else if (m.role === 'assistant' && m.content){
        curBot = newBot(String(m.content));
        curRaw = String(m.content);
      }
    });
    showSession();
    openDrawer(false);
    scrollDown();
  }catch (e){
    note('切换会话失败：' + e.message, 'err');
  }
}

function startNew(){
  sessionId = null;
  localStorage.removeItem(LS.session);
  wrapEl().innerHTML = '';
  toolMap.clear();
  curBot = null;
  curRaw = '';
  showSession();
  note('已开新会话，下一句会建一个新 session。');
}

async function ensureSession(){
  if (sessionId){ return sessionId; }
  const j = await apiPost('/api/sessions', { title: '面板操作' });
  sessionId = j.id;
  localStorage.setItem(LS.session, sessionId);
  showSession();
  return sessionId;
}

async function exportMd(){
  if (!sessionId){
    note('还没有会话可导出，先聊一句。', 'err');
    return;
  }
  try{
    const j = await apiGet('/api/sessions/' + encodeURIComponent(sessionId) + '/messages?limit=800');
    let out = '# Agent 会话 ' + sessionId + NL + NL;
    (j.messages || []).forEach(m => {
      if (m.role === 'user'){
        out += '## 你' + NL + NL + String(m.content || '') + NL + NL;
      } else if (m.role === 'assistant' && m.content){
        out += '## Agent' + NL + NL + String(m.content) + NL + NL;
      } else if (m.role === 'tool'){
        out += '> 工具 ' + String(m.name || '') + ' 输出：' + NL + NL +
          '```' + NL + String(m.content || '').slice(0, 4000) + NL + '```' + NL + NL;
      }
    });
    const blob = new Blob([out], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'agent-' + sessionId + '.md';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    note('已导出 Markdown 文件。');
  }catch (e){
    note('导出失败：' + e.message, 'err');
  }
}

function card(title, meta, desc, actions){
  const box = document.createElement('div');
  box.className = 'panel';
  const h = document.createElement('h3');
  h.textContent = title;
  box.appendChild(h);
  if (meta){
    const m = document.createElement('div');
    m.className = 'meta';
    m.textContent = meta;
    box.appendChild(m);
  }
  if (desc){
    const d = document.createElement('div');
    d.className = 'desc';
    d.textContent = desc;
    box.appendChild(d);
  }
  const acts = document.createElement('div');
  acts.className = 'acts';
  (actions || []).forEach(a => {
    const b = document.createElement('button');
    b.className = 'mini ' + (a.cls || '');
    b.textContent = a.label;
    b.addEventListener('click', a.onClick);
    acts.appendChild(b);
    a.node = b;
  });
  box.appendChild(acts);
  return box;
}
function outputBox(box, text){
  const pre = document.createElement('div');
  pre.className = 'out';
  pre.textContent = text;
  box.appendChild(pre);
  return pre;
}

async function showSkills(){
  const w = sheetWrap();
  try{
    const j = await apiGet('/api/skills?refresh=1');
    const list = j.skills || [];
    if (!list.length){
      w.innerHTML = '<div class="dr-tip">没有扫描到 Skill。把 SKILL.md 放进 skills/ 或 workspace/.skills/ 再刷新。</div>';
      return;
    }
    list.forEach(s => {
      const box = card(s.name, s.directory, s.description, [{
        label: '载入到当前会话',
        cls: 'primary',
        onClick: async () => {
          try{
            const sid = await ensureSession();
            const r = await apiPost('/api/sessions/' + encodeURIComponent(sid) + '/skills/' + encodeURIComponent(s.name), {});
            outputBox(box, r.already_loaded ? '这个会话已经载入过了。' : '已载入，作用在后续每一轮（' + (r.chars || 0) + ' 字）。');
          }catch (e){ outputBox(box, '载入失败：' + e.message); }
        }
      }, {
        label: '看全文',
        onClick: async () => {
          try{
            const r = await apiGet('/api/skills/' + encodeURIComponent(s.name));
            outputBox(box, r.content || '');
          }catch (e){ outputBox(box, '读取失败：' + e.message); }
        }
      }]);
      w.appendChild(box);
    });
  }catch (e){
    w.innerHTML = '<div class="err">加载技能失败：' + esc(e.message) + '</div>';
  }
}

async function showWorkflows(){
  const w = sheetWrap();
  try{
    const j = await apiGet('/api/workflows');
    const list = j.workflows || [];
    if (!list.length){
      w.innerHTML = '<div class="dr-tip">workflows/ 目录里没有 .json 工作流。</div>';
      return;
    }
    list.forEach(def => {
      const inputs = def.inputs || {};
      const box = card(def.name, (def.steps || []).length + ' 步 · ' + (def.path || ''),
        (def.description || ''),
        [{ label: '运行', cls: 'primary', onClick: null }]);
      const fields = {};
      const keys = Object.keys(inputs);
      keys.forEach(k => {
        const spec = inputs[k] || {};
        const lab = document.createElement('div');
        lab.className = 'meta';
        lab.textContent = k + (spec && spec.required ? '（必填）' : (spec && spec.default !== undefined ? '（默认 ' + spec.default + '）' : ''));
        box.appendChild(lab);
        const inp = document.createElement('input');
        inp.placeholder = '输入 ' + k;
        if (spec && spec.default !== undefined){ inp.value = String(spec.default); }
        box.appendChild(inp);
        fields[k] = inp;
      });
      const btn = box.querySelector('.mini.primary');
      btn.addEventListener('click', async () => {
        const payload = {};
        keys.forEach(k => { if (fields[k].value){ payload[k] = fields[k].value; } });
        btn.textContent = '运行中…';
        btn.disabled = true;
        try{
          const r = await apiPost('/api/workflows/' + encodeURIComponent(def.name) + '/run',
            { inputs: payload, session_id: sessionId || undefined });
          outputBox(box, JSON.stringify(r, null, 2));
          btn.textContent = '运行';
        }catch (e){
          outputBox(box, '运行失败：' + e.message);
          btn.textContent = '运行';
        }finally{
          btn.disabled = false;
          refreshSubBadge();
        }
      });
      w.appendChild(box);
    });
  }catch (e){
    w.innerHTML = '<div class="err">加载工作流失败：' + esc(e.message) + '</div>';
  }
}

async function refreshSubBadge(){
  try{
    const j = await apiGet('/api/subagents');
    const n = j.active || 0;
    const max = j.max_concurrent || 3;
    const b = $('badge');
    b.textContent = '子任务 ' + n + '/' + max;
    b.classList.toggle('on', n > 0);
  }catch (e){ }
}

function startSubPoll(){
  stopSubPoll();
  showSubagents();
  subTimer = setInterval(() => { if (sheetMode === 'subagents'){ showSubagents(); } }, 2000);
}
function stopSubPoll(){
  if (subTimer){ clearInterval(subTimer); subTimer = null; }
}

async function showSubagents(){
  const w = sheetWrap();
  try{
    const j = await apiGet('/api/subagents');
    const list = j.subagents || [];
    const head = document.createElement('div');
    head.className = 'dr-tip';
    head.textContent = '并发 ' + (j.active || 0) + '/' + (j.max_concurrent || 3) +
      '，最大层数 ' + (j.max_depth || 1) + '。派第 4 个会被 409 拒绝。';
    w.appendChild(head);
    if (!list.length){
      const e = document.createElement('div');
      e.className = 'dr-tip';
      e.textContent = '还没有子任务。让 Agent 用 spawn_agent 派活，或者在这里直接建。';
      w.appendChild(e);
    }
    list.slice().reverse().forEach(s => {
      const status = s.status || 'running';
      const cls = status === 'done' ? 'ok' : (status === 'running' ? 'run' : 'bad');
      const title = (s.title || '(未命名)') + ' · ' + status;
      const box = card(title,
        '深度 ' + s.depth + ' · 创建 ' + String(s.created_at || '').slice(11, 19) +
        (s.finished_at ? ' · 结束 ' + String(s.finished_at).slice(11, 19) : ''),
        s.task || '',
        [{
          label: '看结果',
          onClick: async () => {
            try{
              const r = await apiGet('/api/subagents/' + encodeURIComponent(s.id));
              outputBox(box, (r.result || '(还没有结果)') + (r.error ? NL + 'ERROR: ' + r.error : ''));
            }catch (e){ outputBox(box, '读取失败：' + e.message); }
          }
        }, {
          label: '取消',
          cls: status === 'running' ? 'warn' : '',
          onClick: async () => {
            try{
              await apiPost('/api/subagents/' + encodeURIComponent(s.id) + '/cancel', {});
              showSubagents();
            }catch (e){ outputBox(box, '取消失败：' + e.message); }
          }
        }]);
      const tag = document.createElement('span');
      tag.className = 'tagline ' + cls;
      tag.textContent = status;
      box.querySelector('h3').appendChild(tag);
      w.appendChild(box);
    });
    const spawnBtn = document.createElement('button');
    spawnBtn.className = 'act primary';
    spawnBtn.textContent = '新建子任务';
    spawnBtn.addEventListener('click', async () => {
      const task = prompt('子任务要做什么？');
      if (!task){ return; }
      try{
        const r = await apiPost('/api/subagents', { task: task, parent_session_id: sessionId || undefined });
        note('已派发子任务 ' + r.id + '（' + (r.concurrency || '') + '）');
        showSubagents();
        refreshSubBadge();
      }catch (e){ alert('派发失败：' + e.message); }
    });
    w.appendChild(spawnBtn);
  }catch (e){
    w.innerHTML = '<div class="err">加载子任务失败：' + esc(e.message) + '</div>';
  }
}

let filePath = '.';
async function showFiles(){
  const w = sheetWrap();
  const row = document.createElement('div');
  row.className = 'row2';
  const inp = document.createElement('input');
  inp.value = filePath;
  inp.placeholder = '相对 workspace 的路径，例如 . 或 outputs';
  row.appendChild(inp);
  const go = document.createElement('button');
  go.className = 'act';
  go.style.marginTop = '0';
  go.textContent = '打开';
  row.appendChild(go);
  w.appendChild(row);
  const out = document.createElement('div');
  out.className = 'code';
  out.textContent = '读取中…';
  w.appendChild(out);

  async function loadDir(p){
    filePath = p;
    inp.value = p;
    out.textContent = '读取中…';
    try{
      const r = await apiGet('/api/dir?path=' + encodeURIComponent(p) + '&recursive=0');
      out.textContent = r.result || '(空目录)';
    }catch (e){
      out.textContent = '读取失败：' + e.message;
    }
  }
  go.addEventListener('click', () => loadDir(inp.value.trim() || '.'));
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter'){ loadDir(inp.value.trim() || '.'); } });
  loadDir(filePath);

  const openRow = document.createElement('div');
  openRow.className = 'row2';
  const fileInp = document.createElement('input');
  fileInp.placeholder = '要预览的文件路径，例如 outputs/report.md';
  openRow.appendChild(fileInp);
  const openBtn = document.createElement('button');
  openBtn.className = 'act';
  openBtn.style.marginTop = '0';
  openBtn.textContent = '预览';
  openRow.appendChild(openBtn);
  w.appendChild(openRow);
  const preview = document.createElement('div');
  preview.className = 'code';
  preview.textContent = '（在下面看文件内容）';
  openBtn.addEventListener('click', async () => {
    const p = fileInp.value.trim();
    if (!p){ return; }
    preview.textContent = '读取中…';
    try{
      const r = await apiGet('/api/files?path=' + encodeURIComponent(p) + '&limit=300');
      preview.textContent = r.result || '(空文件)';
    }catch (e){
      preview.textContent = '读取失败：' + e.message;
    }
  });
  w.appendChild(preview);
}

async function applyModel(){
  const body = {};
  const base = $('inpBase').value.trim();
  const model = $('inpModel').value.trim();
  const key = $('inpKey').value.trim();
  const rate = $('inpRate').value.trim();
  if (base){ body.base_url = base; }
  if (model){ body.model = model; }
  if (key){ body.api_key = key; }
  if (rate !== ''){ body.rate_limit = rate; }
  body.mock = $('chkMock').checked;
  try{
    const r = await apiPost('/api/ui/llm', body);
    $('curModel').textContent = (r.mock_llm ? 'Mock' : r.model) + ' @ ' + r.base_url;
    $('inpKey').value = '';
    note('模型已切换：' + (r.mock_llm ? 'Mock 模式' : r.model + ' @ ' + r.base_url));
    refreshStatus();
  }catch (e){
    note('切换失败：' + e.message, 'err');
  }
}
async function testModel(){
  try{
    const r = await apiPost('/api/chat', { message: '只回复两个字：收到' });
    const text = (r.content || '') + (r.error ? ' / ERROR ' + r.error : '');
    note('模型测试：' + (text || '(空)'));
  }catch (e){
    note('模型测试失败：' + e.message, 'err');
  }
}

async function send(){
  if (busy){ return; }
  const raw = $('input').value.trim();
  if (!raw){ return; }
  $('input').value = '';
  $('input').style.height = 'auto';
  busy = true;
  $('send').textContent = '■';
  $('send').classList.add('stop');
  addUser(raw);

  const body = { message: raw };
  if (sessionId){ body.session_id = sessionId; }
  const sys = localStorage.getItem(LS.system);
  if (sys && !sessionId){ body.system_prompt = sys; }
  const turns = localStorage.getItem(LS.turns);
  if (turns && Number(turns) > 0){ body.max_turns = Number(turns); }

  curBot = null;
  curRaw = '';
  ctrl = new AbortController();

  try{
    const resp = await fetch('/api/chat/stream', {
      method: 'POST', headers: headers(), body: JSON.stringify(body), signal: ctrl.signal
    });
    if (!resp.ok || !resp.body){
      throw new Error('HTTP ' + resp.status + ' ' + (await resp.text()).slice(0, 200));
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true){
      const r = await reader.read();
      if (r.done){ break; }
      buf += dec.decode(r.value, { stream: true });
      let idx = buf.indexOf(NL + NL);
      while (idx >= 0){
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = chunk.split(NL).filter(l => l.slice(0, 5) === 'data:').pop();
        if (line){
          try { handleEvent(JSON.parse(line.slice(5).trim())); } catch (e) { }
        }
        idx = buf.indexOf(NL + NL);
      }
    }
  }catch (e){
    if (e && e.name === 'AbortError'){ note('已停止本轮。'); }
    else { note('出错：' + (e && e.message ? e.message : e), 'err'); }
  }finally{
    busy = false;
    ctrl = null;
    $('send').textContent = '↑';
    $('send').classList.remove('stop');
    showSession();
    refreshSubBadge();
  }
}

function handleEvent(ev){
  const type = ev.type;
  if (type === 'assistant'){
    if (!ev.content){ return; }
    if (!curBot){ curBot = newBot(''); curRaw = ''; }
    curRaw += (curRaw ? NL + NL : '') + String(ev.content);
    curBot.innerHTML = md(curRaw);
    scrollDown();
  } else if (type === 'tool_call'){
    curBot = null;
    curRaw = '';
    addTool(ev.id, ev.name, ev.arguments);
    if (ev.name === 'spawn_agent' || ev.name === 'run_workflow'){ refreshSubBadge(); }
  } else if (type === 'tool_result'){
    fillTool(ev.id, ev.name, ev.output, ev.ok);
    if (ev.name === 'spawn_agent' || ev.name === 'wait_agent'){ refreshSubBadge(); }
  } else if (type === 'warning'){
    note(ev.message || '');
  } else if (type === 'error'){
    note('错误：' + (ev.message || ''), 'err');
  } else if (type === 'end'){
    if (ev.content && !curBot){
      curBot = newBot(String(ev.content));
      curRaw = String(ev.content);
    }
  } else if (type === 'final'){
    if (ev.session_id){
      sessionId = ev.session_id;
      localStorage.setItem(LS.session, sessionId);
    }
    note('完成 · ' + (ev.turns || 0) + ' 轮');
    showSession();
  }
}

$('send').addEventListener('click', () => {
  if (busy){ if (ctrl){ ctrl.abort(); } return; }
  send();
});
$('input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing){
    e.preventDefault();
    if (!busy){ send(); }
  }
});
$('input').addEventListener('input', () => {
  const el = $('input');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, window.innerHeight * 0.38) + 'px';
});
$('btnMenu').addEventListener('click', () => openDrawer(true));
$('mask').addEventListener('click', () => openDrawer(false));
$('btnNew').addEventListener('click', () => { startNew(); openDrawer(false); });
$('badge').addEventListener('click', () => { openDrawer(false); openSheet('subagents', '子任务', '最多 3 个并行'); });
$('goSkills').addEventListener('click', () => { openDrawer(false); openSheet('skills', '技能', 'SKILL.md 载入后长期生效'); showSkills(); });
$('goWorkflows').addEventListener('click', () => { openDrawer(false); openSheet('workflows', '工作流', 'JSON 步骤'); showWorkflows(); });
$('goSubagents').addEventListener('click', () => { openDrawer(false); openSheet('subagents', '子任务', '最多 3 个并行'); });
$('goFiles').addEventListener('click', () => { openDrawer(false); openSheet('files', '文件', '沙箱 workspace'); showFiles(); });
$('sheetBack').addEventListener('click', closeSheet);
$('sheetRefresh').addEventListener('click', () => {
  if (sheetMode === 'skills'){ showSkills(); }
  else if (sheetMode === 'workflows'){ showWorkflows(); }
  else if (sheetMode === 'subagents'){ showSubagents(); }
  else if (sheetMode === 'files'){ showFiles(); }
});
$('btnClearView').addEventListener('click', () => {
  wrapEl().innerHTML = '';
  toolMap.clear();
  curBot = null;
  curRaw = '';
  note('画面已清空，服务端会话还在（可用导出或列表切回）。');
});
$('btnExport').addEventListener('click', exportMd);
$('btnSave').addEventListener('click', () => {
  localStorage.setItem(LS.token, $('inpToken').value.trim());
  localStorage.setItem(LS.system, $('inpSystem').value.trim());
  localStorage.setItem(LS.turns, $('inpTurns').value.trim());
  note('设置已保存到本机浏览器。');
  refreshStatus();
  loadSessions();
});
$('btnApplyModel').addEventListener('click', applyModel);
$('btnTestModel').addEventListener('click', testModel);
$('selPreset').addEventListener('change', () => {
  const idx = Number($('selPreset').value);
  if (!idx){ return; }
  const p = PRESETS[idx - 1];
  if (!p){ return; }
  $('inpBase').value = p.base;
  $('inpModel').value = p.model;
});

PRESETS.forEach((p, i) => {
  const o = document.createElement('option');
  o.value = String(i + 1);
  o.textContent = p.label;
  $('selPreset').appendChild(o);
});
$('inpToken').value = localStorage.getItem(LS.token) || '';
$('inpSystem').value = localStorage.getItem(LS.system) || '';
$('inpTurns').value = localStorage.getItem(LS.turns) || '';
showSession();
refreshStatus();
refreshConfig();
refreshSubBadge();
setInterval(refreshSubBadge, 8000);
note('准备就绪。☰ 里可以切模型、载技能、跑工作流、看子任务、浏览文件。');
$('input').focus();
</script>
</body>
</html>
"""


def mount(router) -> None:
    """把 UI 路由挂到服务上（页面自己带 X-API-Token 调接口）。"""
    from .llm import build_llm
    from .server import Response

    def ui(req, params):
        return Response(200, UI_HTML, content_type="text/html; charset=utf-8")

    def ui_llm(req, params):
        """运行时切换模型：只改内存里的 settings + 重建 llm，不写 .env。"""
        from dataclasses import replace

        runtime = req.runtime
        st = runtime.settings
        body = req.json()

        changes: dict = {}
        base_url = body.get("base_url")
        if isinstance(base_url, str) and base_url.strip():
            changes["llm_base_url"] = base_url.strip().rstrip("/")
        model = body.get("model")
        if isinstance(model, str) and model.strip():
            changes["llm_model"] = model.strip()
        api_key = body.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            changes["llm_api_key"] = api_key.strip()
        rate_limit = body.get("rate_limit")
        if rate_limit is not None and str(rate_limit).strip() != "":
            os.environ["RATE_LIMIT_PER_MIN"] = str(rate_limit).strip()
        mock = body.get("mock")
        if mock is True:
            changes["llm_mock"] = "true"
        elif mock is False and (changes.get("llm_api_key") or st.llm_api_key):
            changes["llm_mock"] = "false"

        st = replace(st, **changes) if changes else st
        runtime.settings = st
        runtime.llm = build_llm(st)
        return Response(
            200,
            {
                "ok": True,
                "model": st.llm_model,
                "base_url": st.llm_base_url,
                "mock_llm": st.use_mock_llm,
                "rate_limit_per_min": os.getenv("RATE_LIMIT_PER_MIN", ""),
            },
        )

    router.add("GET", "/", ui, auth=False)
    router.add("GET", "/ui", ui, auth=False)
    router.add("GET", "/favicon.ico", lambda req, params: Response(204), auth=False)
    router.add("POST", "/api/ui/llm", ui_llm, auth=True)
