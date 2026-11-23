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
    const media = s.media || {};
    const search = s.search || {};
    const auth = s.auth || {};
    w.appendChild(el('div', 'sect', '账号与安全'));
    w.appendChild(settingRow('⛨', '账号与安全',
      auth.setup_needed ? '还没账号 · 谁打开都能用' :
        ((auth.users || 0) + ' 个账号 · 登录已启用' + (auth.google_on ? ' · Google 可用' : '')),
      () => showAccount()));
    w.appendChild(el('div', 'sect', '模型与服务'));
    w.appendChild(settingRow('☆', '默认模型和提示词',
      (s.model || '未配置') + (s.mock_llm ? '（Mock）' : '') + ' · 全局默认提示词',
      () => pushSheet('defaults', '默认模型和提示词', s.model, () => showDefaults())));
    w.appendChild(settingRow('◎', '提供商',
      '从目录添加 / 填 Key / 拉真实模型',
      () => pushSheet('providers', '提供商', '目录 / 拉模型 / 切换', () => showProviders())));
    w.appendChild(settingRow('⌕', '搜索服务',
      search.ready ? (search.name + (search.has_key ? ' · 已配 Key' : ' · 免 Key')) : '未配置 · 搜不了网',
      () => pushSheet('search', '搜索服务', 'Tavily / 博查 / Serper / DDG', () => showSearch())));
    w.appendChild(settingRow('▧', '图片生成',
      (media.image && media.image.ready)
        ? (media.image.model + (media.image.has_key ? ' · 有 Key' : ' · 没 Key'))
        : '未配置 · 生成图片用不了',
      () => pushSheet('media-image', '图片生成', 'OpenAI 形状 /images/generations', () => showMedia('image'))));
    w.appendChild(settingRow('▶', '视频生成',
      (media.video && media.video.ready)
        ? (media.video.model + (media.video.has_key ? ' · 有 Key' : ' · 没 Key'))
        : '未配置 · 生成视频用不了',
      () => pushSheet('media-video', '视频生成', '异步任务 + 轮询', () => showMedia('video'))));
    const tts = s.tts || {};
    w.appendChild(settingRow('♪', '语音合成',
      tts.configured
        ? ((tts.name || tts.preset || 'TTS') + ' · ' + (tts.voice || '默认音色') + (tts.has_key ? '' : ' · 没 Key'))
        : '未配置 · 说不了话',
      () => pushSheet('tts', '语音合成', 'TTS · /audio/speech', () => showTts())));
    w.appendChild(settingRow('◷', '定时任务',
      (s.schedules ? (s.schedules + ' 个任务在跑') : '还没有任务') + ' · cron 到点自动跑',
      () => pushSheet('schedules', '定时任务', 'cron 到点自动跑', () => showSchedules())));
    w.appendChild(settingRow('▤', 'Web 服务器',
      s.host + ':' + s.port + ' · ' + (s.auth_enabled ? '已开鉴权' : '无鉴权'),
      () => pushSheet('server', 'Web 服务器', '监听地址', () => showServer())));
    w.appendChild(el('div', 'sect', '数据设置'));
    w.appendChild(settingRow('▣', '数据备份',
      '会话 / Key / 提示词 / 搜索 / 媒体配置打成一个 zip',
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
    go.addEventListener('click', () => pushSheet('models', '选择模型', '只列真实存在的模型', () => showModelPicker()));
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

async function showSearch(){
  const w = sheetWrap();
  let data;
  try{ data = await apiGet('/api/ui/search'); }
  catch (e){ w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>'; return; }
  w.appendChild(el('div', 'sect', '当前'));
  if (data.ready){
    const kv = el('div', 'kv');
    kv.appendChild(el('b', '', data.name || data.provider));
    kv.appendChild(el('span', '', data.has_key ? ('Key …' + data.key_tail) : '免 Key'));
    w.appendChild(kv);
  } else {
    w.appendChild(el('div', 'desc', '未配置 · 下面任选一家，填 Key 后保存。'));
  }

  w.appendChild(el('div', 'sect', '选一家'));
  const rows = el('div', 'rows');
  (data.presets || []).forEach(p => {
    const row = el('button', 'row' + (p.id === data.provider ? ' now' : ''));
    row.appendChild(el('span', 'rico', p.name.slice(0, 1)));
    const body = el('span', 'rbody');
    body.appendChild(el('b', '', p.name));
    body.appendChild(el('i', '', p.notes));
    row.appendChild(body);
    row.appendChild(el('span', 'rgo', p.id === data.provider ? '✓' : '›'));
    row.addEventListener('click', () => {
      endpoint.value = p.endpoint || '';
      provider.id = p.id;
      provider.name = p.name;
      note('已选 ' + p.name + '，填 Key（免 Key 的直接保存）然后保存。');
    });
    rows.appendChild(row);
  });
  w.appendChild(rows);

  const provider = { id: data.provider, name: data.name };
  const endpoint = inputEl(data.endpoint || '', '端点地址（选上面的会自动填）');
  const key = document.createElement('input');
  key.type = 'password';
  key.placeholder = data.has_key ? ('已配 Key（尾号 ' + data.key_tail + '，留空不改）') : 'API Key（DDG 免 Key）';
  key.autocomplete = 'off';
  const count = inputEl(String(data.max_results || 5), '每次最多几条（1-20）');
  w.appendChild(lab('端点'));
  w.appendChild(endpoint);
  w.appendChild(lab('API Key'));
  w.appendChild(key);
  w.appendChild(lab('默认条数'));
  w.appendChild(count);

  const save = mini('保存', 'primary');
  save.addEventListener('click', async () => {
    try{
      await apiPost('/api/ui/search', {
        provider: provider.id, endpoint: endpoint.value.trim(),
        api_key: key.value.trim(), max_results: count.value.trim()
      });
      note('搜索配置已保存');
      pushSheet('search', '搜索服务', 'Tavily / 博查 / Serper / DDG', () => showSearch());
    }catch (e){ note('保存失败：' + e.message, 'err'); }
  });
  w.appendChild(save);

  w.appendChild(el('div', 'sect', '试一下'));
  const q = inputEl('', '例如 今天科技新闻');
  const go = mini('搜索');
  const out = el('div', 'out', '（结果显示在这里）');
  go.addEventListener('click', async () => {
    out.textContent = '搜索中…';
    try{
      const r = await apiPost('/api/ui/search/test', { query: q.value.trim(), count: 3 });
      const rowsData = r.results || [];
      out.textContent = rowsData.length
        ? rowsData.map((x, i) => (i + 1) + '. ' + (x.title || '') + '\n   ' + (x.url || '') + '\n   ' + (x.snippet || '')).join('\n')
        : '没有结果';
    }catch (e){ out.textContent = '失败：' + e.message; }
  });
  w.appendChild(q);
  w.appendChild(go);
  w.appendChild(out);
  w.appendChild(el('div', 'dr-tip', 'Agent 的 web_search 工具就走这里。要读全文，它会再对具体链接调用 fetch_url。'));
}

async function showMedia(kind){
  const w = sheetWrap();
  let data;
  try{ data = await apiGet('/api/ui/media'); }
  catch (e){ w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>'; return; }
  const cfg = data[kind] || {};
  const isImage = kind === 'image';
  w.appendChild(el('div', 'sect', '当前'));
  if (cfg.ready){
    const kv = el('div', 'kv');
    kv.appendChild(el('b', '', cfg.name || cfg.preset || '已配置'));
    kv.appendChild(el('span', '', cfg.model + ' · ' + cfg.mode));
    w.appendChild(kv);
  } else {
    w.appendChild(el('div', 'desc', '未配置 · 下面任选一家（或自定义中转站），填 Key 后保存。'));
  }

  w.appendChild(el('div', 'sect', '选一家'));
  const rows = el('div', 'rows');
  (cfg.presets || []).forEach(p => {
    const row = el('button', 'row' + (p.id === cfg.preset ? ' now' : ''));
    row.appendChild(el('span', 'rico', String(p.name).slice(0, 1)));
    const body = el('span', 'rbody');
    body.appendChild(el('b', '', p.name));
    body.appendChild(el('i', '', p.notes));
    row.appendChild(body);
    row.appendChild(el('span', 'rgo', p.id === cfg.preset ? '✓' : '›'));
    row.addEventListener('click', () => {
      preset.id = p.id;
      base.value = p.base_url || '';
      model.value = p.model || '';
      if (isImage){ /* size 保持 */ } else if (p.mode){ mode.value = p.mode; }
      note('已选 ' + p.name + '，填 Key 再保存。');
    });
    rows.appendChild(row);
  });
  w.appendChild(rows);

  const preset = { id: cfg.preset || '' };
  const base = inputEl(cfg.base_url || '', 'BASE URL，例如 https://api.siliconflow.cn/v1');
  const model = inputEl(cfg.model || '', isImage ? '模型，例如 Kwai-Kolors/Kolors' : '模型，例如 Wan-AI/Wan2.1-T2V-14B');
  const key = document.createElement('input');
  key.type = 'password';
  key.placeholder = cfg.has_key ? ('已配 Key（尾号 ' + cfg.key_tail + '，留空不改）') : 'API Key';
  key.autocomplete = 'off';
  let mode = null;
  if (!isImage){
    mode = document.createElement('select');
    [['openai_video', 'OpenAI 形状（/videos + 轮询）'], ['siliconflow_video', '硅基流动（/video/submit + /video/status）'],
     ['generic_video', '通用异步（/videos/generations + task_id）']].forEach(([v, t]) => {
      const o = document.createElement('option');
      o.value = v;
      o.textContent = t;
      if (cfg.mode === v){ o.selected = true; }
      mode.appendChild(o);
    });
  }
  const size = inputEl(cfg.size || (isImage ? '1024x1024' : '720x1280'), isImage ? '尺寸 1024x1024' : '尺寸 720x1280');
  const seconds = isImage ? null : inputEl(String(cfg.seconds || 5), '时长秒数 1-60');

  w.appendChild(lab('BASE URL'));
  w.appendChild(base);
  w.appendChild(lab('模型'));
  w.appendChild(model);
  w.appendChild(lab('API Key'));
  w.appendChild(key);
  if (mode){ w.appendChild(lab('接口形状')); w.appendChild(mode); }
  w.appendChild(lab(isImage ? '尺寸' : '尺寸 / 时长'));
  const sr = el('div', 'row2');
  sr.appendChild(size);
  if (seconds){ sr.appendChild(seconds); }
  w.appendChild(sr);

  const save = mini('保存', 'primary');
  save.addEventListener('click', async () => {
    const body = {
      preset: preset.id, base_url: base.value.trim(), model: model.value.trim(),
      size: size.value.trim()
    };
    if (key.value.trim()){ body.api_key = key.value.trim(); }
    if (mode){ body.mode = mode.value; }
    if (seconds){ body.seconds = seconds.value.trim(); }
    try{
      await apiPost('/api/ui/media/' + kind, body);
      note((isImage ? '图片' : '视频') + '生成配置已保存');
      pushSheet('media-' + kind, isImage ? '图片生成' : '视频生成',
        isImage ? 'OpenAI 形状 /images/generations' : '异步任务 + 轮询', () => showMedia(kind));
    }catch (e){ note('保存失败：' + e.message, 'err'); }
  });
  w.appendChild(save);

  w.appendChild(el('div', 'sect', '试一下'));
  const p = inputEl('', isImage ? '例如 一只橘猫蹲在窗台上，逆光' : '例如 海边日落，镜头缓慢推进');
  const go = mini(isImage ? '生成图片' : '生成视频（可能等几分钟）', 'primary');
  const out = el('div', 'out', '（结果在这里）');
  go.addEventListener('click', async () => {
    const prompt = p.value.trim();
    if (!prompt){ out.textContent = '先写一句提示词'; return; }
    out.textContent = isImage ? '生成中…' : '已提交，等待出片…';
    try{
      const r = await apiPost('/api/ui/media/' + kind + '/generate', { prompt: prompt, size: size.value.trim() });
      const res = r.result || {};
      out.textContent = '已生成：' + (res.path || '') + '（' + Math.round((res.bytes || 0) / 1024) + ' KB）模型 ' + (res.model || '');
      if (isImage){
        const img = document.createElement('img');
        img.className = 'md-img';
        img.src = res.url;
        out.appendChild(img);
      } else {
        const video = document.createElement('video');
        video.className = 'md-video';
        video.controls = true;
        video.playsInline = true;
        video.src = res.url;
        out.appendChild(video);
      }
    }catch (e){ out.textContent = '失败：' + e.message; }
  });
  w.appendChild(p);
  w.appendChild(go);
  w.appendChild(out);
  w.appendChild(el('div', 'dr-tip',
    isImage
      ? '走的是 OpenAI 形状 POST /images/generations。有的中转站不认 response_format，会自动去掉再试一次。'
      : '视频是异步的：提交 → 每 3-4 秒查一次 → 完成后下载到 workspace/media/。超时可调大 timeout。'));
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
      '把 data/ 打成 zip：所有会话（含工具输出）、providers.json（含各家 Key）、default_system.md、search.json、media.json。'));
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
