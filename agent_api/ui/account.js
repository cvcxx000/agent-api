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


  if (me.guest){
    w.appendChild(el('div', 'sect', '当前账号'));
    const kv = el('div', 'kv');
    kv.appendChild(el('b', '', '游客（临时匿名）'));
    kv.appendChild(el('span', '', '到期 ' + (me.guest_expires_at || '—') + ' · ' + viaText));
    w.appendChild(kv);
    w.appendChild(el('div', 'hint2', '只活在这一个 ID 里，不能升级成正式账号；数据到期自动清空。'));
    const quota = el('div', 'out', '正在算空间…');
    w.appendChild(quota);
    apiGet('/api/auth/guest/state').then(st => {
      quota.textContent = '独立空间 ' + st.used_mb + ' / ' + st.quota_mb + ' MB（' + st.percent + '%）';
    }).catch(e => { quota.textContent = e.message; });

    w.appendChild(el('div', 'sect', 'Python / 终端'));
    if (me.guest_unlocked){
      w.appendChild(el('div', 'desc', '已解锁：可以用 Python 和终端，但仍然只能动你自己的目录。'));
    }else{
      w.appendChild(el('div', 'desc', '游客默认不能用 Python / 终端。输入开游客时给的解锁码（也可以在终端 /unlock）后开放。'));
      const code = inputEl('', '解锁码，例如 ABCD-EFGH');
      w.appendChild(code);
      const unlock = mini('解锁 Python / 终端');
      w.appendChild(unlock);
      const out = el('div', 'out', '');
      w.appendChild(out);
      unlock.addEventListener('click', async () => {
        out.textContent = '提交中…';
        try{
          await apiPost('/api/auth/guest/unlock', { code: code.value.trim() });
          out.textContent = '解锁成功，只能用在你自己的目录里。';
          me.guest_unlocked = true;
        }catch (e){ out.textContent = e.message; }
      });
    }

    w.appendChild(el('div', 'sect', '游客限制'));
    w.appendChild(el('div', 'desc', '每分钟 1 条消息 · 100MB 独立空间 · 不能派子 Agent / 插件 / 定时任务 / Cookie · 不能改任何全局设置。'));
    const logout = mini('退出游客登录', 'danger');
    logout.addEventListener('click', async () => {
      try{ await apiPost('/api/auth/logout', {}); location.href = '/login'; }
      catch (e){ alert(e.message); }
    });
    w.appendChild(logout);
    return;
  }

  w.appendChild(el('div', 'sect', '当前账号'));
  const kv = el('div', 'kv');
  kv.appendChild(el('b', '', me.name || me.email || '—'));
  kv.appendChild(el('span', '', (me.email || '') + ' · ' + (me.role === 'owner' ? '管理员（owner）' : '会员') + ' · ' + viaText));
  w.appendChild(kv);
  try{
    const setup = await apiGet('/api/setup/status');
    const modeText = setup.mode === 'merchant' ? '商家版（卖订阅）' : '个人版（自己用）';
    w.appendChild(el('div', 'hint2', '当前模式：' + modeText + (me.role === 'owner' ? ' · 你是 owner（管理员）' : ' · 你是会员')));
    if (me.role === 'owner' && setup.mode !== 'merchant'){
      w.appendChild(el('div', 'desc', '个人版没有订阅后台。要卖套餐，在 .env 里写 DEPLOY_MODE=merchant 再重启；已经有账号后网页不能改模式。'));
    }
  }catch (e){ /* 老版本没有 setup 接口 */ }


  // ---- 订阅 / 商家管理 ----
  let billing = null;
  try{ billing = await apiGet('/api/billing/me'); }catch (e){ billing = null; }
  if (billing && billing.enabled){
    if (me.role !== 'owner'){
    const acct = billing.account || {};
    const statusText = { active: '有效', expired: '已到期', none: '未订阅' }[acct.status] || acct.status || '未订阅';
    w.appendChild(el('div', 'sect', '订阅'));
    const row = el('div', 'kv');
    row.appendChild(el('b', '', (acct.plan_name || '没有套餐') + ' · ' + statusText));
    row.appendChild(el('span', '', acct.status === 'active'
      ? ('到期 ' + acct.expires_at + ' · 剩余 ' + acct.remaining_messages + ' 条 · 空间 ' + acct.storage_mb + 'MB')
      : '输入兑换码开通，数据不会因为到期被删'));
    w.appendChild(row);

    const code = inputEl('', '兑换码，例如 AG-XXXX-XXXX');
    w.appendChild(code);
    const use = mini('兑换 / 续费');
    w.appendChild(use);
    const out = el('div', 'out', '');
    w.appendChild(out);
    use.addEventListener('click', async () => {
      out.textContent = '兑换中…';
      try{
        const r = await apiPost('/api/billing/redeem', { code: code.value.trim() });
        out.textContent = '开通成功：' + r.account.plan_name + '，到期 ' + r.account.expires_at + '，剩余 ' + r.account.remaining_messages + ' 条';
        code.value = '';
      }catch (e){ out.textContent = e.message; }
    });
    }

    if (me.role === 'owner'){
      w.appendChild(el('div', 'sect', '商家后台 · 发兑换码'));
      w.appendChild(el('div', 'desc', '选套餐、填数量，生成后只显示这一次，先复制再发人。'));
      const genPlan = document.createElement('select');
      (billing.plans || []).forEach(plan => {
        const opt = document.createElement('option');
        opt.value = plan.id;
        opt.textContent = plan.name + '（' + plan.days + ' 天 / ' + plan.messages + ' 条 / ¥' + (plan.price_cents / 100) + '）';
        genPlan.appendChild(opt);
      });
      w.appendChild(genPlan);
      const genCount = inputEl('10', '生成数量，例如 10');
      const genNote = inputEl('', '备注（卖给谁 / 渠道，可不填）');
      w.appendChild(genCount);
      w.appendChild(genNote);
      const gen = mini('生成兑换码');
      w.appendChild(gen);
      const codesOut = el('div', 'out', '');
      codesOut.style.whiteSpace = 'pre-wrap';
      codesOut.style.userSelect = 'all';
      w.appendChild(codesOut);
      gen.addEventListener('click', async () => {
        codesOut.textContent = '生成中…';
        try{
          const r = await apiPost('/api/billing/admin/codes', {
            plan_id: genPlan.value,
            count: parseInt(genCount.value || '1', 10),
            note: genNote.value.trim()
          });
          codesOut.textContent = '只显示这一次，长按全选复制：\n' + (r.codes || []).join('\n');
        }catch (e){ codesOut.textContent = e.message; }
      });

      w.appendChild(el('div', 'sect', '商家后台 · 套餐'));
      (billing.plans || []).forEach(plan => {
        w.appendChild(el('div', 'desc', plan.id + ' · ' + plan.name + ' · ' + plan.days + ' 天 · ' + plan.messages + ' 条 · ¥' + (plan.price_cents / 100) + ' · ' + plan.storage_mb + 'MB'));
      });
      const pid = inputEl('', '套餐 id，例如 start');
      const pname = inputEl('', '套餐名，例如 入门月卡');
      const pdays = inputEl('', '有效天数，例如 30');
      const pmsgs = inputEl('', '可用消息条数，例如 300');
      const pprice = inputEl('', '价格（元），例如 9.9');
      const pstore = inputEl('', '空间 MB，例如 1024');
      [pid, pname, pdays, pmsgs, pprice, pstore].forEach(x => w.appendChild(x));
      const savePlan = mini('保存 / 新增套餐');
      w.appendChild(savePlan);
      const planOut = el('div', 'out', '');
      w.appendChild(planOut);
      savePlan.addEventListener('click', async () => {
        planOut.textContent = '保存中…';
        try{
          await apiPost('/api/billing/admin/plans', {
            id: pid.value.trim(), name: pname.value.trim(),
            days: parseInt(pdays.value || '30', 10),
            messages: parseInt(pmsgs.value || '300', 10),
            price_cents: Math.round(parseFloat(pprice.value || '0') * 100),
            storage_mb: parseInt(pstore.value || '1024', 10)
          });
          planOut.textContent = '已保存，重新点开这个面板看新套餐。';
        }catch (e){ planOut.textContent = e.message; }
      });

      w.appendChild(el('div', 'sect', '商家后台 · 会员额度'));
      try{
        const ov = await apiGet('/api/billing/admin/overview');
        (ov.accounts || []).forEach(u => {
          const a = u.account || {};
          const line = (u.email || u.name || u.id) + ' · ' + (a.status || 'none') + ' · ' + (a.plan_name || '无套餐') + ' · 剩 ' + (a.remaining_messages || 0) + ' 条';
          w.appendChild(el('div', 'desc', line));
        });
      }catch (e){ w.appendChild(el('div', 'desc', '读取会员失败：' + e.message)); }
    }
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

  // ---- 登录方式 ----
  w.appendChild(el('div', 'sect', '登录方式'));
  w.appendChild(el('div', 'desc', me.google
    ? 'Google 已绑定：' + (me.google_email || me.email)
    : 'Google 还没绑定。不影响用邮箱密码登录；商家配好 Google Client 后，可以用「用 Google 登录」进一次自动绑定。'));

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


  // ---- 身份验证器（Google Authenticator）----
  w.appendChild(el('div', 'sect', '身份验证器（Google Authenticator）'));
  const totpBox = el('div', '');
  w.appendChild(totpBox);
  function renderTotp() {
    totpBox.innerHTML = '';
    if (me.totp) {
      totpBox.appendChild(el('div', 'hint2', '已启用 · 备用码还剩 ' + (me.backup_codes_left || 0) + ' 个'));
      const regen = inputEl('', '输入当前 6 位验证码，重新生成备用码');
      const regenBtn = mini('重新生成备用码');
      const out1 = el('div', 'out', '');
      regenBtn.addEventListener('click', async () => {
        out1.textContent = '提交中…';
        try {
          const r = await apiPost('/api/auth/totp/backup', { code: regen.value.trim() });
          out1.textContent = '新的备用码（只显示这一次）：' + (r.backup_codes || []).join('  ');
        } catch (e) { out1.textContent = e.message; }
      });
      totpBox.appendChild(el('div', 'desc', '登录时会要 6 位动态码；手机丢了就用备用码，各用一次。'));
      totpBox.appendChild(regen);
      totpBox.appendChild(regenBtn);
      totpBox.appendChild(out1);
      const off = inputEl('', '6 位验证码或登录密码，用来确认是本人');
      const offBtn = mini('关闭两步验证', 'danger');
      const out2 = el('div', 'out', '');
      offBtn.addEventListener('click', async () => {
        if (!confirm('关掉两步验证后，只靠密码就能登录。继续？')) return;
        out2.textContent = '提交中…';
        const value = off.value.trim();
        try {
          await apiPost('/api/auth/totp/disable', /^\d{6}$/.test(value) ? { code: value } : { password: value });
          out2.textContent = '已关闭，刷新这个面板生效';
          me.totp = false;
        } catch (e) { out2.textContent = e.message; }
      });
      totpBox.appendChild(off);
      totpBox.appendChild(offBtn);
      totpBox.appendChild(out2);
      return;
    }
    totpBox.appendChild(el('div', 'desc', '给这个账号加一层：登录要密码 + 手机上的 6 位动态码；忘记密码也能用它重置，不依赖邮件。'));
    const start = mini('开始绑定（生成二维码）');
    totpBox.appendChild(start);
    const pane = el('div', '');
    totpBox.appendChild(pane);
    start.addEventListener('click', async () => {
      pane.innerHTML = '生成中…';
      try {
        const info = await apiPost('/api/auth/totp/setup', {});
        pane.innerHTML = '';
        const qr = el('div', 'qrbox');
        qr.innerHTML = info.svg || '';
        pane.appendChild(qr);
        pane.appendChild(el('div', 'hint2', '用 Google Authenticator「扫描二维码」；扫不了就手动输入这串密钥（类型选「基于时间」）：'));
        pane.appendChild(el('div', 'code', info.secret || ''));
        pane.appendChild(el('div', 'hint2', '账号名：' + (info.uri || '').split('?')[0]));
        const code = inputEl('', '输入刚出现的 6 位验证码');
        const ok = mini('确认启用');
        const out = el('div', 'out', '');
        ok.addEventListener('click', async () => {
          out.textContent = '提交中…';
          try {
            const r = await apiPost('/api/auth/totp/enable', { code: code.value.trim() });
            me.totp = true;
            out.textContent = '已启用。备用码（只显示这一次，抄下来）：' + (r.backup_codes || []).join('  ');
            renderTotp();
            const keep = el('div', 'out', '已启用。备用码（只显示这一次，抄下来）：' + (r.backup_codes || []).join('  '));
            totpBox.appendChild(keep);
          } catch (e) { out.textContent = e.message; }
        });
        pane.appendChild(code);
        pane.appendChild(ok);
        pane.appendChild(out);
      } catch (e) { pane.textContent = e.message; }
    });
  }
  renderTotp();

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
