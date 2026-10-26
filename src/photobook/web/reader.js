(() => {
  'use strict';
  const book = JSON.parse(document.getElementById('book-data').textContent);
  const $ = id => document.getElementById(id);
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let flip, mobile = false, current = 0, direction = 1, resizeTimer, statusTimer, initialized = false, lastSize = '';
  $('title').textContent = book.title;
  $('page-number').max = book.pageCount;

  function showStatus(message) {
    clearTimeout(statusTimer);
    $('status-message').textContent = message; $('status-message').hidden = false;
    statusTimer = setTimeout(() => { $('status-message').hidden = true; }, 5000);
  }

  function visibleIndices(index = current) {
    if (mobile || index === 0 || index === book.pages.length - 1) return [index];
    const start = index % 2 ? index : index - 1;
    return [start, start + 1];
  }
  function meaningful(index) { return book.pages[index]?.kind !== 'blank'; }
  function hydrate(center) {
    // Keep a small neighborhood decoded; the complete compressed book remains offline.
    document.querySelectorAll('#mount .book-page img').forEach(img => {
      const index = Number(img.dataset.index);
      if (Math.abs(index - center) <= 5) {
        if (!img.getAttribute('src')) img.src = book.pages[index].image;
      } else img.removeAttribute('src');
    });
  }
  function update(index) {
    current = index;
    if (mobile && !meaningful(index)) {
      const target = Math.max(0, Math.min(book.pages.length - 1, index + direction));
      requestAnimationFrame(() => { if (flip && current === index) flip.turnToPage(target); });
      return;
    }
    const ids = visibleIndices(index);
    const content = ids.map(i => book.pages[i]).filter(p => p.kind === 'page');
    $('page-label').textContent = content.length ? `${content.map(p => p.label).join(' — ')} / ${book.pageCount}` : book.pages[index].label;
    $('previous').disabled = index === 0;
    $('next').disabled = index >= book.pages.length - 1;
    $('zoom').disabled = !ids.some(i => book.pages[i].image);
    const shell = $('book');
    shell?.classList.toggle('front', index === 0);
    shell?.classList.toggle('back', index === book.pages.length - 1);
    hydrate(index);
  }
  function build(index = 0) {
    if (flip) flip.destroy();
    $('mount').replaceChildren();
    mobile = window.innerWidth <= 760;
    // Fit after the actual header/navigation layout, with no fixed book-size ceiling.
    const mount = $('mount');
    const unit = Math.max(1, Math.floor(Math.min(mount.clientWidth / (mobile ? 1 : 2), mount.clientHeight / book.ratio)));
    lastSize = `${mount.clientWidth}:${mount.clientHeight}:${mobile}`;
    const shell = document.createElement('div'); shell.id = 'book';
    shell.className = 'book-shell' + (index === 0 ? ' front' : index === book.pages.length - 1 ? ' back' : '');
    $('mount').append(shell);
    book.pages.forEach(p => {
      const node = document.createElement('div'); node.className = 'book-page'; node.dataset.kind = p.kind; node.dataset.index = p.index;
      node.setAttribute('aria-label', p.kind === 'page' ? `内页 ${p.label} ${p.title || ''}` : p.label);
      if (p.kind === 'cover' || p.kind === 'back') node.dataset.density = 'hard';
      if (p.image) {
        const img = document.createElement('img'); img.dataset.index = p.index;
        img.alt = p.kind === 'page' ? `第 ${p.pdf_page} 页：${p.title || ''}` : `${book.title} 封面`;
        img.draggable = false; node.append(img);
      } else if (p.kind === 'back') {
        const board = document.createElement('div'); board.className = 'end-board';
        const title = document.createElement('strong'); title.textContent = book.title.split('·')[0].trim();
        const text = document.createElement('small'); text.textContent = 'THE JOURNEY STAYS';
        board.append(title, text); node.append(board);
      }
      shell.append(node);
    });
    if (mobile && !meaningful(index)) index = Math.min(index + 1, book.pages.length - 1);
    flip = new St.PageFlip(shell, {width:unit, height:Math.floor(unit * book.ratio), size:'fixed',
      showCover:true, usePortrait:mobile, autoSize:true, drawShadow:true, maxShadowOpacity:.26,
      flippingTime:650, mobileScrollSupport:true, swipeDistance:35, clickEventForward:false,
      showPageCorners:!reduced.matches, disableFlipByClick:reduced.matches, useMouseEvents:!reduced.matches});
    flip.on('flip', e => { direction = e.data >= current ? 1 : -1; update(e.data); });
    flip.on('changeState', e => { shell.dataset.state = e.data; });
    flip.loadFromHTML(shell.querySelectorAll('.book-page'));
    flip.turnToPage(index);
    update(flip.getCurrentPageIndex());
    initialized = true;
    document.body.dataset.ready = 'true';
  }
  function go(index, animate = true) {
    index = Math.max(0, Math.min(book.pages.length - 1, index));
    direction = index >= current ? 1 : -1;
    hydrate(index);
    if (reduced.matches || !animate) flip.turnToPage(index);
    else flip.flip(index, 'bottom');
  }
  function step(delta) {
    if (flip.getState() !== 'read') return;
    const ids = visibleIndices();
    let index = delta > 0 ? ids[ids.length - 1] + 1 : ids[0] - 1;
    if (mobile) while (index >= 0 && index < book.pages.length && !meaningful(index)) index += delta;
    if (index >= 0 && index < book.pages.length) go(index);
  }
  $('previous').onclick = () => step(-1); $('next').onclick = () => step(1);
  const drawer = $('drawer'), zoomDialog = $('zoom-dialog');
  $('contents').onclick = () => { drawer.showModal(); $('contents').setAttribute('aria-expanded', 'true'); };
  $('close-drawer').onclick = () => drawer.close();
  drawer.addEventListener('close', () => $('contents').setAttribute('aria-expanded', 'false'));
  const links = [{title:'封面', index:0, page:0}, ...book.chapters];
  links.forEach((c, i) => {
    const button = document.createElement('button'); button.type = 'button';
    const text = document.createElement('span'); text.textContent = c.title;
    const number = document.createElement('small'); number.textContent = c.page ? String(c.page).padStart(2,'0') : 'COVER';
    button.append(text, number); button.dataset.chapter = i;
    button.onclick = () => { drawer.close(); go(c.index, false); };
    $('chapters').append(button);
  });
  $('jump-form').onsubmit = e => { e.preventDefault(); if (!$('jump-form').reportValidity()) return; drawer.close(); go(Number($('page-number').value) + 1, false); };
  function showZoom(index) {
    const p = book.pages[index];
    $('zoom-image').src = p.image; $('zoom-image').classList.remove('actual');
    $('zoom-title').textContent = p.kind === 'page' ? `内页 ${p.label} · ${p.title || ''}` : '封面';
    document.querySelectorAll('#zoom-options button').forEach(b => b.classList.toggle('active', Number(b.dataset.index) === index));
  }
  $('zoom').onclick = () => {
    const ids = visibleIndices().filter(i => book.pages[i].image);
    if (!ids.length) return;
    $('zoom-options').replaceChildren();
    ids.forEach(i => {
      const b = document.createElement('button'); b.dataset.index = i; b.textContent = book.pages[i].label + (ids.length > 1 ? ' 页' : '');
      b.onclick = () => showZoom(i); $('zoom-options').append(b);
    });
    showZoom(ids[0]); zoomDialog.showModal();
  };
  $('close-zoom').onclick = () => { zoomDialog.close(); $('zoom-image').removeAttribute('src'); };
  $('zoom-image').onclick = () => $('zoom-image').classList.toggle('actual');
  [drawer, zoomDialog].forEach(dialog => dialog.addEventListener('click', e => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close(); } }));
  $('fullscreen').onclick = async () => {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
    catch { showStatus('当前浏览器不支持全屏，可使用浏览器的全屏功能。'); }
  };
  document.addEventListener('keydown', e => {
    if (drawer.open || zoomDialog.open || /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    if (e.key === 'Home') { e.preventDefault(); go(0, false); }
    if (e.key === 'End') { e.preventDefault(); go(book.pages.length - 1, false); }
  });
  function scheduleResize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const mount = $('mount');
      const size = `${mount.clientWidth}:${mount.clientHeight}:${window.innerWidth <= 760}`;
      if (initialized && size !== lastSize) build(current);
    }, 150);
  }
  window.addEventListener('resize', scheduleResize);
  new ResizeObserver(scheduleResize).observe($('mount'));
  reduced.addEventListener('change', () => build(current));
  try { build(); } catch (error) { $('error').hidden = false; $('error').textContent = '照片书未能加载，请使用新版 Chrome、Edge 或 Safari 打开。' + error.message; console.error(error); }
})();
