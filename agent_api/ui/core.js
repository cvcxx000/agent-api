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
function safeUrl(u){
  const s = String(u || '').trim();
  if (/^(https?:\/\/|data:image\/|\/files\/|\/ui\/|#)/i.test(s)){ return esc(s); }
  if (s.charAt(0) === '/'){ return esc(s); }
  return esc(s);
}
function linkMd(s){
  return String(s)
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) =>
      '<a class="md-a" href="' + safeUrl(u) + '" target="_blank" rel="noreferrer noopener">' + esc(t) + '</a>')
    .replace(/(^|[\s（(])(https?:\/\/[^\s<）)"]+)/g, (m, p, u) =>
      p + '<a class="md-a" href="' + safeUrl(u) + '" target="_blank" rel="noreferrer noopener">' + esc(u) + '</a>');
}
function boldMd(s){
  const parts = String(s).split('**');
  let out = '';
  for (let i = 0; i < parts.length; i++){
    out += (i % 2 === 1) ? '<b>' + linkMd(parts[i]) + '</b>' : linkMd(parts[i]);
  }
  return out;
}
function inlineMd(s){
  const parts = String(s).split('`');
  let out = '';
  for (let i = 0; i < parts.length; i++){
    out += (i % 2 === 1) ? '<code>' + esc(parts[i]) + '</code>' : boldMd(parts[i]);
  }
  return out;
}
function codeBlock(raw){
  let body = String(raw);
  let lang = '';
  const p = body.indexOf(NL);
  if (p >= 0 && body.slice(0, p).length <= 20 && body.slice(0, p).indexOf(' ') < 0){
    lang = body.slice(0, p).trim();
    body = body.slice(p + 1);
  }
  while (body.slice(-1) === NL){ body = body.slice(0, -1); }
  return '<div class="codebox"><div class="codebar"><span class="lang">' + esc(lang || 'text') +
    '</span><button class="copy" type="button">复制</button></div><pre class="code">' + esc(body) + '</pre></div>';
}
const IMG_RE = /^!\[([^\]]*)\]\(([^)\s]+)\)$/;
const VID_RE = /^(\/files\/[^\s]+\.(?:mp4|webm|mov))$/i;
const AUD_RE = /^(\/files\/[^\s]+\.(?:mp3|wav|m4a|ogg|opus|flac|aac))$/i;

function textBlocks(txt){
  const lines = String(txt).split(NL);
  let html = '';
  let para = [];
  let inList = false;
  let inQuote = false;
  function flushPara(){
    if (para.length){ html += '<p>' + inlineMd(para.join(' ')) + '</p>'; para = []; }
  }
  function closeList(){
    if (inList){ html += '</ul>'; inList = false; }
  }
  function closeQuote(){
    if (inQuote){ html += '</blockquote>'; inQuote = false; }
  }
  for (let i = 0; i < lines.length; i++){
    const t = lines[i].trim();
    if (!t){ flushPara(); closeList(); closeQuote(); continue; }

    const img = t.match(IMG_RE);
    if (img){
      flushPara(); closeList(); closeQuote();
      html += '<div class="md-fig"><a href="' + safeUrl(img[2]) + '" target="_blank" rel="noreferrer noopener">' +
        '<img class="md-img" loading="lazy" alt="' + esc(img[1]) + '" src="' + safeUrl(img[2]) + '"></a></div>';
      continue;
    }
    const aud = t.match(AUD_RE);
    if (aud){
      flushPara(); closeList(); closeQuote();
      html += '<div class="md-fig"><audio class="md-audio" controls preload="metadata" src="' +
        safeUrl(aud[1]) + '"></audio></div>';
      continue;
    }
    const vid = t.match(VID_RE);
    if (vid){
      flushPara(); closeList(); closeQuote();
      html += '<div class="md-fig"><video class="md-video" controls playsinline preload="metadata" src="' +
        safeUrl(vid[1]) + '"></video></div>';
      continue;
    }

    if (t.slice(0, 2) === '- ' || t.slice(0, 2) === '* '){
      flushPara(); closeQuote();
      if (!inList){ html += '<ul>'; inList = true; }
      html += '<li>' + inlineMd(t.slice(2)) + '</li>';
      continue;
    }
    if (t.charAt(0) === '>'){
      flushPara(); closeList();
      if (!inQuote){ html += '<blockquote>'; inQuote = true; }
      html += inlineMd(t.slice(1).trim()) + '<br>';
      continue;
    }
    if (t.charAt(0) === '#'){
      let lvl = 0;
      while (t.charAt(lvl) === '#'){ lvl += 1; }
      if (lvl > 0 && lvl < 7){
        flushPara(); closeList(); closeQuote();
        html += '<div class="h h' + lvl + '">' + inlineMd(t.slice(lvl).trim()) + '</div>';
        continue;
      }
    }
    const dot = t.indexOf('. ');
    if (dot > 0 && dot < 4 && isDigit(t.charAt(0))){
      flushPara(); closeList(); closeQuote();
      html += '<p>' + inlineMd(t) + '</p>';
      continue;
    }
    para.push(t);
  }
  flushPara(); closeList(); closeQuote();
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

document.addEventListener('click', (ev) => {
  const btn = ev.target.closest ? ev.target.closest('.copy') : null;
  if (!btn){ return; }
  const pre = btn.parentElement && btn.parentElement.parentElement
    ? btn.parentElement.parentElement.querySelector('pre') : null;
  const text = pre ? pre.textContent : '';
  const done = () => { btn.textContent = '已复制'; setTimeout(() => { btn.textContent = '复制'; }, 1200); };
  if (navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
});
function fallbackCopy(text, done){
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { note('复制失败，手动选中吧'); }
  ta.remove();
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

let lastUsage = null;

function fmtTokens(n){
  const v = Number(n || 0);
  if (v >= 10000){ return (v / 10000).toFixed(1) + '万'; }
  if (v >= 1000){ return (v / 1000).toFixed(1) + 'k'; }
  return String(Math.round(v));
}

function showUsage(u){
  if (u !== undefined){ lastUsage = u || {}; }
  const el = $('usage');
  if (!el){ return; }
  const box = lastUsage || {};
  const prompt = Number(box.prompt || 0);
  const cached = Number(box.cached || 0);
  if (!prompt){
    el.textContent = '缓存 —';
    el.title = '这个模型/服务没返回 token 用量，算不出命中率';
    el.classList.remove('hot');
    return;
  }
  const rate = Math.round((cached / prompt) * 100);
  el.textContent = '缓存 ' + rate + '% · ' + fmtTokens(cached) + '/' + fmtTokens(prompt);
  el.title = '本会话累计：提示 ' + prompt + ' tokens，命中缓存 ' + cached + '，生成 ' + Number(box.completion || 0) +
    '，模型调用 ' + Number(box.calls || 0) + ' 次';
  el.classList.toggle('hot', rate >= 50);
}
function addUser(text, attachments){
  const d = document.createElement('div');
  d.className = 'msg user';
  d.innerHTML = '<div class="who">你</div><div class="bubble">' + esc(text) + '</div>';
  const list = attachments || [];
  if (list.length){
    const thumbs = document.createElement('div');
    thumbs.className = 'bubble-thumbs';
    list.forEach((a) => {
      const img = document.createElement('img');
      img.src = a.url || ('/files/' + a.path);
      img.alt = a.name || '附件';
      img.loading = 'lazy';
      thumbs.appendChild(img);
    });
    d.appendChild(thumbs);
  }
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
  const media = String(text || '');
  const imgMatch = media.match(/MEDIA_IMAGE: (\S+)/);
  const vidMatch = media.match(/MEDIA_VIDEO: (\S+)/);
  const audMatch = media.match(/MEDIA_AUDIO: (\S+)/);
  const holder = rec.el.querySelector('.mediaHolder') || document.createElement('div');
  holder.className = 'mediaHolder';
  if (imgMatch){
    holder.innerHTML = '';
    const img = document.createElement('img');
    img.className = 'md-img';
    img.src = imgMatch[1];
    img.alt = name;
    img.loading = 'lazy';
    holder.appendChild(img);
  } else if (vidMatch){
    holder.innerHTML = '';
    const video = document.createElement('video');
    video.className = 'md-video';
    video.controls = true;
    video.playsInline = true;
    video.preload = 'metadata';
    video.src = vidMatch[1];
    holder.appendChild(video);
  }
  if (audMatch && audMatch[1]){
    holder.innerHTML = '';
    const audio = document.createElement('audio');
    audio.className = 'md-audio';
    audio.controls = true;
    audio.preload = 'metadata';
    audio.src = audMatch[1];
    holder.appendChild(audio);
  }
  if (holder.innerHTML){ rec.el.appendChild(holder); }
  scrollDown();
}

function headers(){
  const h = { 'Content-Type': 'application/json' };
  const t = localStorage.getItem(LS.token);
  if (t){ h['X-API-Token'] = t; }
  return h;
}
function needLogin(resp, path){
  if (resp.status === 401 && !String(path).startsWith('/api/auth')){
    location.href = '/login';
    return true;
  }
  return false;
}
async function apiGet(path){
  const resp = await fetch(path, { headers: headers() });
  if (needLogin(resp, path)) throw new Error('需要登录');
  if (!resp.ok){ throw new Error('HTTP ' + resp.status + ' ' + (await resp.text()).slice(0, 160)); }
  return resp.json();
}
async function apiPost(path, body){
  const resp = await fetch(path, { method: 'POST', headers: headers(), body: JSON.stringify(body || {}) });
  if (needLogin(resp, path)) throw new Error('需要登录');
  const text = await resp.text();
  let data = null;
  try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
  if (!resp.ok){ throw new Error((data && data.error) || ('HTTP ' + resp.status + ' ' + text.slice(0, 160))); }
  return data;
}

async function apiDelete(path){
  const resp = await fetch(path, { method: 'DELETE', headers: headers() });
  if (needLogin(resp, path)) throw new Error('需要登录');
  const text = await resp.text();
  let data = null;
  try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
  if (!resp.ok){ throw new Error((data && data.error) || ('HTTP ' + resp.status + ' ' + text.slice(0, 160))); }
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
    const pub = await apiGet('/api/public/summary');
    const n = pub.providers || 0;
    const on = pub.providers_enabled || 0;
    const cur = (pub.current && pub.current.model) ? pub.current.model : '未配置';
    $('providerSub').textContent = pub.guest
      ? '游客模式 · 用设备当前模型'
      : (pub.member ? '会员模式 · 商家模型 · ' + cur : n + ' 家 · ' + on + ' 家启用 · ' + cur);
    $('curModel').textContent = (pub.current && pub.current.base_url)
      ? (cur + ' @ ' + pub.current.base_url) : '未配置';
    const chip = $('modelChip');
    if (chip) chip.textContent = pub.active_name ? (pub.active_name + ' · ' + cur) : '未配置模型';
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

// 游客模式：藏起改全局设置的入口，顶部显示身份与到期
(async function guestBadge(){
  try{
    const resp = await fetch('/api/auth/me', { credentials: 'same-origin' });
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data.me || !data.me.guest) {
      if (data.me && data.me.role === 'user' && data.me.billing_enabled) {
        document.body.classList.add('member');
      }
      return;
    }
    document.body.classList.add('guest');
    const bar = document.createElement('div');
    bar.className = 'guestBar';
    const label = document.createElement('span');
    label.textContent = '游客模式 · 独立空间 · 到期 ' + (data.me.guest_expires_at || '—') + ' · 每分钟 1 条';
    const quit = document.createElement('button');
    quit.textContent = '退出游客登录';
    quit.className = 'guestQuit';
    quit.addEventListener('click', async () => {
      try{ await apiPost('/api/auth/logout', {}); }catch (e){}
      location.href = '/login';
    });
    bar.appendChild(label);
    bar.appendChild(quit);
    document.body.insertBefore(bar, document.body.firstChild);
  }catch (e){ /* 老版本 / 没开账号体系，忽略 */ }
})();
