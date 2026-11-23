// 登录页：登录 / 注册 / 恢复码 / 忘记密码 / Google 票 / 密钥登录
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var state = { status: null, forgotEmail: "", ticket: "" };

  function show(pane) {
    ['login', 'register', 'recovery', 'forgot', 'reset', 'ticket', 'key'].forEach(function (name) {
      var node = $('pane-' + name);
      if (node) node.classList.toggle('on', name === pane);
    });
    $('loading').style.display = 'none';
    var first = $('pane-' + pane).querySelector('input:not([readonly])');
    if (first && window.innerWidth > 700) first.focus();
  }

  function msg(id, text, ok) {
    var node = $(id);
    if (!node) return;
    node.textContent = text || '';
    node.className = 'msg on ' + (ok ? 'ok' : 'bad');
  }

  function quiet(id) {
    var node = $(id);
    if (node) node.className = 'msg';
  }

  async function post(path, body) {
    var resp = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}), credentials: 'same-origin'
    });
    var data = {};
    try { data = await resp.json(); } catch (e) { data = {}; }
    if (!resp.ok) throw new Error(data.error || ('HTTP ' + resp.status));
    return data;
  }

  async function boot() {
    var params = new URLSearchParams(location.search);
    state.ticket = params.get('reset_ticket') || '';
    var err = params.get('error') || '';
    try {
      var resp = await fetch('/api/auth/status', { credentials: 'same-origin' });
      state.status = await resp.json();
    } catch (e) {
      $('loading').textContent = '连不上服务：' + e.message;
      return;
    }
    var st = state.status;
    $('mode-line').textContent = st.users ? (st.users + ' 个账号 · ' + (st.google_on ? 'Google 已配' : '仅密码')) : '本机工具型 Agent';

    if (st.enabled) {
      try {
        var me = await fetch('/api/auth/me', { credentials: 'same-origin' });
        if (me.ok) { location.href = '/'; return; }
      } catch (e) { /* 忽略，继续走登录 */ }
    }

    if (st.google_on) $('btn-google-login').style.display = 'flex';
    if (st.register_open && !st.setup_needed) $('login-register-hint').style.display = 'block';
    if (st.setup_needed) {
      $('reg-title').textContent = '创建第一个账号';
      $('reg-sub').textContent = '这台设备还没有账号。第一个账号是主人（owner），建完就要求登录。';
      show('register');
    } else if (state.ticket) {
      $('ticket-email').value = params.get('email') || '';
      show('ticket');
    } else {
      show('login');
      if (err) msg('msg-login', err, false);
    }
  }

  // ---- 切换 ----
  document.addEventListener('click', function (event) {
    var go = event.target.closest('[data-go]');
    if (go) {
      var target = go.getAttribute('data-go');
      if (target === 'forgot') {
        var email = ($('login-email').value || $('reset-email').value || '').trim();
        if (email) $('forgot-email').value = email;
      }
      if (target === 'register') $('reg-email').value = ($('login-email').value || '').trim();
      show(target);
    }
  });

  $('theme-toggle').addEventListener('click', function () {
    var html = document.documentElement;
    var dark = html.getAttribute('data-theme') === 'dark';
    html.setAttribute('data-theme', dark ? 'light' : 'dark');
    try { localStorage.setItem('agent_theme', dark ? 'light' : 'dark'); } catch (e) {}
    this.textContent = dark ? '切换深色' : '切换浅色';
  });
  try {
    var saved = localStorage.getItem('agent_theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
    if (saved === 'dark') $('theme-toggle').textContent = '切换浅色';
  } catch (e) {}

  // ---- 登录 ----
  $('btn-login').addEventListener('click', async function () {
    quiet('msg-login');
    var email = $('login-email').value.trim();
    var password = $('login-password').value;
    if (!email || !password) return msg('msg-login', '邮箱和密码都要填', false);
    this.disabled = true;
    try {
      await post('/api/auth/login', { email: email, password: password });
      location.href = '/';
    } catch (e) {
      msg('msg-login', e.message, false);
    } finally { this.disabled = false; }
  });
  $('login-password').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('btn-login').click(); });

  // ---- Google 登录 ----
  $('btn-google-login').addEventListener('click', function () {
    var email = $('login-email').value.trim();
    location.href = '/api/auth/google/start?purpose=login' + (email ? ('&email=' + encodeURIComponent(email)) : '');
  });

  // ---- 注册 ----
  $('btn-register').addEventListener('click', async function () {
    quiet('msg-register');
    var body = {
      email: $('reg-email').value.trim(),
      password: $('reg-password').value,
      name: $('reg-name').value.trim()
    };
    if (!body.email || !body.password) return msg('msg-register', '邮箱和密码都要填', false);
    this.disabled = true;
    try {
      var out = await post('/api/auth/register', body);
      if (out.recovery_code) {
        $('recovery-code').textContent = out.recovery_code;
        show('recovery');
      } else {
        location.href = '/';
      }
    } catch (e) {
      msg('msg-register', e.message, false);
    } finally { this.disabled = false; }
  });

  $('btn-copy-recovery').addEventListener('click', async function () {
    var text = $('recovery-code').textContent;
    try {
      await navigator.clipboard.writeText(text);
      this.textContent = '已复制';
    } catch (e) {
      msg('msg-register', '浏览器不让复制，长按选中抄下来', false);
    }
  });
  $('btn-recovery-done').addEventListener('click', function () { location.href = '/'; });

  // ---- 忘记密码 ----
  $('btn-forgot-check').addEventListener('click', async function () {
    quiet('msg-forgot');
    $('forgot-methods').style.display = 'none';
    var email = $('forgot-email').value.trim();
    if (!email) return msg('msg-forgot', '先填邮箱', false);
    this.disabled = true;
    try {
      var out = await post('/api/auth/forgot', { email: email });
      if (!out.exists) return msg('msg-forgot', '这台设备没有这个邮箱的账号', false);
      state.forgotEmail = out.email;
      $('reset-email').value = out.email;
      var methods = out.methods || [];
      $('btn-google-reset').style.display = methods.indexOf('google') >= 0 ? 'flex' : 'none';
      $('btn-use-code').style.display = methods.indexOf('recovery') >= 0 ? 'block' : 'none';
      $('forgot-hint').textContent = methods.indexOf('google') >= 0
        ? '这个账号绑了 Google：走 Google 身份认证，证明是本人后直接设新密码，不用发邮件。'
        : '这个账号没绑 Google，只能用注册时给的恢复码重置。';
      $('forgot-methods').style.display = 'block';
    } catch (e) {
      msg('msg-forgot', e.message, false);
    } finally { this.disabled = false; }
  });
  $('btn-google-reset').addEventListener('click', function () {
    location.href = '/api/auth/google/start?purpose=reset&email=' + encodeURIComponent(state.forgotEmail);
  });
  $('btn-use-code').addEventListener('click', function () { show('reset'); });

  // ---- 恢复码重置 ----
  $('btn-reset').addEventListener('click', async function () {
    quiet('msg-reset');
    var body = {
      email: $('reset-email').value.trim(),
      code: $('reset-code').value.trim(),
      new: $('reset-password').value
    };
    if (!body.email || !body.code || !body.new) return msg('msg-reset', '三项都要填', false);
    this.disabled = true;
    try {
      var out = await post('/api/auth/reset', body);
      $('reset-code-out').textContent = out.recovery_code || '—';
      $('reset-newcode').style.display = 'block';
      msg('msg-reset', '重置成功，所有设备都已下线，请重新登录', true);
    } catch (e) {
      msg('msg-reset', e.message, false);
    } finally { this.disabled = false; }
  });
  $('btn-reset-done').addEventListener('click', function () { location.href = '/login'; });

  // ---- Google 票重置 ----
  $('btn-ticket').addEventListener('click', async function () {
    quiet('msg-ticket');
    var password = $('ticket-password').value;
    if (!password) return msg('msg-ticket', '先填新密码', false);
    this.disabled = true;
    try {
      var out = await post('/api/auth/google/reset', { ticket: state.ticket, new: password });
      $('ticket-code-out').textContent = out.recovery_code || '—';
      $('ticket-newcode').style.display = 'block';
      msg('msg-ticket', '设置成功，请用新密码登录', true);
    } catch (e) {
      msg('msg-ticket', e.message, false);
    } finally { this.disabled = false; }
  });
  $('btn-ticket-done').addEventListener('click', function () { location.href = '/login'; });

  // ---- 密钥登录 ----
  $('btn-key').addEventListener('click', function () { show('key'); });
  $('btn-key-login').addEventListener('click', async function () {
    quiet('msg-key');
    var key = $('key-input').value.trim();
    if (!key) return msg('msg-key', '先粘贴 ak_ 开头的密钥', false);
    this.disabled = true;
    try {
      await post('/api/auth/key-login', { key: key });
      location.href = '/';
    } catch (e) {
      msg('msg-key', e.message, false);
    } finally { this.disabled = false; }
  });
  $('key-input').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('btn-key-login').click(); });

  boot();
})();
