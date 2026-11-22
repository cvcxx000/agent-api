function el(tag, cls, text){
  const d = document.createElement(tag);
  if (cls){ d.className = cls; }
  if (text !== undefined){ d.textContent = text; }
  return d;
}
function lab(text){ return el('div', 'lab', text); }
function inputEl(value, placeholder){
  const i = document.createElement('input');
  if (value){ i.value = value; }
  if (placeholder){ i.placeholder = placeholder; }
  i.autocomplete = 'off';
  return i;
}
function mini(text, cls){
  const b = el('button', 'mini2' + (cls ? ' ' + cls : ''), text);
  return b;
}
function say(box, text){
  const d = el('div', 'out', text);
  box.appendChild(d);
  return d;
}

async function activateProvider(pid, model){
  try{
    const r = await apiPost('/api/providers/' + encodeURIComponent(pid) + '/activate', { model: model || '' });
    note('已切到 ' + r.model + (r.mock_llm ? '（这家没 Key，进 Mock）' : ' @ ' + r.base_url));
    refreshStatus();
    refreshProviderSummary();
    if (sheetMode === 'providers'){ showProviders(); }
  }catch (e){
    note('切换失败：' + e.message, 'err');
  }
}

async function providerAction(pid, action, body){
  try{
    await apiPost('/api/providers/' + encodeURIComponent(pid) + '/' + action, body || {});
    showProviders();
    refreshProviderSummary();
  }catch (e){
    note('操作失败：' + e.message, 'err');
  }
}

function providerCard(p){
  const card = el('div', 'prov');
  const main = el('div', 'prov-main');
  const ico = el('div', 'pico', String(p.name || '?').slice(0, 1).toUpperCase());
  const body = el('div', 'pbody');
  const nm = el('b', '', p.name);
  const nt = el('i', '', p.notes || p.base_url);
  const chips = el('div', 'chips');
  const on = el('span', 'chip' + (p.enabled ? '' : ' off'), p.enabled ? '启用' : '停用');
  const cnt = el('span', 'chip model', String(p.model_count) + ' 个模型');
  chips.appendChild(on);
  chips.appendChild(cnt);
  if (p.is_active){ chips.appendChild(el('span', 'chip active', '当前')); }
  if (!p.has_key){ chips.appendChild(el('span', 'chip off', '无 Key')); }
  body.appendChild(nm);
  body.appendChild(nt);
  body.appendChild(chips);
  const dot = el('button', 'dotbtn', '⋮');
  main.appendChild(ico);
  main.appendChild(body);
  main.appendChild(dot);
  card.appendChild(main);

  const menu = el('div', 'pmenu');
  const tg = mini(p.enabled ? '停用' : '启用');
  tg.addEventListener('click', () => providerAction(p.id, 'toggle', { enabled: !p.enabled }));
  const tst = mini('测一句');
  tst.addEventListener('click', async () => {
    menu.classList.remove('on');
    detail.classList.add('on');
    fillDetail();
    runTest();
  });
  const del = mini('删除', 'danger');
  del.addEventListener('click', async () => {
    if (!confirm('删除提供商 ' + p.name + '？Key 和模型列表都会没。')){ return; }
    try{
      const resp = await fetch('/api/providers/' + encodeURIComponent(p.id), { method: 'DELETE', headers: headers() });
      if (!resp.ok){ throw new Error('HTTP ' + resp.status); }
      showProviders();
      refreshProviderSummary();
    }catch (e){ note('删除失败：' + e.message, 'err'); }
  });
  menu.appendChild(tg);
  menu.appendChild(tst);
  menu.appendChild(del);
  card.appendChild(menu);

  const detail = el('div', 'pdetail');
  card.appendChild(detail);

  function runTest(){
    const model = p.models.length ? p.models[0] : '';
    if (!model){ say(detail, '先加一个模型再测。'); return; }
    const got = say(detail, '测试 ' + model + ' …');
    apiPost('/api/providers/' + encodeURIComponent(p.id) + '/test', { model: model })
      .then(r => { got.textContent = 'OK ' + r.model + ' → ' + (r.reply || '(空回复)'); })
      .catch(e => { got.textContent = '失败：' + e.message; });
  }

  function fillDetail(){
    if (detail.getAttribute('data-ready')){ return; }
    detail.setAttribute('data-ready', '1');
    detail.innerHTML = '';
    detail.appendChild(lab('BASE URL（填到 /v1 为止，不用带 /chat/completions）'));
    const base = inputEl(p.base_url, 'https://api.example.com/v1');
    detail.appendChild(base);
    detail.appendChild(lab(p.has_key ? 'API Key（已配置 ' + p.key_tail + '，留空就不改）' : 'API Key（还没填）'));
    const key = document.createElement('input');
    key.type = 'password';
    key.placeholder = 'sk-...';
    key.autocomplete = 'off';
    detail.appendChild(key);

    const save = mini('保存', 'primary');
    save.addEventListener('click', async () => {
      const body = { id: p.id, base_url: base.value.trim() };
      if (key.value.trim()){ body.api_key = key.value.trim(); }
      try{
        await apiPost('/api/providers', body);
        note('已保存 ' + p.name);
        showProviders();
        refreshProviderSummary();
      }catch (e){ note('保存失败：' + e.message, 'err'); }
    });
    const rmKey = mini('清除 Key', 'danger');
    rmKey.addEventListener('click', async () => {
      try{
        await apiPost('/api/providers', { id: p.id, clear_key: true });
        note('已清除 ' + p.name + ' 的 Key');
        showProviders();
      }catch (e){ note('清除失败：' + e.message, 'err'); }
    });
    detail.appendChild(save);
    detail.appendChild(rmKey);

    detail.appendChild(lab('模型（点「用这个」切到它，切换后立刻生效）'));
    const models = el('div', 'models');
    if (!p.models.length){ models.appendChild(el('div', 'dr-tip', '还没有模型，下面加一个。')); }
    p.models.forEach(m => {
      const row = el('div', 'mrow');
      row.appendChild(el('span', '', m));
      const use = el('button', '', '用这个');
      use.addEventListener('click', () => activateProvider(p.id, m));
      const delModel = el('button', 'del', '✕');
      delModel.addEventListener('click', async () => {
        try{
          const resp = await fetch('/api/providers/' + encodeURIComponent(p.id) + '/models/' + encodeURIComponent(m),
            { method: 'DELETE', headers: headers() });
          if (!resp.ok){ throw new Error('HTTP ' + resp.status); }
          showProviders();
          refreshProviderSummary();
        }catch (e){ note('删除模型失败：' + e.message, 'err'); }
      });
      row.appendChild(use);
      row.appendChild(delModel);
      models.appendChild(row);
    });
    detail.appendChild(models);

    const addRow = el('div', 'row2');
    const addInp = inputEl('', '新模型 ID，例如 deepseek-chat');
    const addBtn = mini('添加模型');
    addBtn.addEventListener('click', async () => {
      const m = addInp.value.trim();
      if (!m){ return; }
      try{
        await apiPost('/api/providers/' + encodeURIComponent(p.id) + '/models', { model: m });
        showProviders();
        refreshProviderSummary();
      }catch (e){
        note('添加失败：' + e.message, 'err');
      }
    });
    addRow.appendChild(addInp);
    addRow.appendChild(addBtn);
    detail.appendChild(addRow);

    const testBtn = mini('测试这家');
    testBtn.addEventListener('click', () => {
      const model = p.models.length ? p.models[0] : '';
      if (!model){ say(detail, '先加模型再测。'); return; }
      const got = say(detail, '测试 ' + model + ' …');
      apiPost('/api/providers/' + encodeURIComponent(p.id) + '/test', { model: model })
        .then(r => { got.textContent = 'OK ' + r.model + ' → ' + (r.reply || '(空回复)'); })
        .catch(e => { got.textContent = '失败：' + e.message; });
    });
    detail.appendChild(testBtn);
  }

  body.addEventListener('click', () => {
    detail.classList.toggle('on');
    if (detail.classList.contains('on')){ fillDetail(); }
  });
  dot.addEventListener('click', () => {
    menu.classList.toggle('on');
  });
  return card;
}

async function showProviders(){
  const w = sheetWrap();
  try{
    const j = await apiGet('/api/providers');
    providersCache = j.providers || [];
    const now = el('div', 'dr-tip',
      '当前：' + ((j.current && j.current.model) || '未配置') + '  ' + ((j.current && j.current.base_url) || ''));
    w.appendChild(now);

    const search = document.createElement('input');
    search.type = 'search';
    search.className = 'psrch';
    search.placeholder = '搜索提供商';
    search.autocomplete = 'off';
    w.appendChild(search);

    const listBox = el('div', 'plist');
    w.appendChild(listBox);
    const fill = () => {
      listBox.innerHTML = '';
      const q = search.value.trim().toLowerCase();
      const arr = providersCache.filter(p => {
        if (!q){ return true; }
        const hay = (p.name + ' ' + p.id + ' ' + (p.notes || '') + ' ' + (p.base_url || '')).toLowerCase();
        return hay.indexOf(q) >= 0;
      });
      if (!arr.length){ listBox.appendChild(el('div', 'dr-tip', '没有匹配的提供商。')); }
      arr.forEach(p => listBox.appendChild(providerCard(p)));
    };
    fill();
    search.addEventListener('input', fill);
    w.appendChild(newProviderPanel());
  }catch (e){
    w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>';
  }
}

function newProviderPanel(){
  const box = el('div', 'panel');
  box.appendChild(el('h3', '', '添加提供商'));
  box.appendChild(el('div', 'meta', '自定义 OpenAI 兼容接口，例如中转站、vLLM、Ollama'));
  const name = inputEl('', '名称，例如 我的中转');
  const base = inputEl('', 'BASE URL，例如 https://api.xxx.com/v1');
  const key = document.createElement('input');
  key.type = 'password';
  key.placeholder = 'API Key（可留空，之后在详情里填）';
  key.autocomplete = 'off';
  box.appendChild(name);
  box.appendChild(base);
  box.appendChild(key);
  const btn = mini('保存', 'primary');
  btn.addEventListener('click', async () => {
    if (!name.value.trim() || !base.value.trim()){ say(box, '名称和 BASE URL 都要填。'); return; }
    const body = { name: name.value.trim(), base_url: base.value.trim() };
    if (key.value.trim()){ body.api_key = key.value.trim(); }
    try{
      await apiPost('/api/providers', body);
      showProviders();
      refreshProviderSummary();
      note('已添加提供商：' + body.name);
    }catch (e){ say(box, '失败：' + e.message); }
  });
  box.appendChild(btn);
  return box;
}
