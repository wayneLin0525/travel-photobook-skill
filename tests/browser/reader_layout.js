async (page) => {
  // Run with the reviewed export already open. No album names or photo IDs are used.
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.waitForTimeout(350);
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const errors = [], requests = [], results = [];
  page.on('pageerror', error => errors.push(String(error)));
  const book = await page.locator('#book-data').evaluate(node => JSON.parse(node.textContent));
  const startingViewport = page.viewportSize();
  const startingActual = await page.evaluate(() => ({width:innerWidth,height:innerHeight}));
  const scaleX = startingViewport ? startingViewport.width/startingActual.width : 1;
  const scaleY = startingViewport ? startingViewport.height/startingActual.height : 1;
  const jump = async n => {
    await page.locator('#contents').click();
    await page.locator('#page-number').fill(String(n));
    await page.locator('#jump-form button').click();
    await page.waitForTimeout(650);
  };
  const size = async (width, height) => {
    // Respect the browser's existing zoom while testing specified CSS viewport sizes.
    await page.setViewportSize({width:Math.round(width*scaleX), height:Math.round(height*scaleY)});
    await page.waitForTimeout(700);
  };
  const measure = () => page.evaluate(() => {
    const rect = selector => {
      const {x,y,width,height,top,right,bottom,left} = document.querySelector(selector).getBoundingClientRect();
      return {x,y,width,height,top,right,bottom,left};
    };
    return {viewport:[innerWidth,innerHeight],mount:rect('#mount'),book:rect('#book'),header:rect('.topbar'),nav:rect('.navigation'),
      tools:[...document.querySelectorAll('.tools button')].map(node => ({label:node.getAttribute('aria-label'),...node.getBoundingClientRect().toJSON()})),
      removed:!!document.querySelector('.intro,.track,footer,#hint,#chapter-label'),
      overflow:document.documentElement.scrollWidth>innerWidth+1 || document.documentElement.scrollHeight>innerHeight+1,
      label:document.querySelector('#page-label').textContent};
  });
  for (const [width,height] of [[1440,1000],[2688,1318],[1280,720],[390,844],[320,568],[844,390]]) {
    await size(width,height);await jump(2);
    const m = await measure(), desktop = m.viewport[0]>760;
    assert(!m.overflow && !m.removed, 'Reader overflow or obsolete information remains');
    assert(m.tools.length===3, 'The three top actions must remain');
    assert(m.tools.every(b => b.left>=0 && b.right<=m.viewport[0]+1 && b.bottom<=m.header.bottom+1), 'A top action is clipped');
    assert(m.book.width<=m.mount.width+1 && m.book.height<=m.mount.height+1, 'Book is clipped');
    assert(Math.max(m.book.width/m.mount.width,m.book.height/m.mount.height)>.98, 'Book does not use the available space');
    assert(m.book.top>=m.header.bottom && m.book.bottom<=m.nav.top+1, 'Book covers header/navigation');
    if (desktop) assert(Math.abs(m.book.width/m.book.height - 2/book.ratio)<.02, 'Spread ratio changed');
    else assert(Math.abs(m.book.width/m.book.height - 1/book.ratio)<.02, 'Mobile page ratio changed');
    results.push(m);
    if (!desktop) {
      await page.keyboard.press('Home');await page.waitForTimeout(650);
      const front=await page.locator('.book-page[data-index="0"]').boundingBox();
      assert(front.x>=0 && front.x+front.width<=m.viewport[0]+1,'Mobile cover is clipped');
      assert(Math.abs(front.x+front.width/2-m.viewport[0]/2)<2,'Mobile cover is not centered');
    }
  }
  await size(1440,1000);await page.keyboard.press('Home');await page.waitForTimeout(650);
  const cover = await page.locator('.book-page[data-index="0"]').boundingBox();
  const dimensions = await page.evaluate(() => ({width:innerWidth,height:innerHeight}));
  assert(Math.abs(cover.x+cover.width/2-dimensions.width/2)<2, 'Cover is not centered');
  await page.locator('#next').click();await page.waitForTimeout(160);
  const animation = await page.locator('#book').getAttribute('data-state');
  assert(animation==='flipping', 'Page-turn animation did not start');
  await page.waitForTimeout(850);
  assert((await page.locator('#page-label').textContent()).startsWith('01'), 'Opening page is wrong');
  await jump(Math.min(20,book.pageCount));
  await page.locator('#zoom').click();
  assert(await page.locator('#zoom-dialog').evaluate(node=>node.open), 'Zoom did not open');
  assert(await page.locator('#zoom-options button').count()>=1, 'Zoom page selection missing');
  await page.locator('#zoom-options button').last().click();
  const zoomTitle = await page.locator('#zoom-title').textContent();await page.keyboard.press('Escape');
  await page.locator('#fullscreen').click();await page.waitForTimeout(550);
  const fullscreen = await page.evaluate(() => !!document.fullscreenElement);
  assert(fullscreen, 'Fullscreen did not open');
  await page.evaluate(() => document.exitFullscreen());await page.waitForTimeout(550);
  const afterFullscreen = await measure();
  assert(!afterFullscreen.overflow, 'Fullscreen resize introduced overflow');
  await size(390,844);await page.emulateMedia({reducedMotion:'reduce'});await page.waitForTimeout(300);
  await page.keyboard.press('Home');const labels=[];
  for (let n=1;n<=book.pageCount;n++) {
    await page.locator('#next').click();
    const label = await page.locator('#page-label').textContent();
    assert(label.startsWith(String(n).padStart(2,'0')+' /'), 'Mobile skips/repeats page '+n);
    await page.locator(`#mount img[data-index="${n+1}"]`).first().evaluate(img=>img.decode());labels.push(label);
  }
  await page.locator('#next').click();assert(await page.locator('#next').isDisabled(), 'Back-cover boundary is wrong');
  const backNode = page.locator(`.book-page[data-index="${book.pages.length-1}"]`);
  await backNode.waitFor({state:'visible'});
  const back = await backNode.boundingBox();
  const phoneWidth = await page.evaluate(() => innerWidth);
  assert(back.x>=0 && back.x+back.width<=phoneWidth+1, 'Mobile back cover is clipped');
  await page.locator('#previous').click();assert((await page.locator('#page-label').textContent()).startsWith(String(book.pageCount).padStart(2,'0')), 'Back cover cannot return');
  await page.emulateMedia({reducedMotion:'no-preference'});await page.waitForTimeout(350);
  await page.keyboard.press('Home');await page.waitForTimeout(650);
  const touch = await page.context().newCDPSession(page);
  let swipe;
  try {
    await touch.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});await page.waitForTimeout(350);
    const r = await page.locator('.book-page[data-index="0"]').boundingBox();
    const y = r.y+r.height*.65;
    // StPageFlip distinguishes a quick swipe from a held paper drag (250 ms).
    await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:r.x+r.width-15,y}]});
    await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:r.x+20,y}]});
    await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    await page.waitForTimeout(950);swipe = await page.locator('#page-label').textContent();
    assert(swipe.startsWith('01'), 'Touch swipe did not open the book');
  } finally {await touch.send('Emulation.setTouchEmulationEnabled',{enabled:false});await touch.detach();}
  await page.waitForTimeout(350);
  const statusSpace = await page.locator('#mount').evaluate(n => [n.clientWidth,n.clientHeight]);
  await page.evaluate(() => {window.__readerTestFullscreen=document.documentElement.requestFullscreen;document.documentElement.requestFullscreen=()=>Promise.reject(new Error('test unsupported'));});
  let fallback;
  try {
    await page.locator('#fullscreen').click();fallback = await page.locator('#status-message').textContent();
    assert(!await page.locator('#status-message').isHidden(),'Fullscreen failure feedback missing');
    const afterStatus = await page.locator('#mount').evaluate(n => [n.clientWidth,n.clientHeight]);
    assert(JSON.stringify(statusSpace)===JSON.stringify(afterStatus),'Status message takes book space');
    await page.waitForTimeout(5150);assert(await page.locator('#status-message').isHidden(),'Transient feedback did not expire');
  } finally {await page.evaluate(() => {document.documentElement.requestFullscreen=window.__readerTestFullscreen;delete window.__readerTestFullscreen;});}
  const requestListener = request => requests.push(request.url());page.on('request',requestListener);
  try {
    await page.context().setOffline(true);await jump(1);
    await page.locator('#next').click();await page.waitForTimeout(900);
    assert((await page.locator('#page-label').textContent()).startsWith('02'), 'Offline navigation failed');
  } finally {await page.context().setOffline(false);page.off('request',requestListener);}
  assert(requests.length===0, 'Offline reader requested remote assets');
  assert(errors.length===0, 'Reader JS errors: '+errors.join(';'));
  return {result:'passed',windows:results,coverCentered:true,animation,zoomTitle,fullscreen,
    mobilePagesChecked:labels.length,touchSwipe:swipe,fullscreenFallback:fallback,transientStatusVerified:true,offlineRequests:requests,errors};
}
