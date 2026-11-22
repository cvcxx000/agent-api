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
  return el('button', 'mini2' + (cls ? ' ' + cls : ''), text);
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
    if (sheetMode === 'models'){ showModelPicker(); }
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

async function pullModels(pid, silent){
  try{
    const r = await apiPost('/api/providers/' + encodeURIComponent(pid) + '/fetch-models', {});
    const f = r.fetched || {};
    if (!silent){ note('拉到 ' + (f.count || 0) + ' 个模型'); }
    refreshProviderSummary();
    return f;
  }catch (e){
    if (!silent){ note('拉模型失败：' + e.message, 'err'); }
    throw e;
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
  chips.appendChild(el('span', 'chip' + (p.enabled ? '' : ' off'), p.enabled ? '启用' : '停用'));
  chips.appendChild(el('span', 'chip model', String(p.model_count) + ' 个模型'));
  if (p.is_active){ chips.appendChild(el('span', 'chip active', '当前')); }
  if (!p.has_key){ chips.appendChild(el('span', 'chip off', '无 Key')); }
  if (p.vision){ chips.appendChild(el('span', 'chip model', '看图')); }
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
  const pull = mini('拉取模型');
  pull.addEventListener('click', async () => {
    pull.textContent = '拉取中…';
    try{ await pullModels(p.id); showProviders(); }
    catch (e){ pull.textContent = '拉取模型'; }
  });
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
  menu.appendChild(pull);
  menu.appendChild(tst);
  menu.appendChild(del);
  card.appendChild(menu);

  const detail = el('div', 'pdetail');
  card.appendChild(detail);

  function runTest(){
    const model = p.active_model || (p.models.length ? p.models[0] : '');
    if (!model){ say(detail, '先拉模型或加一个模型再测。'); return; }
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

    const visionRow = el('div', 'row2');
    const visionLabel = el('span', 'dr-tip', '这个模型能读图片（多模态）：');
    const visionBtn = mini(p.vision ? '已开 · 点此关闭' : '未开 · 点此开启', p.vision ? 'primary' : '');
    visionBtn.addEventListener('click', async () => {
      try{
        await apiPost('/api/providers/' + encodeURIComponent(p.id) + '/vision', { vision: !p.vision });
        note((p.vision ? '已关闭' : '已开启') + '图片输入');
        showProviders();
      }catch (e){ note('设置失败：' + e.message, 'err'); }
    });
    visionRow.appendChild(visionLabel);
    visionRow.appendChild(visionBtn);
    detail.appendChild(visionRow);

    detail.appendChild(lab('模型（点「用这个」切到它，切换后立刻生效）'));
    const models = el('div', 'models');
    if (!p.models.length){
      models.appendChild(el('div', 'dr-tip', '还没有模型。填好 Key 后点下面的「从服务商拉取」，拿真实列表。'));
    }
    p.models.forEach(m => {
      const row = el('div', 'mrow' + (p.is_active && p.active_model === m ? ' now' : ''));
      row.appendChild(el('span', '', m));
      if (p.is_active && p.active_model === m){
        row.appendChild(el('span', 'chip active', '当前'));
      }
      const use = el('button', '', '用这个');
      use.addEventListener('click', () => activateProvider(p.id, m));
      const delModel = el('button', 'del', '✕');
      delModel.addEventListener('click', async () => {
        try{
          await apiDelete('/api/providers/' + encodeURIComponent(p.id) + '/models/' + encodeURIComponent(m));
          showProviders();
          refreshProviderSummary();
        }catch (e){ note('删除模型失败：' + e.message, 'err'); }
      });
      row.appendChild(use);
      row.appendChild(delModel);
      models.appendChild(row);
    });
    detail.appendChild(models);

    const pullBtn = mini('从服务商拉取模型', 'primary');
    pullBtn.addEventListener('click', async () => {
      pullBtn.textContent = '拉取中…';
      try{
        const f = await pullModels(p.id);
        note('拉到 ' + (f.count || 0) + ' 个模型' + (f.count ? '，点「用这个」切换' : ''));
        showProviders();
      }catch (e){ pullBtn.textContent = '从服务商拉取模型'; }
    });
    detail.appendChild(pullBtn);

    const addRow = el('div', 'row2');
    const addInp = inputEl('', '或手填模型 ID，例如 deepseek-chat');
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
    testBtn.addEventListener('click', runTest);
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

    const emptyCount = providersCache.filter(p => !p.has_key && !p.is_active).length;
    if (emptyCount){
      const prune = mini('清理 ' + emptyCount + ' 个没 Key 的条目', 'danger');
      prune.addEventListener('click', async () => {
        try{
          const r = await apiPost('/api/providers/prune', {});
          note('已清理 ' + (r.count || 0) + ' 个空壳');
          showProviders();
          refreshProviderSummary();
        }catch (e){ note('清理失败：' + e.message, 'err'); }
      });
      w.appendChild(prune);
    }

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
      if (!arr.length){ listBox.appendChild(el('div', 'dr-tip', '还没有提供商。点下面「从目录添加」挑一家。')); }
      arr.forEach(p => listBox.appendChild(providerCard(p)));
    };
    fill();
    search.addEventListener('input', fill);

    const addBox = el('div', 'panel');
    addBox.appendChild(el('h3', '', '添加提供商'));
    addBox.appendChild(el('div', 'meta', '从目录挑一家（只显示模板，不会凭空塞假模型），或自定义中转站'));
    const catalogBtn = mini('从目录添加', 'primary');
    catalogBtn.addEventListener('click', () => pushSheet('catalog', '添加提供商', '选一家预置', () => showCatalog()));
    addBox.appendChild(catalogBtn);
    const customBtn = mini('自定义');
    customBtn.addEventListener('click', () => pushSheet('addpreset', '自定义提供商', '任意 OpenAI 兼容', () => showAddPreset(null)));
    addBox.appendChild(customBtn);
    w.appendChild(addBox);
  }catch (e){
    w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>';
  }
}

async function showCatalog(){
  const w = sheetWrap();
  try{
    const j = await apiGet('/api/providers/catalog');
    const list = j.providers || [];
    const search = document.createElement('input');
    search.type = 'search';
    search.className = 'psrch';
    search.placeholder = '搜索目录（deepseek / 硅基 / ollama…）';
    w.appendChild(search);
    const rows = el('div', 'rows');
    w.appendChild(rows);
    const fill = () => {
      rows.innerHTML = '';
      list.filter(it => !q0(search.value, it)).forEach(it => {
        const row = el('button', 'row');
        row.appendChild(el('span', 'rico', it.name.slice(0, 1)));
        const body = el('span', 'rbody');
        body.appendChild(el('b', '', it.name + (it.keyless ? ' · 免 Key' : '')));
        body.appendChild(el('i', '', it.notes || it.base_url));
        row.appendChild(body);
        row.appendChild(el('span', 'rgo', '›'));
        row.addEventListener('click', () => pushSheet('addpreset', it.name, it.base_url, () => showAddPreset(it)));
        rows.appendChild(row);
      });
      if (!rows.children.length){ rows.appendChild(el('div', 'dr-tip', '没有匹配的预置。')); }
    };
    function q0(raw, it){
      const q = String(raw || '').trim().toLowerCase();
      if (!q){ return false; }
      const hay = (it.name + ' ' + it.id + ' ' + it.base_url + ' ' + (it.notes || '')).toLowerCase();
      return hay.indexOf(q) < 0;
    }
    fill();
    search.addEventListener('input', fill);
    w.appendChild(el('div', 'dr-tip', '目录只是模板：添加后要填 Key，再点「拉取模型」拿真实模型列表。'));
  }catch (e){
    w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>';
  }
}

function showAddPreset(preset){
  const w = sheetWrap();
  const box = el('div', 'panel');
  box.appendChild(el('h3', '', preset ? preset.name : '自定义提供商'));
  box.appendChild(el('div', 'meta', preset ? (preset.notes || preset.base_url) : '任何 OpenAI 兼容接口：中转站、vLLM、Ollama'));
  if (preset && preset.key_url){
    box.appendChild(el('div', 'desc', '取 Key：' + preset.key_url));
  }
  const name = inputEl(preset ? preset.name : '', '名称，例如 我的中转');
  const base = inputEl(preset ? preset.base_url : '', 'BASE URL，例如 https://api.xxx.com/v1');
  const key = document.createElement('input');
  key.type = 'password';
  key.placeholder = preset && preset.keyless ? '不用填 Key' : 'API Key（sk-...，可先留空）';
  key.autocomplete = 'off';
  const model = inputEl('', '模型 ID（可留空，添加后拉取）');
  if (preset && preset.model_hint){ model.placeholder = '模型 ID，例如 ' + preset.model_hint; }
  box.appendChild(lab('名称'));
  box.appendChild(name);
  box.appendChild(lab('BASE URL'));
  box.appendChild(base);
  box.appendChild(lab('API Key'));
  box.appendChild(key);
  box.appendChild(lab('模型（留空也能先加，稍后拉取）'));
  box.appendChild(model);

  const go = mini('添加并拉取模型', 'primary');
  go.addEventListener('click', async () => {
    if (!name.value.trim() || !base.value.trim()){ say(box, '名称和 BASE URL 都要填。'); return; }
    go.textContent = '添加中…';
    go.disabled = true;
    try{
      const body = {
        preset: preset ? preset.id : '',
        name: name.value.trim(),
        base_url: base.value.trim(),
      };
      if (key.value.trim()){ body.api_key = key.value.trim(); }
      if (model.value.trim()){ body.model = model.value.trim(); }
      if (!preset){
        const saved = await apiPost('/api/providers', body);
        note('已添加提供商：' + body.name + '（去详情里填 Key、拉模型）');
        refreshProviderSummary();
        pushSheet('providers', '提供商', '目录 / 拉模型 / 切换', () => showProviders());
        return;
      }
      const added = await apiPost('/api/providers/add', body);
      const pid = added.added;
      note('已添加 ' + body.name);
      if (key.value.trim() && pid){
        try{
          const f = await pullModels(pid, true);
          if (f && f.count){
            await apiPost('/api/providers/' + encodeURIComponent(pid) + '/activate', { model: f.models[0] });
            note('已拉取 ' + f.count + ' 个模型，当前用 ' + f.models[0]);
          } else {
            note('服务商没返回模型，可以去详情里手填一个。');
          }
        }catch (e){
          note('这家拉了模型但失败了：' + e.message + '（Key 或 BASE URL 可能不对）', 'err');
        }
      } else {
        note('先留着，填好 Key 再拉模型。');
      }
      refreshStatus();
      refreshProviderSummary();
      pushSheet('providers', '提供商', '目录 / 拉模型 / 切换', () => showProviders());
    }catch (e){
      say(box, '失败：' + e.message);
      go.textContent = '添加并拉取模型';
    }finally{
      go.disabled = false;
    }
  });
  box.appendChild(go);
  w.appendChild(box);
}

async function showModelPicker(){
  const w = sheetWrap();
  try{
    const j = await apiGet('/api/providers');
    const list = (j.providers || []).filter(p => p.models && p.models.length);
    w.appendChild(el('div', 'dr-tip',
      '当前：' + ((j.current && j.current.model) || '未配置') + '。只列你真实添加过的模型，点一行就切。'));
    if (!list.length){
      w.appendChild(el('div', 'desc', '还没有任何模型。先去「提供商」挑一家，填 Key，点拉取模型。'));
      const go = mini('去添加提供商', 'primary');
      go.addEventListener('click', () => pushSheet('providers', '提供商', '目录 / 拉模型 / 切换', () => showProviders()));
      w.appendChild(go);
      return;
    }
    list.forEach(p => {
      w.appendChild(el('div', 'sect', p.name + (p.has_key ? '' : '（没 Key，切过去会进 Mock）')));
      const rows = el('div', 'rows');
      p.models.forEach(m => {
        const row = el('button', 'row' + (p.is_active && p.active_model === m ? ' now' : ''));
        row.appendChild(el('span', 'rico', m.slice(0, 2)));
        const body = el('span', 'rbody');
        body.appendChild(el('b', '', m));
        body.appendChild(el('i', '', p.base_url));
        row.appendChild(body);
        row.appendChild(el('span', 'rgo', p.is_active && p.active_model === m ? '✓' : '›'));
        row.addEventListener('click', () => { activateProvider(p.id, m); closeSheet(); });
        rows.appendChild(row);
      });
      w.appendChild(rows);
    });
  }catch (e){
    w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>';
  }
}
