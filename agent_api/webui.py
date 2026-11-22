"""自包含 Web 聊天页：GET / 与 /ui 返回单文件 HTML，自带 SSE 解析与工具卡片。

不依赖任何前端构建或 CDN；加 /ui 只是为了不和以后可能的静态首页冲突。
"""

from __future__ import annotations

from .server import Response, Router

UI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Agent</title>
<style>
:root{
  --bg:#f7f4ef;--panel:#fffdfa;--ink:#29241f;--muted:#8a8179;--line:#e6ded4;
  --accent:#c2683f;--accent-soft:#f3e3d9;--ok:#4a7a58;--err:#a8443a;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%;margin:0}
body{
  background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif;
  display:flex;flex-direction:column;overflow:hidden;
}
header{
  display:flex;align-items:center;gap:10px;padding:10px 14px;
  background:rgba(255,253,250,.86);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
}
header .dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none}
header .dot.on{background:var(--ok)}
header .dot.off{background:var(--err)}
header h1{font-size:15px;font-weight:600;margin:0;letter-spacing:.3px}
header .sid{font:12px/1 var(--mono);color:var(--muted);margin-left:auto;max-width:38%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
header button{
  border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:9px;
  padding:6px 10px;font-size:13px;cursor:pointer;
}
header button:active{background:var(--accent-soft)}
#log{flex:1;overflow-y:auto;padding:18px 14px 8px;display:flex;flex-direction:column;gap:14px;scroll-behavior:smooth}
.row{display:flex;gap:10px;align-items:flex-start;max-width:900px;width:100%;margin:0 auto}
.row.user{justify-content:flex-end}
.avatar{width:26px;height:26px;border-radius:8px;flex:none;display:grid;place-items:center;font-size:12px;font-weight:600}
.row.user .avatar{background:var(--accent-soft);color:var(--accent);order:2}
.row.agent .avatar{background:#eae4dc;color:#6b625a}
.bubble{
  padding:10px 13px;border-radius:13px;white-space:pre-wrap;word-break:break-word;
  max-width:calc(100% - 46px);border:1px solid transparent;
}
.row.user .bubble{background:var(--accent-soft);border-color:#eed9cb}
.row.agent .bubble{background:var(--panel);border-color:var(--line);box-shadow:0 1px 0 rgba(0,0,0,.02)}
.bubble.plain{background:transparent!important;border-color:transparent!important;box-shadow:none!important;padding-left:0;color:var(--muted);font-size:13px}
.tool{
  width:100%;max-width:calc(100% - 46px);border:1px solid var(--line);border-radius:11px;
  background:#fbf8f4;overflow:hidden;font-size:13px;
}
.tool summary{cursor:pointer;padding:8px 11px;display:flex;gap:8px;align-items:center;list-style:none}
.tool summary::-webkit-details-marker{display:none}
.tool .tag{font:11px/1 var(--mono);padding:3px 6px;border-radius:5px;background:#ece5dc;color:#6b625a;flex:none}
.tool .name{font-family:var(--mono);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tool .st{margin-left:auto;font-size:11px;color:var(--muted);flex:none}
.tool pre{
  margin:0;padding:0 11px 10px;font:12.5px/1.55 var(--mono);color:#57504a;
  white-space:pre-wrap;word-break:break-all;max-height:340px;overflow:auto;
}
.tool .args{color:#8a8179;border-bottom:1px dashed var(--line);padding-bottom:8px}
section.compose{border-top:1px solid var(--line);background:rgba(255,253,250,.92);backdrop-filter:blur(8px);padding:10px 12px 12px}
.opts{display:none;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.opts.show{display:flex}
.opts input{
  flex:1;min-width:200px;border:1px solid var(--line);background:var(--panel);border-radius:9px;
  padding:7px 9px;font-size:13px;color:var(--ink)
}
.opts label{font-size:12px;color:var(--muted);align-self:center}
.composer{display:flex;gap:8px;align-items:flex-end;max-width:900px;margin:0 auto}
#input{
  flex:1;resize:none;border:1px solid var(--line);border-radius:12px;background:var(--panel);
  padding:10px 12px;font:15px/1.5 inherit;color:var(--ink);max-height:140px;min-height:44px;
}
#input:focus{outline:none;border-color:#d9c6b8}
#send{
  border:none;background:var(--accent);color:#fff;border-radius:12px;padding:0 16px;height:44px;
  font-size:14px;font-weight:600;cursor:pointer;flex:none
}
#send:disabled{opacity:.45;cursor:not-allowed}
#send.stop{background:#8a8179}
.hint{text-align:center;font-size:11px;color:var(--muted);margin-top:7px}
@media (max-width:520px){
  header h1{font-size:14px}
  header .sid{display:none}
  #log{padding:14px 10px 6px}
  .bubble{max-width:calc(100% - 38px)}
  .tool{max-width:calc(100% - 38px)}
}
</style>
</head>
<body>
<header>
  <span class="dot" id="dot"></span>
  <h1>Agent</h1>
  <span class="sid" id="sid">new session</span>
  <button id="toggle">设置</button>
  <button id="reset">新会话</button>
</header>
<div id="log"></div>
<section class="compose">
  <div class="opts" id="opts">
    <input id="token" type="password" placeholder="X-API-Token（服务未设密码则留空）" autocomplete="off">
    <input id="base" placeholder="服务地址，留空=当前页" autocomplete="off">
    <input id="system" placeholder="系统提示词（留空=用服务端默认）" autocomplete="off">
  </div>
  <div class="composer">
    <textarea id="input" rows="1" placeholder="说点什么，Enter 发送，Shift+Enter 换行"></textarea>
    <button id="send">发送</button>
  </div>
  <div class="hint">工具调用会实时显示，结果可展开</div>
</section>
<script>
(function(){
  var $ = function(id){ return document.getElementById(id); };
  var log = $('log'), input = $('input'), send = $('send'), sidEl = $('sid'), dot = $('dot');
  var memory = {};
  try { memory = JSON.parse(localStorage.getItem('agentui') || '{}'); } catch(e){ memory = {}; }
  var state = {
    sessionId: memory.sessionId || '',
    token: memory.token || '',
    base: memory.base || '',
    system: memory.system || '',
    busy: false, ctrl: null, lastBubble: null
  };
  $('token').value = state.token;
  $('base').value = state.base;
  $('system').value = state.system;
  sidEl.textContent = state.sessionId || 'new session';

  function persist(){
    try { localStorage.setItem('agentui', JSON.stringify({
      sessionId: state.sessionId, token: state.token, base: state.base, system: state.system
    })); } catch(e){}
  }
  function apiBase(){
    var b = (state.base || '').trim().replace(/\/+$/, '');
    if (!b && location.protocol.indexOf('http') === 0) b = location.origin;
    return b;
  }
  function headers(){
    var h = {'Content-Type':'application/json'};
    if (state.token) h['X-API-Token'] = state.token;
    return h;
  }
  function atBottom(){
    return log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  }
  function scrollDown(force){
    if (force || atBottom()) { log.scrollTop = log.scrollHeight; }
  }
  function bubble(role, text){
    var row = document.createElement('div');
    row.className = 'row ' + role;
    var av = document.createElement('div');
    av.className = 'avatar';
    av.textContent = role === 'user' ? '你' : 'AI';
    var b = document.createElement('div');
    b.className = 'bubble';
    b.textContent = text;
    row.appendChild(av); row.appendChild(b);
    log.appendChild(row);
    scrollDown(true);
    return b;
  }
  function note(text, cls){
    var row = document.createElement('div');
    row.className = 'row agent';
    var b = document.createElement('div');
    b.className = 'bubble plain';
    if (cls) b.style.color = 'var(--err)';
    b.textContent = text;
    row.appendChild(b);
    log.appendChild(row);
    scrollDown(true);
  }
  function toolCard(name, args){
    var d = document.createElement('details');
    d.className = 'tool';
    var s = document.createElement('summary');
    var tag = document.createElement('span'); tag.className = 'tag'; tag.textContent = 'tool';
    var nm = document.createElement('span'); nm.className = 'name'; nm.textContent = name;
    var st = document.createElement('span'); st.className = 'st'; st.textContent = '运行中';
    s.appendChild(tag); s.appendChild(nm); s.appendChild(st);
    d.appendChild(s);
    var pre = document.createElement('pre');
    pre.className = 'args';
    pre.textContent = '$ ' + name + (args && args !== '{}' ? ' ' + args : '');
    d.appendChild(pre);
    log.appendChild(d);
    scrollDown();
    return {card: d, status: st, pre: pre};
  }
  function setBusy(b){
    state.busy = b;
    send.textContent = b ? '停止' : '发送';
    send.classList.toggle('stop', b);
    dot.className = 'dot ' + (b ? 'on' : (dot.className.indexOf('off') >= 0 ? 'off' : ''));
  }
  function health(){
    var b = apiBase();
    if (!b) { return; }
    fetch(b + '/health').then(function(r){ return r.json(); }).then(function(j){
      if (j && j.data && j.data.mock_llm) {
        dot.className = 'dot on';
        note('当前是 Mock 模式：没读到 LLM_API_KEY，先去 .env 里填真 Key。');
      } else {
        dot.className = 'dot on';
      }
    }).catch(function(){ dot.className = 'dot off'; });
  }
  function handleEvent(ev){
    var t = ev.type;
    if (t === 'assistant') {
      if (state.lastBubble && !state.lastBubble.dataset.final) {
        state.lastBubble.textContent = ev.content || '';
      } else {
        state.lastBubble = bubble('agent', ev.content || '');
      }
    } else if (t === 'tool_call') {
      state.lastBubble = null;
      var c = toolCard(ev.name || 'tool', ev.arguments || '{}');
      state.lastCard = c;
    } else if (t === 'tool_result') {
      var card = state.lastCard;
      if (card) {
        card.status.textContent = (ev.ok === false ? '失败' : '完成');
        card.status.style.color = ev.ok === false ? 'var(--err)' : 'var(--ok)';
        var out = ev.output;
        if (typeof out !== 'string') { try { out = JSON.stringify(out, null, 2); } catch(e){ out = String(out); } }
        var p = document.createElement('pre');
        p.textContent = out || '(空)';
        card.card.appendChild(p);
        card.status = card.status;
      }
    } else if (t === 'warning') {
      note('⚠ ' + (ev.message || ''));
    } else if (t === 'error') {
      note('错误：' + (ev.message || ''));
    } else if (t === 'final') {
      if (ev.session_id) { state.sessionId = ev.session_id; sidEl.textContent = state.sessionId; persist(); }
      if (state.lastBubble) state.lastBubble.dataset.final = '1';
      else if (ev.content) bubble('agent', ev.content);
    }
  }
  function parseChunk(buf){
    var events = [], blocks = buf.split('\n\n');
    var rest = blocks.pop();
    blocks.forEach(function(block){
      var dataLines = [];
      block.split('\n').forEach(function(line){
        if (line.indexOf('data:') === 0) { dataLines.push(line.slice(5).replace(/^ /, '')); }
      });
      if (!dataLines.length) { return; }
      try { events.push(JSON.parse(dataLines.join('\n'))); } catch(e){}
    });
    return {events: events, rest: rest};
  }
  function sendMessage(){
    var text = input.value.trim();
    if (!text || state.busy) { return; }
    if (!apiBase()) { note('请先在设置里填服务地址'); return; }
    input.value = ''; autoGrow();
    bubble('user', text);
    state.lastBubble = null; state.lastCard = null;
    setBusy(true);
    var ctrl = new AbortController();
    state.ctrl = ctrl;
    var body = {message: text, stream: true};
    if (state.sessionId) { body.session_id = state.sessionId; }
    if (state.system.trim()) { body.system_prompt = state.system.trim(); }
    fetch(apiBase() + '/api/agent/run', {
      method: 'POST', headers: headers(), body: JSON.stringify(body), signal: ctrl.signal
    }).then(function(resp){
      if (!resp.ok) {
        return resp.text().then(function(t){ throw new Error('HTTP ' + resp.status + ' ' + t.slice(0, 300)); });
      }
      var ctype = resp.headers.get('content-type') || '';
      if (ctype.indexOf('event-stream') < 0) {
        return resp.json().then(function(j){ handleEvent({type:'final', content: (j.data && j.data.content) || '', session_id: j.data && j.data.session_id}); });
      }
      var reader = resp.body.getReader(), decoder = new TextDecoder('utf-8'), buf = '';
      function pump(){
        return reader.read().then(function(r){
          if (r.done) { return; }
          buf += decoder.decode(r.value, {stream: true});
          var parsed = parseChunk(buf);
          buf = parsed.rest;
          parsed.events.forEach(handleEvent);
          return pump();
        });
      }
      return pump();
    }).catch(function(err){
      if (err && err.name === 'AbortError') { note('已停止'); }
      else { note('请求失败：' + (err && err.message ? err.message : err)); }
    }).then(function(){
      setBusy(false); state.ctrl = null; scrollDown();
    });
  }
  function autoGrow(){
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  }
  send.addEventListener('click', function(){
    if (state.busy) { if (state.ctrl) state.ctrl.abort(); return; }
    sendMessage();
  });
  input.addEventListener('input', autoGrow);
  input.addEventListener('keydown', function(e){
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  $('toggle').addEventListener('click', function(){ $('opts').classList.toggle('show'); });
  $('reset').addEventListener('click', function(){
    state.sessionId = ''; sidEl.textContent = 'new session'; persist();
    log.innerHTML = ''; note('已开新会话，下一句会建一个新 session。');
  });
  ['token','base','system'].forEach(function(id){
    $(id).addEventListener('change', function(){
      state[id] = $(id).value.trim();
      persist();
      if (id === 'base' || id === 'token') health();
    });
  });
  note('准备就緖。工具调用、子任务派发会实时显示在这里。');
  health();
})();
</script>
</body>
</html>
"""

_LONG_CACHE = {"Cache-Control": "public, max-age=600"}
_NOCACHE = {"Cache-Control": "no-cache"}


def _serve_ui(request, params):
    return Response(200, UI_HTML, headers=_LONG_CACHE, content_type="text/html; charset=utf-8")


def _serve_root(request, params):
    return Response(200, UI_HTML, headers=_NOCACHE, content_type="text/html; charset=utf-8")


def _favicon(request, params):
    return Response(204, None, content_type="image/x-icon")


def mount(router: Router) -> None:
    """把 Web 页挂到根路径。这些接口不需要 X-API-Token。"""
    router.add("GET", "/", _serve_root, auth=False)
    router.add("GET", "/ui", _serve_ui, auth=False)
    router.add("GET", "/favicon.ico", _favicon, auth=False)
