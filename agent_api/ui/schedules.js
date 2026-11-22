// 定时任务面板：列表 / 新建 / 开关 / 立即跑 / 删除
function schAgo(ts){
  if (!ts){ return '还没跑过'; }
  return String(ts);
}

async function showSchedules(){
  const w = sheetWrap();
  let data;
  try{ data = await apiGet('/api/schedules'); }
  catch (e){ w.innerHTML = '<div class="err">加载失败：' + esc(e.message) + '</div>'; return; }

  const tasks = data.tasks || [];
  w.appendChild(el('div', 'sect', '任务（' + tasks.length + '）'));
  if (!tasks.length){
    w.appendChild(el('div', 'desc', '还没有定时任务。下面新建一个：到点它自己跑，不用你催。'));
  }
  const list = el('div', 'rows');
  tasks.forEach(t => {
    const row = el('div', 'sch-row');
    const main = el('div', 'sch-main');
    main.appendChild(el('div', 'sch-name', t.name + (t.enabled ? '' : '（已暂停）')));
    const action = t.action || {};
    const what = action.kind === 'workflow' ? ('工作流 ' + action.workflow) : String(action.prompt || '').slice(0, 60);
    main.appendChild(el('div', 'sch-meta', t.human + ' · cron ' + t.cron + ' · 下次 ' + (t.next_run || '—')));
    main.appendChild(el('div', 'sch-meta', '内容：' + (what || '—')));
    const last = t.last_run
      ? ('上次 ' + t.last_run + ' · ' + (t.last_status === 'error' ? '失败' : '成功') + ' · ' + (t.run_count || 0) + ' 次')
      : schAgo('');
    const lastEl = el('div', 'sch-meta' + (t.last_status === 'error' ? ' sch-bad' : (t.last_status ? ' sch-ok' : '')), last);
    main.appendChild(lastEl);
    if (t.last_error){ main.appendChild(el('div', 'sch-meta sch-bad', '错误：' + t.last_error.slice(0, 120))); }
    row.appendChild(main);

    const acts = el('div', 'sch-acts');
    const run = el('button', 'btn', '跑一次');
    run.addEventListener('click', async () => {
      run.disabled = true; run.textContent = '跑中…';
      try{
        const r = await apiPost('/api/schedules/' + encodeURIComponent(t.id) + '/run', {});
        note(r.ok ? '跑完了：' + String(r.output || '').slice(0, 120) : ('失败：' + (r.error || r.message || '')));
      }catch(e){ note('跑失败：' + e.message); }
      finally{ run.disabled = false; run.textContent = '跑一次'; pushSheet('schedules', '定时任务', 'cron 到点自动跑', () => showSchedules()); }
    });
    const tog = el('button', 'btn', t.enabled ? '暂停' : '启用');
    tog.addEventListener('click', async () => {
      tog.disabled = true;
      try{
        await apiPost('/api/schedules/' + encodeURIComponent(t.id), { enabled: !t.enabled });
        pushSheet('schedules', '定时任务', 'cron 到点自动跑', () => showSchedules());
      }catch(e){ note('操作失败：' + e.message); tog.disabled = false; }
    });
    const del = el('button', 'btn', '删除');
    del.addEventListener('click', async () => {
      del.disabled = true;
      try{
        await apiDelete('/api/schedules/' + encodeURIComponent(t.id));
        pushSheet('schedules', '定时任务', 'cron 到点自动跑', () => showSchedules());
      }catch(e){ note('删除失败：' + e.message); del.disabled = false; }
    });
    acts.appendChild(run); acts.appendChild(tog); acts.appendChild(del);
    row.appendChild(acts);
    list.appendChild(row);
  });
  w.appendChild(list);

  w.appendChild(el('div', 'sect', '新建'));
  const name = inputEl('', '任务名字，例如 每天早上读日报');
  const cron = inputEl('0 9 * * *', 'cron：分 时 日 月 周（也支持 @daily / @every 10m）');
  const kind = document.createElement('select');
  [['chat', '跑一句话'], ['workflow', '跑工作流']].forEach(([v, t2]) => {
    const o = document.createElement('option');
    o.value = v; o.textContent = t2;
    kind.appendChild(o);
  });
  const prompt = document.createElement('textarea');
  prompt.placeholder = '要它做的事，例如：抓一下今天的金价，超过前一日就提醒我';
  prompt.rows = 3;
  const workflow = inputEl('', '工作流名（kind=工作流时填，见「工作流」面板）');
  workflow.style.display = 'none';
  kind.addEventListener('change', () => { workflow.style.display = kind.value === 'workflow' ? '' : 'none'; });

  w.appendChild(lab('名字'));
  w.appendChild(name);
  w.appendChild(lab('时间'));
  w.appendChild(cron);
  w.appendChild(lab('做什么'));
  w.appendChild(kind);
  w.appendChild(prompt);
  w.appendChild(workflow);

  const add = document.createElement('button');
  add.className = 'btn primary';
  add.textContent = '创建';
  add.addEventListener('click', async () => {
    add.disabled = true;
    try{
      const body = { name: name.value.trim(), cron: cron.value.trim(), kind: kind.value,
        prompt: prompt.value.trim(), workflow: workflow.value.trim() };
      const r = await apiPost('/api/schedules', body);
      note('已创建，下次 ' + ((r.task && r.task.next_run) || '待计算'));
      pushSheet('schedules', '定时任务', 'cron 到点自动跑', () => showSchedules());
    }catch(e){ note('创建失败：' + e.message); }
    finally{ add.disabled = false; }
  });
  w.appendChild(add);

  w.appendChild(el('div', 'desc',
    'cron 写法：0 9 * * *（每天 9 点）、30 8 * * 1-5（工作日 8:30）、*/15 * * * *（每 15 分钟）。' +
    '每个任务的结果都在「[定时] 名字」这个会话里。'));
}
