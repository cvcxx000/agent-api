// 语音合成（TTS）：设置页卡片 + 试朗读
async function showTts(){
  const w = sheetWrap();
  let data;
  try{ data = await apiGet('/api/ui/tts'); }
  catch (e){ w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>'; return; }

  w.appendChild(el('div', 'sect', '当前'));
  if (data.configured){
    const kv = el('div', 'kv');
    kv.appendChild(el('b', '', data.name || data.preset || '已配置'));
    kv.appendChild(el('span', '', data.model + ' · 音色 ' + (data.voice || '默认') +
      (data.has_key ? ' · 有 Key' : ' · 没 Key')));
    w.appendChild(kv);
  } else {
    w.appendChild(el('div', 'desc', '未配置 · 选一家预置（或自定义中转站），填 Key 和模型后保存。'));
  }

  w.appendChild(el('div', 'sect', '选一家'));
  const rows = el('div', 'rows');
  (data.presets || []).forEach(p => {
    const row = el('button', 'row' + (p.id === data.preset ? ' now' : ''));
    row.appendChild(el('span', 'rico', String(p.name).slice(0, 1)));
    const body = el('span', 'rbody');
    body.appendChild(el('b', '', p.name));
    body.appendChild(el('i', '', p.note || p.base_url || ''));
    row.appendChild(body);
    row.appendChild(el('span', 'rgo', p.id === data.preset ? '✓' : '›'));
    row.addEventListener('click', () => {
      preset.id = p.id;
      base.value = p.base_url || '';
      model.value = p.model || '';
      voice.value = p.voice || '';
      note('已选 ' + p.name + '，填 Key 再保存。');
    });
    rows.appendChild(row);
  });
  w.appendChild(rows);

  const preset = { id: data.preset || '' };
  const base = inputEl(data.base_url || '', 'BASE URL，例如 https://api.openai.com/v1');
  const model = inputEl(data.model || '', '模型，例如 gpt-4o-mini-tts');
  const voice = inputEl(data.voice || '', '音色，例如 alloy（留空用服务端默认）');
  const key = document.createElement('input');
  key.type = 'password';
  key.placeholder = data.has_key ? ('已配 Key（尾号 ' + data.key_tail + '，留空不改）') : 'API Key';
  key.autocomplete = 'off';
  const speed = inputEl(String(data.speed || 1), '语速 0.25 ~ 4.0');
  const fmt = document.createElement('select');
  ['mp3', 'wav', 'opus', 'aac', 'flac'].forEach(v => {
    const o = document.createElement('option');
    o.value = v; o.textContent = v;
    if (data.format === v){ o.selected = true; }
    fmt.appendChild(o);
  });

  w.appendChild(lab('BASE URL'));
  w.appendChild(base);
  w.appendChild(lab('模型'));
  w.appendChild(model);
  w.appendChild(lab('音色'));
  w.appendChild(voice);
  w.appendChild(lab('API Key'));
  w.appendChild(key);
  w.appendChild(lab('语速（1 是正常）'));
  w.appendChild(speed);
  w.appendChild(lab('输出格式'));
  w.appendChild(fmt);

  const btn = document.createElement('button');
  btn.className = 'btn primary';
  btn.textContent = '保存';
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try{
      const body = { preset: preset.id, base_url: base.value.trim(), model: model.value.trim(),
        voice: voice.value.trim(), format: fmt.value, speed: speed.value.trim(),
        api_key: key.value.trim() };
      const r = await apiPost('/api/ui/tts', body);
      note('已保存：' + (r.configured ? '可以朗读了' : '还差点配置（检查 BASE URL 和模型）'));
      pushSheet('tts', '语音合成', 'TTS · /audio/speech', () => showTts());
    }catch(e){ note('保存失败：' + e.message); }
    finally{ btn.disabled = false; }
  });
  w.appendChild(btn);

  w.appendChild(el('div', 'sect', '试一下'));
  const sample = inputEl('你好，我是你的 Agent。这段语音是用来验证 TTS 配好没配好。', '输入要朗读的话');
  sample.tagName = 'TEXTAREA';
  w.appendChild(sample);
  const play = document.createElement('button');
  play.className = 'btn';
  play.textContent = '试朗读';
  const holder = el('div', 'tts-row');
  play.addEventListener('click', async () => {
    play.disabled = true;
    holder.innerHTML = '';
    try{
      const r = await apiPost('/api/ui/tts/speak', { text: sample.value || '' });
      const box = el('div', 'desc', '已合成 ' + r.bytes + ' 字节 · ' + r.model + ' · 音色 ' + (r.voice || '默认'));
      w.appendChild(box);
      const audio = document.createElement('audio');
      audio.controls = true;
      audio.src = r.url;
      holder.innerHTML = '';
      holder.appendChild(audio);
      if (data.autoplay){ audio.play().catch(() => {}); }
    }catch(e){
      holder.innerHTML = '<div class="err">合成失败：' + esc(e.message) + '</div>';
    }finally{ play.disabled = false; }
  });
  holder.appendChild(play);
  w.appendChild(holder);
}
