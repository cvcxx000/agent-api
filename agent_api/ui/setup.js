(async function(){
  const $ = id => document.getElementById(id);
  function say(text){ $('msg').textContent = text || ''; }
  async function get(url){
    const r = await fetch(url, { credentials: 'same-origin' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }
  async function post(url, body){
    const r = await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }

  let setup = { mode: 'unset', env_locked: false, has_users: false };
  let auth = {};
  try{
    setup = await get('/api/setup/status');
    auth = await get('/api/auth/status');
  }catch (e){ say('连不上服务：' + e.message); return; }

  if (setup.has_users){
    $('setup-form').classList.add('hide');
    $('done-card').classList.remove('hide');
    return;
  }

  if (setup.env_locked){
    $('mode').disabled = true;
    $('mode').value = setup.mode === 'merchant' ? 'merchant' : 'personal';
    $('mode-note').textContent = '模式被 .env 的 DEPLOY_MODE 锁住了，这里不能改。';
  }else if (setup.mode === 'merchant'){
    $('mode').value = 'merchant';
  }

  function syncMode(){
    const merchant = $('mode').value === 'merchant';
    $('codes').disabled = !merchant;
    if (!merchant) $('codes').checked = false;
    $('register').disabled = merchant;
    if (merchant) $('register').checked = true;
    $('mode-note').textContent = merchant
      ? '商家版：用户自己注册、买套餐；你配模型 Key、发兑换码。'
      : '个人版：一台设备一个管理员，注册默认关闭；游客按开关决定。';
  }
  $('mode').addEventListener('change', syncMode);
  syncMode();

  $('setup-form').addEventListener('submit', async function(ev){
    ev.preventDefault();
    say('');
    const body = {
      mode: $('mode').value,
      email: $('email').value.trim(),
      password: $('password').value,
      name: $('name').value.trim(),
      guest_enabled: $('guest').checked,
      allow_register: $('register').checked,
      codes_enabled: $('codes').checked,
      agree: $('agree').checked
    };
    if (!body.email || !body.password) return say('邮箱和密码都要填');
    if (!body.agree) return say('先勾选同意《用户协议》和《商家协议》');
    const btn = $('submit');
    btn.disabled = true;
    say('正在初始化…');
    try{
      const out = await post('/api/setup/init', body);
      $('setup-form').classList.add('hide');
      $('recovery-card').classList.remove('hide');
      $('recovery-code').textContent = out.recovery_code || '（没返回，去登录页重置一次）';
    }catch (e){ say(e.message); }
    finally { btn.disabled = false; }
  });
})();
