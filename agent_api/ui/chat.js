let attachments = [];
let streamedThisTurn = false;
let curThink = null;
let curThinkBody = null;
let curThinkInfo = null;
let curThinkStarted = 0;
let renderTimer = null;

function pendingAttachments(){
  return attachments.map(a => ({ path: a.path, name: a.name, type: a.type }));
}

function addAttachment(a){
  attachments.push(a);
  renderAttachRow();
}
function renderAttachRow(){
  const row = $('attachRow');
  row.innerHTML = '';
  row.classList.toggle('on', attachments.length > 0);
  attachments.forEach((a, i) => {
    const chip = document.createElement('div');
    chip.className = 'attachChip';
    const img = document.createElement('img');
    img.src = a.url || ('/files/' + a.path);
    img.alt = a.name || '附件';
    chip.appendChild(img);
    const label = document.createElement('span');
    label.textContent = a.name || '图片';
    chip.appendChild(label);
    const rm = document.createElement('button');
    rm.className = 'attachX';
    rm.textContent = '✕';
    rm.title = '移除';
    rm.addEventListener('click', () => { attachments.splice(i, 1); renderAttachRow(); });
    chip.appendChild(rm);
    row.appendChild(chip);
  });
}
window.addAttachment = addAttachment;

async function uploadBlob(blob, name){
  const reader = new FileReader();
  const dataUrl = await new Promise((resolve, reject) => {
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('读图片失败'));
    reader.readAsDataURL(blob);
  });
  const r = await apiPost('/api/ui/upload', { name: name || 'clipboard', data_url: dataUrl });
  return { path: r.path, url: r.url, type: 'image', name: name || '截图' };
}

async function send(){
  if (busy){ return; }
  const raw = $('input').value.trim();
  if (!raw){ return; }
  const hero = document.querySelector('.hero');
  if (hero){ hero.remove(); }
  const atts = pendingAttachments();
  $('input').value = '';
  $('input').style.height = 'auto';
  attachments = [];
  renderAttachRow();
  busy = true;
  $('send').textContent = '■';
  $('send').classList.add('stop');
  addUser(raw, atts);

  const body = { message: raw };
  if (atts.length){ body.attachments = atts; }
  if (sessionId){ body.session_id = sessionId; }
  const sys = localStorage.getItem(LS.system);
  if (sys && !sessionId){ body.system_prompt = sys; }
  const turns = localStorage.getItem(LS.turns);
  if (turns && Number(turns) > 0){ body.max_turns = Number(turns); }

  curBot = null;
  curRaw = '';
  curThink = null;
  curThinkBody = null;
  curThinkInfo = null;
  streamedThisTurn = false;
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
    const msg = e && e.message ? String(e.message) : String(e || '');
    if (e && e.name === 'AbortError'){ note('已停止本轮。'); }
    else if (/ConnectionAborted|ConnectionReset|BrokenPipe|network|Failed to fetch|Load failed|网络|连接中断/i.test(msg)){
      note('连接断了（手机休眠或切后台最容易断）。上面的内容都保留着，再发一次就行。', 'err');
    }
    else { note('出错：' + (msg || e), 'err'); }
  }finally{
    if (renderTimer){ clearTimeout(renderTimer); renderTimer = null; }
    busy = false;
    ctrl = null;
    $('send').textContent = '↑';
    $('send').classList.remove('stop');
    showSession();
    refreshSubBadge();
  }
}

function ensureThink(){
  if (curThink){ return curThink; }
  if (!curBot){ curBot = newBot(''); curRaw = ''; }
  const box = document.createElement('details');
  box.className = 'think';
  box.open = true;
  box.innerHTML = '<summary><span class="tdot"></span>思考中…<small class="tsec"></small></summary>' +
    '<div class="think-body"></div>';
  const holder = curBot.parentElement;
  holder.insertBefore(box, curBot);
  curThink = box;
  curThinkBody = box.querySelector('.think-body');
  curThinkInfo = box.querySelector('summary');
  curThinkStarted = Date.now();
  return box;
}

function closeThink(){
  if (!curThink){ return; }
  const secs = ((Date.now() - curThinkStarted) / 1000).toFixed(1);
  curThinkInfo.innerHTML = '<span class="tdot"></span>思考过程<small class="tsec">' + secs + 's</small>';
  curThink.open = false;
  curThink = null;
  curThinkBody = null;
  curThinkInfo = null;
}

function scheduleRender(){
  if (renderTimer){ return; }
  renderTimer = setTimeout(() => {
    renderTimer = null;
    if (curBot){ curBot.innerHTML = md(curRaw); scrollDown(); }
  }, 60);
}

function handleEvent(ev){
  const type = ev.type;
  if (type === 'reasoning_delta'){
    if (!ev.text){ return; }
    ensureThink();
    curThinkBody.textContent += ev.text;
    curThinkBody.scrollTop = curThinkBody.scrollHeight;
    scrollDown();
  } else if (type === 'content_delta'){
    if (!ev.text){ return; }
    if (!curBot){ curBot = newBot(''); curRaw = ''; }
    closeThink();
    curRaw += ev.text;
    streamedThisTurn = true;
    scheduleRender();
  } else if (type === 'assistant'){
    closeThink();
    if (!streamedThisTurn && ev.content){
      if (!curBot){ curBot = newBot(''); }
      curRaw = String(ev.content);
      curBot.innerHTML = md(curRaw);
      streamedThisTurn = true;
    }
    scrollDown();
  } else if (type === 'tool_call'){
    closeThink();
    curBot = null;
    curRaw = '';
    streamedThisTurn = false;
    addTool(ev.id, ev.name, ev.arguments);
    if (ev.name === 'spawn_agent' || ev.name === 'run_workflow'){ refreshSubBadge(); }
  } else if (type === 'tool_result'){
    fillTool(ev.id, ev.name, ev.output, ev.ok);
    if (ev.name === 'spawn_agent' || ev.name === 'wait_agent'){ refreshSubBadge(); }
  } else if (type === 'warning'){
    note(ev.message || '');
  } else if (type === 'usage'){
    showUsage(ev.usage);
  } else if (type === 'error'){
    note('错误：' + (ev.message || ''), 'err');
  } else if (type === 'end'){
    if (renderTimer){ clearTimeout(renderTimer); renderTimer = null; }
    if (curBot && streamedThisTurn){ curBot.innerHTML = md(curRaw); }
    else if (ev.content && !curBot){
      curBot = newBot(String(ev.content));
      curRaw = String(ev.content);
    }
  } else if (type === 'final'){
    if (ev.session_id){
      sessionId = ev.session_id;
      localStorage.setItem(LS.session, sessionId);
    }
    showUsage(ev.usage);
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
$('input').addEventListener('paste', async (e) => {
  const items = (e.clipboardData && e.clipboardData.items) || [];
  for (const item of items){
    if (item.kind === 'file' && item.type.indexOf('image/') === 0){
      e.preventDefault();
      try{
        const a = await uploadBlob(item.getAsFile(), 'paste');
        addAttachment(a);
        note('已附加剪贴板里的图片。');
      }catch (err){ note('上传失败：' + err.message, 'err'); }
    }
  }
});

$('btnMenu').addEventListener('click', () => openDrawer(true));
$('mask').addEventListener('click', () => openDrawer(false));
$('btnNew').addEventListener('click', () => { startNew(); welcomeHero(); openDrawer(false); });
$('badge').addEventListener('click', () => { openDrawer(false); pushSheet('subagents', '子任务', '最多 3 个并行', null); });
$('goSkills').addEventListener('click', () => { openDrawer(false); pushSheet('skills', '技能', 'SKILL.md 载入后长期生效', () => showSkills()); });
$('goWorkflows').addEventListener('click', () => { openDrawer(false); pushSheet('workflows', '工作流', 'JSON 步骤', () => showWorkflows()); });
$('goSubagents').addEventListener('click', () => { openDrawer(false); pushSheet('subagents', '子任务', '最多 3 个并行', null); });
$('goFiles').addEventListener('click', () => { openDrawer(false); pushSheet('files', '文件', '浏览 / 预览 MD', () => showFiles()); });
$('goSchedules').addEventListener('click', () => { openDrawer(false); pushSheet('schedules', '定时任务', 'cron 到点自动跑', () => showSchedules()); });
$('goProviders').addEventListener('click', () => { openDrawer(false); pushSheet('providers', '提供商', '目录 / 拉模型 / 切换', () => showProviders()); });
$('goSettings').addEventListener('click', () => { openDrawer(false); pushSheet('settings', '设置', '模型 · 搜索 · 图片 · 视频', () => showSettings()); });
$('btnHeaderSettings').addEventListener('click', () => { openDrawer(false); pushSheet('settings', '设置', '模型 · 搜索 · 图片 · 视频', () => showSettings()); });
$('goTerminal').addEventListener('click', () => {
  openDrawer(false);
  pushSheet('terminal', '终端版界面', 'SSH 或 Termux 里直接聊', () => showTerminalHelp());
});
$('modelChip').addEventListener('click', () => {
  pushSheet('models', '选择模型', '只列真实存在的模型', () => showModelPicker());
});
$('sheetBack').addEventListener('click', sheetBack);
$('sheetRefresh').addEventListener('click', () => {
  if (sheetRender){ sheetRender(); return; }
  if (sheetMode === 'subagents'){ showSubagents(); }
});
$('btnClearView').addEventListener('click', () => {
  wrapEl().innerHTML = '';
  toolMap.clear();
  curBot = null;
  curRaw = '';
  curThink = null;
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
$('inpToken').value = localStorage.getItem(LS.token) || '';
$('inpSystem').value = localStorage.getItem(LS.system) || '';
$('inpTurns').value = localStorage.getItem(LS.turns) || '';
showSession();
refreshUsage();
refreshStatus();
refreshProviderSummary();
refreshSubBadge();
setInterval(refreshSubBadge, 8000);

function showTerminalHelp(){
  const w = sheetWrap();
  w.appendChild(el('div', 'sect', '在终端里用同一套能力'));
  w.appendChild(el('div', 'desc',
    '服务跑在手机 Termux 时，另开一个窗口即可。流式输出、思考过程、工具卡、图片/视频路径都会打印出来。'));
  const box = document.createElement('div');
  box.className = 'code';
  box.textContent = 'cd ~/agent-api' + NL + 'python3 scripts/tui.py';
  w.appendChild(box);
  w.appendChild(el('div', 'desc', '带鉴权或远程：'));
  const box2 = document.createElement('div');
  box2.className = 'code';
  box2.textContent = 'python3 scripts/tui.py --base http://127.0.0.1:8000 --token 你的TOKEN';
  w.appendChild(box2);
  w.appendChild(el('div', 'dr-tip',
    '常用命令：/new 新会话 · /model 选模型 · /providers 提供商 · /search 关键词 · /image 提示词 · /video 提示词 · /md 文件 渲染 Markdown · /help'));
}

function welcomeHero(){
  const box = document.createElement('div');
  box.className = 'hero';
  const t = document.createElement('div');
  t.className = 'hero-t';
  t.textContent = '今天想让 Agent 做点什么？';
  const sub = document.createElement('div');
  sub.className = 'hero-s';
  sub.textContent = '零依赖 · 跑在本机 · 会思考、能搜网、能出图和视频';
  box.appendChild(t);
  box.appendChild(sub);
  const row = document.createElement('div');
  row.className = 'hero-chips';
  const ideas = [
    ['看一下工作目录', 'text', '看一下工作目录里有什么？'],
    ['画个草图', 'canvas', ''],
    ['搜一下今天的新闻', 'text', '搜一下今天科技圈有什么新闻，挑三条给我'],
  ];
  ideas.forEach(it => {
    const b = document.createElement('button');
    b.className = 'hero-chip';
    b.textContent = it[0];
    b.addEventListener('click', () => {
      if (it[1] === 'canvas'){ if (window.openBoard){ window.openBoard(); } return; }
      $('input').value = it[2];
      $('input').focus();
      $('input').dispatchEvent(new Event('input'));
    });
    row.appendChild(b);
  });
  box.appendChild(row);
  wrapEl().appendChild(box);
}

welcomeHero();
