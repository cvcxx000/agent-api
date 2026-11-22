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
