"""极简 Web 聊天界面：GET /ui 返回单文件页面，直接调本服务的 /api/chat/stream。

页面只有一个文件，不依赖任何前端框架/外链 CDN，离线也能用。
"""

from __future__ import annotations

UI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#faf9f5">
<title>Agent</title>
<style>
:root{
  --bg:#faf9f5; --panel:#f2efe8; --ink:#23211d; --dim:#7a756c;
  --line:#e4dfd3; --accent:#c96442; --accent-soft:#f3e3db; --code:#2b2925;
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.65 -apple-system,"PingFang SC","Noto Sans CJK SC",system-ui,sans-serif;
  display:flex; flex-direction:column;
}
header{
  display:flex; align-items:center; gap:8px; padding:10px 14px;
  border-bottom:1px solid var(--line); background:var(--bg);
  position:sticky; top:0; z-index:5;
}
header .title{font-weight:600; letter-spacing:.02em; flex:1}
header .title small{display:block; font-weight:400; color:var(--dim); font-size:11px}
header button{
  border:1px solid var(--line); background:#fff; color:var(--ink);
  border-radius:10px; padding:6px 10px; font-size:13px; cursor:pointer;
}
header button:active{transform:scale(.97)}
#settings{display:none; gap:8px; flex-wrap:wrap; padding:10px 14px; background:var(--panel); border-bottom:1px solid var(--line)}
#settings input{
  flex:1; min-width:140px; padding:8px 10px; font-size:13px;
  border:1px solid var(--line); border-radius:10px; background:#fff; color:var(--ink);
}
#log{flex:1; overflow-y:auto; padding:18px 14px 8px; scroll-behavior:smooth}
.wrap{max-width:760px; margin:0 auto}
.msg{margin:0 0 18px; display:flex; flex-direction:column; gap:6px}
.msg .who{font-size:11px; color:var(--dim); letter-spacing:.08em; text-transform:uppercase}
.user .bubble{
  align-self:flex-end; max-width:85%; background:var(--accent-soft);
  border-radius:14px 14px 4px 14px; padding:10px 13px; white-space:pre-wrap;
}
.bot .text{white-space:pre-wrap}
.bot .text strong{font-weight:600}
.tool{
  border:1px solid var(--line); border-radius:12px; background:#fff;
  margin:0 0 10px; overflow:hidden;
}
.tool summary{
  cursor:pointer; padding:8px 12px; font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
  color:var(--code); display:flex; gap:8px; align-items:center; list-style:none;
}
.tool summary::-webkit-details-marker{display:none}
.tool .tag{background:var(--panel); border-radius:6px; padding:1px 6px; color:var(--dim)}
.tool pre{
  margin:0; padding:10px 12px; border-top:1px solid var(--line);
  font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
  white-space:pre-wrap; word-break:break-word; color:#3a3730; background:#fbfaf7;
  max-height:280px; overflow:auto;
}
.sysmsg{color:var(--dim); font-size:12px; text-align:center; margin:10px 0}
.err{color:#a33b25; font-size:13px}
footer{
  border-top:1px solid var(--line); background:var(--bg);
  padding:10px 12px calc(12px + env(safe-area-inset-bottom));
}
.composer{max-width:760px; margin:0 auto; display:flex; gap:8px; align-items:flex-end}
#input{
  flex:1; resize:none; min-height:44px; max-height:38vh;
  padding:11px 12px; font:15px/1.5 inherit; color:var(--ink);
  border:1px solid var(--line); border-radius:14px; background:#fff;
  outline:none;
}
#input:focus{border-color:#cfc6b4}
#send{
  width:52px; height:44px; border:0; border-radius:12px; cursor:pointer;
  background:var(--accent); color:#fff; font-size:14px;
}
#send[disabled]{opacity:.45}
.hint{max-width:760px; margin:6px auto 0; font-size:11px; color:var(--dim); display:flex; gap:10px}
#status{font-size:11px; color:var(--dim)}
@media(max-width:520px){
  #log{padding:14px 12px 6px}
  .user .bubble{max-width:92%}
}
</style>
</head>
<body>
<header>
  <div class="title">Agent<small id="status">连接中…</small></div>
  <button id="btnNew">新会话</button>
  <button id="btnSet">设置</button>
</header>
<div id="settings">
  <input id="inpToken" placeholder="API_TOKEN（服务端没设可留空）" autocomplete="off">
  <input id="inpSystem" placeholder="系统提示词（可选，下一次生效）" autocomplete="off">
  <button id="btnSave">保存</button>
</div>
<main id="log"><div class="wrap" id="wrap"></div></main>
<footer>
  <div class="composer">
    <textarea id="input" rows="1" placeholder="让 Agent 干活…（Enter 发送，Shift+Enter 换行）"></textarea>
    <button id="send" title="发送">↑</button>
  </div>
  <div class="hint"><span id="sid">session: -</span><span id="ask">/exit 退出</span></div>
</footer>
<script>
const $ = (id) => document.getElementById(id);
const NL = String.fromCharCode(10);
const LS = {
  token: 'agent.token',
  session: 'agent.session',
  system: 'agent.system',
};
let sessionId = localStorage.getItem(LS.session) || null;
let busy = false;

function esc(s){
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function wrapEl(){ return $('wrap'); }
function scrollDown(){ $('log').scrollTop = $('log').scrollHeight; }
function note(text, cls){
  const d = document.createElement('div');
  d.className = 'sysmsg ' + (cls || '');
  d.textContent = text;
  wrapEl().appendChild(d); scrollDown();
}
function showSession(){
  $('sid').textContent = 'session: ' + (sessionId ? sessionId.slice(0, 14) : '新建');
}
function addUser(text){
  const d = document.createElement('div');
  d.className = 'msg user';
  d.innerHTML = '<div class="who">你</div><div class="bubble">' + esc(text) + '</div>';
  wrapEl().appendChild(d); scrollDown();
  return d;
}
function addBot(){
  const d = document.createElement('div');
  d.className = 'msg bot';
  d.innerHTML = '<div class="who">Agent</div><div class="text"></div>';
  wrapEl().appendChild(d); scrollDown();
  return d.querySelector('.text');
}
function appendText(node, text){
  node.textContent += text;
  scrollDown();
}
function addTool(name, args){
  const d = document.createElement('details');
  d.className = 'tool';
  const brief = JSON.stringify(args || {}).slice(0, 120);
  d.innerHTML = '<summary><span class="tag">工具</span><b>' + esc(name) + '</b><span>' + esc(brief) + '</span></summary><pre>调用中…</pre>';
  wrapEl().appendChild(d); scrollDown();
  return d;
}
function fillTool(d, text){
  const pre = d.querySelector('pre');
  if (pre) { pre.textContent = String(text || ''); }
  scrollDown();
}

async function refreshStatus(){
  try{
    const r = await fetch('/health');
    const j = await r.json();
    $('status').textContent = '在线 · ' + (j.mock_llm ? 'Mock 模式（未配 Key）' : '已连真实模型');
  }catch(e){
    $('status').textContent = '离线';
  }
}

function headers(){
  const h = {'Content-Type': 'application/json'};
  const t = localStorage.getItem(LS.token);
  if (t) h['X-API-Token'] = t;
  return h;
}

async function send(){
  if (busy) return;
  const raw = $('input').value.trim();
  if (!raw) return;
  $('input').value = '';
  $('input').style.height = 'auto';
  busy = true; $('send').disabled = true;
  addUser(raw);

  const body = {message: raw};
  if (sessionId) body.session_id = sessionId;
  const sys = localStorage.getItem(LS.system);
  if (sys && !sessionId) body.system_prompt = sys;

  const bot = addBot();
  const tools = new Map();
  let finalText = '';

  try{
    const resp = await fetch('/api/chat/stream', {method:'POST', headers: headers(), body: JSON.stringify(body)});
    if (!resp.ok || !resp.body){
      const txt = await resp.text().catch(()=> '');
      throw new Error('HTTP ' + resp.status + ' ' + txt.slice(0, 200));
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true){
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      let idx;
      while ((idx = buf.indexOf(NL + NL)) >= 0){
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const dataLine = chunk.split(NL).find(l => l.startsWith('data: '));
        if (!dataLine) continue;
        let ev;
        try { ev = JSON.parse(dataLine.slice(6)); } catch(e){ continue; }
        handleEvent(ev, bot, tools);
      }
      if (buf.indexOf('data: ') === 0 && buf.length > 4000) buf = '';
    }
    if (finalText) bot.textContent = finalText;
  }catch(e){
    const p = document.createElement('div');
    p.className = 'err';
    p.textContent = '出错：' + (e && e.message ? e.message : e);
    bot.parentElement.appendChild(p);
  }finally{
    busy = false; $('send').disabled = false; $('input').focus();
    showSession();
  }
}

function handleEvent(ev, bot, tools){
  const type = ev.type;
  if (type === 'assistant'){
    if (ev.content) appendText(bot, ev.content);
  } else if (type === 'tool_call'){
    tools.set(ev.id, addTool(ev.name, ev.arguments));
  } else if (type === 'tool_result'){
    const d = tools.get(ev.id);
    if (d) fillTool(d, ev.output);
    else addTool(ev.name, {}).querySelector('pre').textContent = String(ev.output || '');
  } else if (type === 'warning'){
    note(ev.message || '', '');
  } else if (type === 'error'){
    note('错误：' + (ev.message || ''), 'err');
  } else if (type === 'final'){
    if (ev.session_id){ sessionId = ev.session_id; localStorage.setItem(LS.session, sessionId); }
    note('完成 · ' + (ev.turns || 0) + ' 轮', '');
    showSession();
  } else if (type === 'end'){
    if (ev.content) bot.textContent = ev.content;
  }
}

$('send').addEventListener('click', send);
$('input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing){
    e.preventDefault(); send();
  }
});
$('input').addEventListener('input', () => {
  const el = $('input');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, window.innerHeight * 0.38) + 'px';
});
$('btnNew').addEventListener('click', () => {
  sessionId = null; localStorage.removeItem(LS.session);
  wrapEl().innerHTML = ''; showSession(); note('已新建会话');
});
$('btnSet').addEventListener('click', () => {
  const p = $('settings');
  p.style.display = p.style.display === 'flex' ? 'none' : 'flex';
});
$('btnSave').addEventListener('click', () => {
  localStorage.setItem(LS.token, $('inpToken').value.trim());
  localStorage.setItem(LS.system, $('inpSystem').value.trim());
  note('设置已保存到本机浏览器');
});
$('inpToken').value = localStorage.getItem(LS.token) || '';
$('inpSystem').value = localStorage.getItem(LS.system) || '';
showSession();
refreshStatus();
$('input').focus();
</script>
</body>
</html>
"""


def mount(router) -> None:
    """把 UI 路由挂到服务上（不需要鉴权，页面自己带 X-API-Token 调接口）。"""
    from .server import Response

    def ui(req, params):
        return Response(200, UI_HTML, content_type="text/html; charset=utf-8")

    router.add("GET", "/", ui, auth=False)
    router.add("GET", "/ui", ui, auth=False)
    router.add("GET", "/favicon.ico", lambda req, params: Response(204), auth=False)
