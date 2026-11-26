const UPDATE_DISMISS = 'agent.update.dismissed';
let updateData = null;

function updateHide(){
  const box = $('updBox');
  if (box){ box.classList.add('hidden'); }
}

function updateShow(data){
  if (!data || !data.available){ return; }
  updateData = data;
  const remote = data.remote || {};
  const current = data.current || {};
  const key = remote.sha || '';
  if (key && localStorage.getItem(UPDATE_DISMISS) === key){ return; }
  const box = $('updBox');
  if (!box){ return; }
  const meta = $('updMeta');
  const summary = $('updSummary');
  const msg = $('updMsg');
  const now = $('updNow');
  if (meta){
    meta.textContent = (current.short || '当前') + ' → ' + (remote.short || String(key).slice(0, 7)) +
      (data.behind ? ' · 落后 ' + data.behind + ' 个提交' : '');
  }
  if (summary){ summary.textContent = data.summary || remote.title || '有新提交'; }
  if (msg){ msg.textContent = data.reason || ''; }
  if (now){
    now.disabled = !data.can_apply;
    now.textContent = data.can_apply ? '立即更新' : '不能自动更新';
    now.onclick = data.can_apply ? updateApply : null;
  }
  box.classList.remove('hidden');
}

async function updateApply(){
  const now = $('updNow');
  const msg = $('updMsg');
  if (now){ now.disabled = true; now.textContent = '更新中…'; }
  if (msg){ msg.textContent = '正在 git pull，别关页面…'; }
  try{
    const r = await apiPost('/api/update/apply');
    if (msg){ msg.textContent = r.message || '已更新，重启 python3 run.py 后生效'; }
    if (now){ now.textContent = '知道了'; now.disabled = false; now.onclick = updateHide; }
    if (r.current && r.current.sha){ localStorage.setItem(UPDATE_DISMISS, r.current.sha); }
    if (r.summary && $('updSummary')){ $('updSummary').textContent = r.summary; }
  }catch(e){
    if (msg){ msg.textContent = '更新失败：' + (e.message || e); }
    if (now){ now.textContent = '重试'; now.disabled = false; now.onclick = updateApply; }
  }
}

async function checkUpdate(){
  if (localStorage.getItem('agent.update.off') === '1'){ return; }
  if (document.hidden){ return; }
  try{
    const r = await apiGet('/api/update/check');
    if (r && r.available){ updateShow(r); }
  }catch(e){}
}

function updateLater(){
  if (updateData && updateData.remote && updateData.remote.sha){
    localStorage.setItem(UPDATE_DISMISS, updateData.remote.sha);
  }
  updateHide();
}

document.addEventListener('click', (ev) => {
  if (!ev.target || !ev.target.closest){ return; }
  if (ev.target.closest('#updLater')){ updateLater(); }
  if (ev.target.closest('#updBox') && ev.target.id === 'updBox'){ updateLater(); }
});

setTimeout(checkUpdate, 1200);
setInterval(checkUpdate, 10 * 60 * 1000);
document.addEventListener('visibilitychange', () => { if (!document.hidden){ checkUpdate(); } });
