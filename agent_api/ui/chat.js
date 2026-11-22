async function send(){
  if (busy){ return; }
  const raw = $('input').value.trim();
  if (!raw){ return; }
  const hero = document.querySelector('.hero');
  if (hero){ hero.remove(); }
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
$('btnNew').addEventListener('click', () => { startNew(); welcomeHero(); openDrawer(false); });
$('badge').addEventListener('click', () => { openDrawer(false); pushSheet('subagents', '子任务', '最多 3 个并行', null); });
$('goSkills').addEventListener('click', () => { openDrawer(false); pushSheet('skills', '技能', 'SKILL.md 载入后长期生效', () => showSkills()); });
$('goWorkflows').addEventListener('click', () => { openDrawer(false); pushSheet('workflows', '工作流', 'JSON 步骤', () => showWorkflows()); });
$('goSubagents').addEventListener('click', () => { openDrawer(false); pushSheet('subagents', '子任务', '最多 3 个并行', null); });
$('goFiles').addEventListener('click', () => { openDrawer(false); pushSheet('files', '文件', '沙箱 workspace', () => showFiles()); });
$('goProviders').addEventListener('click', () => { openDrawer(false); pushSheet('providers', '提供商', '启用与切换', () => showProviders()); });
$('goSettings').addEventListener('click', () => { openDrawer(false); pushSheet('settings', '设置', '模型与服务', () => showSettings()); });
$('btnHeaderSettings').addEventListener('click', () => { openDrawer(false); pushSheet('settings', '设置', '模型与服务', () => showSettings()); });
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
refreshStatus();
refreshProviderSummary();
refreshSubBadge();
setInterval(refreshSubBadge, 8000);
function welcomeHero(){
  const box = document.createElement('div');
  box.className = 'hero';
  const t = document.createElement('div');
  t.className = 'hero-t';
  t.textContent = '今天想让 Agent 做点什么？';
  const sub = document.createElement('div');
  sub.className = 'hero-s';
  sub.textContent = '零依赖 · 跑在本机 · 可读写文件、执行代码、载入技能';
  box.appendChild(t);
  box.appendChild(sub);
  const row = document.createElement('div');
  row.className = 'hero-chips';
  const ideas = [
    ['看一下工作目录', '看一下工作目录里有什么？'],
    ['写个脚本', '帮我写一个 Python 脚本，统计 workspace 里每个文件的大小'],
    ['整理文件', '列出 workspace 里最大的 5 个文件，并给出整理建议'],
  ];
  ideas.forEach(it => {
    const b = document.createElement('button');
    b.className = 'hero-chip';
    b.textContent = it[0];
    b.addEventListener('click', () => {
      $('input').value = it[1];
      $('input').focus();
      $('input').dispatchEvent(new Event('input'));
    });
    row.appendChild(b);
  });
  box.appendChild(row);
  wrapEl().appendChild(box);
}

welcomeHero();
