/* 画板：手指/鼠标画一张，导成 PNG 上传，作为附件跟着下一句话发出去。零依赖。 */
(function(){
  const board = document.getElementById('board');
  const canvas = document.getElementById('boardCanvas');
  if (!board || !canvas){ return; }
  const ctx = canvas.getContext('2d');
  let color = '#111827';
  let size = 4;
  let drawing = false;
  let last = null;
  let history = [];

  function fit(){
    const stage = canvas.parentElement;
    const w = Math.max(200, stage.clientWidth);
    const h = Math.max(200, stage.clientHeight);
    const dpr = window.devicePixelRatio || 1;
    let snapshot = null;
    if (canvas.width && canvas.height){
      try { snapshot = ctx.getImageData(0, 0, canvas.width, canvas.height); } catch (e) { snapshot = null; }
    }
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    if (snapshot){ ctx.putImageData(snapshot, 0, 0); }
  }

  function point(ev){
    const rect = canvas.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  }
  function pushHistory(){
    try { history.push(ctx.getImageData(0, 0, canvas.width, canvas.height)); } catch (e) { }
    if (history.length > 12){ history.shift(); }
  }

  canvas.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    canvas.setPointerCapture(ev.pointerId);
    drawing = true;
    pushHistory();
    last = point(ev);
    ctx.strokeStyle = color;
    ctx.lineWidth = size;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(last.x, last.y);
    ctx.lineTo(last.x + 0.01, last.y + 0.01);
    ctx.stroke();
  });
  canvas.addEventListener('pointermove', (ev) => {
    if (!drawing){ return; }
    const p = point(ev);
    ctx.beginPath();
    ctx.moveTo(last.x, last.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    last = p;
  });
  const stop = () => { drawing = false; last = null; };
  canvas.addEventListener('pointerup', stop);
  canvas.addEventListener('pointercancel', stop);
  canvas.addEventListener('pointerleave', stop);

  document.querySelectorAll('.swatch').forEach((btn) => {
    btn.addEventListener('click', () => {
      color = btn.getAttribute('data-color') || '#111827';
      document.querySelectorAll('.swatch').forEach((b) => b.classList.remove('on'));
      btn.classList.add('on');
    });
  });
  const range = document.getElementById('brushSize');
  if (range){ range.addEventListener('input', () => { size = Number(range.value) || 4; }); }

  function clear(){
    pushHistory();
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  window.openBoard = function(){
    board.classList.add('on');
    setTimeout(fit, 30);
  };
  window.closeBoard = function(){ board.classList.remove('on'); };

  document.getElementById('boardBack').addEventListener('click', closeBoard);
  document.getElementById('boardClear').addEventListener('click', clear);
  document.getElementById('boardUndo').addEventListener('click', () => {
    const snap = history.pop();
    if (snap){ ctx.putImageData(snap, 0, 0); }
    else { clear(); }
  });

  document.getElementById('boardSend').addEventListener('click', async () => {
    const btn = document.getElementById('boardSend');
    btn.textContent = '发送中…';
    try{
      const src = document.createElement('canvas');
      src.width = canvas.width;
      src.height = canvas.height;
      const sctx = src.getContext('2d');
      sctx.fillStyle = '#ffffff';
      sctx.fillRect(0, 0, src.width, src.height);
      sctx.drawImage(canvas, 0, 0);
      const dataUrl = src.toDataURL('image/png');
      const r = await apiPost('/api/ui/upload', { name: 'sketch', data_url: dataUrl });
      if (window.addAttachment){ window.addAttachment({ path: r.path, url: r.url, type: 'image', name: '画板草图' }); }
      closeBoard();
      note('草图已附加，写一句话再发送。');
      document.getElementById('input').focus();
    }catch (e){
      note('草图上传失败：' + e.message, 'err');
    }finally{
      btn.textContent = '发送';
    }
  });

  const entry1 = document.getElementById('btnCanvas');
  const entry2 = document.getElementById('goCanvas');
  if (entry1){ entry1.addEventListener('click', openBoard); }
  if (entry2){ entry2.addEventListener('click', () => { openDrawer(false); openBoard(); }); }
})();
