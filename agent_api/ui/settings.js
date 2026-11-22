function settingRow(icon, title, sub, onClick){
  const row = el('button', 'row');
  row.appendChild(el('span', 'rico', icon));
  const body = el('span', 'rbody');
  body.appendChild(el('b', '', title));
  body.appendChild(el('i', '', sub));
  row.appendChild(body);
  row.appendChild(el('span', 'rgo', '›'));
  row.addEventListener('click', onClick);
  return row;
}

async function showSettings(){
  const w = sheetWrap();
  try{
    const s = await apiGet('/api/ui/settings');
    w.appendChild(el('div', 'sect', '模型与服务'));
    w.appendChild(settingRow('☆', '默认模型和提示词',
      (s.model || '未配置') + (s.mock_llm ? '（Mock）' : '') + ' · 全局默认提示词',
      () => pushSheet('defaults', '默认模型和提示词', s.model, () => showDefaults())));
    w.appendChild(settingRow('◎', '提供商',
      'Base URL / Key / 模型列表，切一家就生效',
      () => pushSheet('providers', '提供商', '启用与切换', () => showProviders())));
    w.appendChild(settingRow('▤', 'Web 服务器',
      s.host + ':' + s.port + ' · ' + (s.auth_enabled ? '已开鉴权' : '无鉴权'),
      () => pushSheet('server', 'Web 服务器', '监听地址', () => showServer())));
    w.appendChild(el('div', 'sect', '数据设置'));
    w.appendChild(settingRow('▣', '数据备份',
      '会话 / Key / 提示词打成一个 zip',
      () => pushSheet('backup', '数据备份', '下载与恢复', () => showBackup())));
    w.appendChild(settingRow('⚙', '引擎与限流',
      'Python ' + (s.enable_python ? '开' : '关') + ' · Shell ' + (s.enable_shell ? '开' : '关') +
      ' · 限流 ' + s.rate_limit_per_min + '/分',
      () => pushSheet('engine', '引擎与限流', '只读为主', () => showEngine())));
  }catch (e){
    w.innerHTML = '<div class="err">加载设置失败：' + esc(e.message) + '</div>';
  }
}

async function showDefaults(){
  const w = sheetWrap();
  try{
    const s = await apiGet('/api/ui/settings');
    const p = await apiGet('/api/ui/prompt');
    w.appendChild(el('div', 'sect', '当前默认模型'));
    const kv = el('div', 'kv');
    kv.appendChild(el('b', '', s.model || '未配置'));
    kv.appendChild(el('span', '', s.base_url || ''));
    w.appendChild(kv);
    const go = mini('去切换模型');
    go.addEventListener('click', () => pushSheet('providers', '提供商', '启用与切换', () => showProviders()));
    w.appendChild(go);

    w.appendChild(el('div', 'sect', '全局默认系统提示词（新建会话时套用）'));
    const ta = document.createElement('textarea');
    ta.rows = 10;
    ta.value = p.text || '';
    w.appendChild(ta);
    const save = mini('保存', 'primary');
    save.addEventListener('click', async () => {
      try{
        const r = await apiPost('/api/ui/prompt', { text: ta.value });
        note('默认提示词已保存（' + r.chars + ' 字）');
      }catch (e){ note('保存失败：' + e.message, 'err'); }
    });
    const reset = mini('恢复内置');
    reset.addEventListener('click', async () => {
      try{
        const r = await apiDelete('/api/ui/prompt');
        ta.value = (await apiGet('/api/ui/prompt')).text || '';
        note('已恢复内置提示词（' + r.chars + ' 字）');
      }catch (e){ note('恢复失败：' + e.message, 'err'); }
    });
    w.appendChild(save);
    w.appendChild(reset);
    w.appendChild(el('div', 'dr-tip',
      '这里改的是服务端默认提示词（存 data/default_system.md）。只想改某一个会话：☰ → 对话设置里填，新建会话时生效。'));
  }catch (e){
    w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>';
  }
}

async function showServer(){
  const w = sheetWrap();
  try{
    const s = await apiGet('/api/ui/settings');
    const url = 'http://' + (s.host === '0.0.0.0' ? '127.0.0.1' : s.host) + ':' + s.port;
    const add = (k, v) => { const r = el('div', 'kv'); r.appendChild(el('b', '', k)); r.appendChild(el('span', '', v)); w.appendChild(r); };
    w.appendChild(el('div', 'sect', '访问地址'));
    add('网页', url + '/ui');
    add('接口', url + '/api/chat');
    add('监听', s.host + ':' + s.port);
    add('鉴权', s.auth_enabled ? '已开（请求头 X-API-Token）' : '关闭');
    const copy = mini('复制网页地址');
    copy.addEventListener('click', () => {
      const text = url + '/ui';
      if (navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(() => note('已复制：' + text)).catch(() => note(text));
      } else {
        note(text);
      }
    });
    w.appendChild(copy);
    w.appendChild(el('div', 'dr-tip',
      '改监听地址/端口要改 .env 里的 HOST、PORT 再重启服务；手机上默认 http://127.0.0.1:8000/ui。'));
  }catch (e){
    w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>';
  }
}

async function showBackup(){
  const w = sheetWrap();
  try{
    const s = await apiGet('/api/ui/settings');
    w.appendChild(el('div', 'sect', '打包下载'));
    w.appendChild(el('div', 'desc',
      '把 data/ 打成 zip：所有会话（含工具输出）、providers.json（含各家 Key）、default_system.md。'));
    const btn = mini('下载备份 zip', 'primary');
    btn.addEventListener('click', async () => {
      btn.textContent = '打包中…';
      try{
        const resp = await fetch('/api/backup', { headers: headers() });
        if (!resp.ok){ throw new Error('HTTP ' + resp.status); }
        const blob = await resp.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        const d = new Date();
        a.download = 'agent-api-backup-' + d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') +
          String(d.getDate()).padStart(2, '0') + '.zip';
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        btn.textContent = '下载备份 zip';
        note('备份已下载（' + Math.round(blob.size / 1024) + ' KB）');
      }catch (e){
        btn.textContent = '下载备份 zip';
        note('备份失败：' + e.message, 'err');
      }
    });
    w.appendChild(btn);
    w.appendChild(el('div', 'sect', '路径'));
    const add = (k, v) => { const r = el('div', 'kv'); r.appendChild(el('b', '', k)); r.appendChild(el('span', '', v)); w.appendChild(r); };
    add('数据目录', s.data_dir);
    add('沙箱', s.sandbox_root);
    add('技能目录', (s.skills_dirs || []).join(' , '));
    add('工作流', s.workflows_dir);
    w.appendChild(el('div', 'dr-tip',
      '恢复：关掉服务，把 zip 里的 data/ 覆盖回项目目录，再启动。会话和提供商配置都会回来。'));
  }catch (e){
    w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>';
  }
}

async function showEngine(){
  const w = sheetWrap();
  try{
    const s = await apiGet('/api/ui/settings');
    w.appendChild(el('div', 'sect', '能力开关（改 .env 后重启）'));
    const add = (k, v) => { const r = el('div', 'kv'); r.appendChild(el('b', '', k)); r.appendChild(el('span', '', v)); w.appendChild(r); };
    add('执行 Python', s.enable_python ? '允许（超时 ' + s.python_timeout + 's）' : '关闭');
    add('执行 Shell', s.enable_shell ? '允许' : '关闭');
    add('单轮最大工具轮数', String(s.max_turns));
    add('子任务并发', String(s.max_subagents) + '（深度 ' + s.max_subagent_depth + '）');
    w.appendChild(el('div', 'sect', '上游限流（立即生效）'));
    const rate = inputEl(String(s.rate_limit_per_min), '每分钟请求数，0 = 不限');
    w.appendChild(rate);
    const save = mini('保存限流', 'primary');
    save.addEventListener('click', async () => {
      try{
        await apiPost('/api/ui/llm', { rate_limit: rate.value.trim() });
        note('限流已设为 ' + rate.value.trim() + ' 次/分（主/子 Agent 共用配额）');
      }catch (e){ note('保存失败：' + e.message, 'err'); }
    });
    w.appendChild(save);
    w.appendChild(el('div', 'dr-tip',
      '额度小的 Key 建议 3-7；0 表示不限速。429 时会按 Retry-After 自动退避。'));
  }catch (e){
    w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>';
  }
}
