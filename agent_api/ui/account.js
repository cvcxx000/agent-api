// 账号与安全：当前账号 / 改密码 / API 密钥 / 设备 / 退出 / Google 配置
async function showAccount(){
  const w = sheetWrap();
  w.innerHTML = '';
  let data = null;
  try{
    data = await apiGet('/api/auth/me');
  }catch (e){
    // 401 = 账号体系没开（或者根本没人登录）
    const box = el('div', 'desc', '这台设备还没启用账号体系，谁打开都能用。要加登录，就创建第一个账号：');
    w.appendChild(box);
    const go = mini('去创建账号');
    go.addEventListener('click', () => { location.href = '/login'; });
    w.appendChild(go);
    const hint = el('div', 'hint2', '建完之后再打开这里，就能管密码、Google 登录和 API 密钥。');
    w.appendChild(hint);
    return;
  }

  const me = data.me || {};
  const ident = data.identity || {};
  const viaText = { cookie: '网页登录', key: 'API 密钥', token: '全局 API Token' }[ident.via] || ident.via || '—';

  w.appendChild(el('div', 'sect', '当前账号'));
  const kv = el('div', 'kv');
  kv.appendChild(el('b', '', me.name || me.email || '—'));
  kv.appendChild(el('span', '', (me.email || '') + ' · ' + (me.role === 'owner' ? '主人（owner）' : '成员') + ' · ' + viaText));
  w.appendChild(kv);
  if (me.google){
    w.appendChild(el('div', 'hint2', 'Google 已绑定：' + (me.google_email || me.email)));
  }else{
    w.appendChild(el('div', 'hint2', '还没绑 Google。想用 Google 身份认证找回密码，先用「用 Google 登录」进一次。'));
  }

  // ---- 改密码 ----
  w.appendChild(el('div', 'sect', '改密码'));
  const oldPwd = inputEl('', '原密码（Google 建的账号可留空）');
  oldPwd.type = 'password';
  const newPwd = inputEl('', '新密码，至少 8 位');
  newPwd.type = 'password';
  w.appendChild(lab('原密码'));
  w.appendChild(oldPwd);
  w.appendChild(lab('新密码'));
  w.appendChild(newPwd);
  const savePwd = mini('改密码（其它设备会下线）');
  w.appendChild(savePwd);
  const pwdOut = el('div', 'out', '');
  w.appendChild(pwdOut);
  savePwd.addEventListener('click', async () => {
    pwdOut.textContent = '提交中…';
    try{
      const out = await apiPost('/api/auth/password', { old: oldPwd.value, new: newPwd.value });
      pwdOut.textContent = '改好了。新的恢复码：' + (out.recovery_code || '（没返回，去重置一次）');
      oldPwd.value = ''; newPwd.value = '';
    }catch (e){ pwdOut.textContent = e.message; }
  });

  // ---- API 密钥 ----
  w.appendChild(el('div', 'sect', 'API 密钥（给终端 / 脚本用）'));
  const keys = me.keys || [];
  if (!keys.length){
    w.appendChild(el('div', 'desc', '还没有密钥。生成一个，就能在 Termux 里用 --token ak_… 直接连。'));
  }
  keys.forEach(k => {
    const row = el('div', 'kv');
    row.appendChild(el('b', '', k.name + '  ' + (k.prefix || '')));
    row.appendChild(el('span', '', '创建 ' + (k.created || '') + (k.last_used ? (' · 最近 ' + k.last_used) : ' · 没用过')));
    const del = mini('吊销', 'danger');
    del.addEventListener('click', async () => {
      if (!confirm('吊销密钥「' + k.name + '」？用它的脚本马上失效。')) return;
      try{ await apiDelete('/api/auth/keys/' + k.id); showAccount(); }
      catch (e){ alert(e.message); }
    });
    row.appendChild(del);
    w.appendChild(row);
  });
  const keyName = inputEl('', '给这个密钥起个名，例如 Termux');
  w.appendChild(keyName);
  const makeKey = mini('生成新密钥');
  w.appendChild(makeKey);
  const keyOut = el('div', 'out', '');
  w.appendChild(keyOut);
  makeKey.addEventListener('click', async () => {
    keyOut.textContent = '生成中…';
    try{
      const out = await apiPost('/api/auth/keys', { name: keyName.value });
      keyOut.textContent = '只显示这一次，复制走：' + out.key;
    }catch (e){ keyOut.textContent = e.message; }
  });

  // ---- 设备 ----
  w.appendChild(el('div', 'sect', '登录中的设备'));
  try{
    const dev = await apiGet('/api/auth/devices');
    const list = dev.devices || [];
    if (!list.length) w.appendChild(el('div', 'desc', '没有（可能是用密钥/Token 进来的）。'));
    list.slice(0, 10).forEach(d => {
      const row = el('div', 'kv');
      row.appendChild(el('b', '', d.ip || '本机'));
      row.appendChild(el('span', '', (d.created || '') + ' · ' + String(d.ua || '').slice(0, 60)));
      w.appendChild(row);
    });
  }catch (e){ w.appendChild(el('div', 'desc', '读设备列表失败：' + e.message)); }

  const logout = mini('退出登录');
  logout.addEventListener('click', async () => {
    try{ await apiPost('/api/auth/logout', {}); }catch (e){}
    location.href = '/login';
  });
  const logoutAll = mini('退出所有设备', 'danger');
  logoutAll.addEventListener('click', async () => {
    if (!confirm('把所有设备都下线？包括当前浏览器。')) return;
    try{ await apiPost('/api/auth/logout-all', {}); }catch (e){ alert(e.message); return; }
    location.href = '/login';
  });
  w.appendChild(logout);
  w.appendChild(logoutAll);

  // ---- Google 配置（owner）----
  if (me.role === 'owner'){
    w.appendChild(el('div', 'sect', 'Google 登录（owner）'));
    const g = el('div', 'desc', '在 Google Cloud Console 建一个「Web 应用」OAuth 客户端，把重定向 URI 填成下面这个，再把 Client ID / Secret 粘进来。');
    w.appendChild(g);
    let cfg = {};
    try{ cfg = await apiGet('/api/auth/google/config'); }catch (e){ cfg = {}; }
    w.appendChild(el('div', 'hint2', '重定向 URI：' + (cfg.redirect_uri || '/api/auth/google/callback')));
    const cid = inputEl(cfg.client_id || '', 'Client ID（…apps.googleusercontent.com）');
    const csec = inputEl('', cfg.has_secret ? ('Client Secret（已保存，留空不改）') : 'Client Secret');
    csec.type = 'password';
    w.appendChild(lab('Client ID'));
    w.appendChild(cid);
    w.appendChild(lab('Client Secret'));
    w.appendChild(csec);
    const allow = document.createElement('input');
    allow.type = 'checkbox';
    allow.checked = !!cfg.allow_register;
    const allowRow = el('label', 'checkline');
    allowRow.appendChild(allow);
    allowRow.appendChild(el('span', '', '允许别人注册新账号（默认只允许你一个）'));
    w.appendChild(allowRow);
    const saveG = mini('保存 Google 配置');
    w.appendChild(saveG);
    const gOut = el('div', 'out', '');
    w.appendChild(gOut);
    saveG.addEventListener('click', async () => {
      gOut.textContent = '保存中…';
      const body = { client_id: cid.value.trim(), allow_register: allow.checked };
      if (csec.value.trim()) body.client_secret = csec.value.trim();
      try{
        const out = await apiPost('/api/auth/google/config', body);
        gOut.textContent = out.on ? '已开启 Google 登录，回 /login 就能看到按钮。' : '已保存，但 Client ID 或 Secret 还缺一个。';
      }catch (e){ gOut.textContent = e.message; }
    });
  }
}
