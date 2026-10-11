import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {readFile, mkdir} from 'node:fs/promises';

const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = spawn('python3', ['tests/browser_fixture.py', '--pages', '--presentations'], {stdio:['ignore', 'pipe', 'inherit']});
let browser;
try {
  const details = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Pages fixture did not start')), 15000);
    createInterface({input:fixture.stdout}).once('line', line => {clearTimeout(timer); resolve(JSON.parse(line));});
    fixture.once('exit', code => {clearTimeout(timer); reject(new Error(`Fixture exited: ${code}`));});
  });
  browser = await chromium.launch({headless:true});
  const page = await browser.newPage({acceptDownloads:true});
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const bootstrap = JSON.parse(await readFile(new URL('../site/config.json', import.meta.url), 'utf8'));
  await mkdir(new URL('../test-results/', import.meta.url), {recursive:true});
  for (const mode of ['pending', 'unavailable']) {
    const preview = await browser.newPage({viewport:{width:390, height:844}});
    preview.setDefaultTimeout(10000);
    await preview.clock.setFixedTime(new Date('2026-10-01T12:00:00Z'));
    preview.on('pageerror', error => errors.push(error.message));
    const apiRequests = [];
    preview.on('request', request => {
      if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request.url());
    });
    await preview.route(details.base + 'config.json', route => route.fulfill({json:{...bootstrap,
      api_url:mode === 'pending' ? '' : details.api, backend_url:''}}));
    await preview.route(details.api + 'config.json', route => route.fulfill({status:503, body:'Unavailable'}));
    for (const hash of ['', '#token=' + 'T'.repeat(43)]) {
      await preview.goto(details.base + hash);
      await preview.locator('.event-card').nth(3).waitFor({state:'attached'});
      assert.equal(await preview.locator('#home-notice').isVisible(), true);
      assert.match(await preview.locator('#home-notice').textContent(), /provisional/i);
      assert.equal(await preview.locator('#programme-progress').isVisible(), false);
      assert.equal(await preview.locator('.event-card-bottom:visible, input:visible, textarea:visible').count(), 0);
      assert.equal(await preview.locator('button.calendar-date:not(:disabled)').count(), 0);
      assert.equal(await preview.locator('.date-leading, .date-confirmed').filter({has:preview.locator('.calendar-day-number')}).count(), 0);
      for (const logo of await preview.locator('.masthead img').all()) {
        assert.equal(await logo.evaluate(image => image.complete && image.naturalWidth > 0), true);
      }
      assert.equal(await preview.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await preview.screenshot({path:`test-results/pages-${mode}-mobile.png`, fullPage:true});
      await preview.getByRole('link', {name:'View agenda for Seminar 1', exact:true}).click();
      assert.equal(new URL(preview.url()).pathname, '/d3seminar/agenda.html');
      await preview.locator('.agenda-event').nth(3).waitFor({state:'attached'});
      assert.match(await preview.locator('#agenda-notice').textContent(), /provisional/i);
      assert.equal(await preview.locator('#presentation-editor').isVisible(), false);
      assert.equal(await preview.locator('[data-present], .public-presenters').count(), 0);
      assert.equal(await preview.getByText('No public talks yet.', {exact:true}).count(), 0);
      await preview.getByRole('link', {name:'About', exact:true}).click();
      await preview.getByRole('heading', {name:'About the D3 Seminar', exact:true}).waitFor();
      await preview.goto(details.base + 'event.html?event=seminar-2' + hash);
      if (hash) {
        await preview.locator('#notice').filter({hasText:'provisional'}).waitFor();
        assert.equal(await preview.locator('#response-form').isVisible(), false);
      } else {
        await preview.locator('[data-event="seminar-2"] .event-disclosure[open]').waitFor();
      }
    }
    await preview.goto(details.admin);
    await preview.locator('#admin-notice').filter({hasText:'provisional'}).waitFor();
    assert.equal(await preview.locator('#dashboard').isVisible(), false);
    assert.deepEqual(apiRequests, []);
    await preview.close();
  }
  await page.goto(details.home);
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  assert.doesNotMatch(await page.title(), /STALE STATIC COPY/);
  await page.getByRole('link', {name:'Confirm Seminar 1', exact:true}).click();
  assert.equal(new URL(page.url()).pathname, '/d3seminar/event.html');
  await page.locator('.poll-option').first().getByLabel('Available', {exact:true}).check();
  await page.getByRole('button', {name:'Save for later', exact:true}).click();
  await page.locator('#notice').filter({hasText:'Response saved.'}).waitFor();
  await page.reload();
  await page.locator('.poll-option input:checked').waitFor();
  await page.goto(details.base + 'agenda.html?present=seminar-1#token=' + 'T'.repeat(43));
  await page.getByLabel('Talk title', {exact:true}).fill('Cross-origin private title');
  await page.getByLabel('Abstract', {exact:false}).fill('Private abstract, visible only to invitees.');
  await page.getByRole('radio', {name:'Public', exact:true}).check();
  const html = '<!doctype html><h1>Private explainer</h1><script>try{document.cookie;document.body.dataset.isolated="no"}catch(e){document.body.dataset.isolated="yes"}</script>';
  await page.locator('#explainer-file').setInputFiles({name:'explainer.html', mimeType:'text/html', buffer:Buffer.from(html)});
  const pdf = Buffer.from('%PDF-1.4\nCross-origin upload\n%%EOF');
  await page.locator('#slides-file').setInputFiles({name:'pages-test.pdf', mimeType:'application/pdf', buffer:pdf});
  await page.getByRole('button', {name:'Save talk', exact:true}).click();
  await page.locator('#presentation-status.success').waitFor();
  await page.getByRole('button', {name:'View in agenda', exact:true}).click();
  const popupPromise = page.context().waitForEvent('page');
  await page.getByRole('button', {name:'Open explainer', exact:true}).click();
  const popup = await popupPromise;
  await popup.getByRole('heading', {name:'Private explainer', exact:true}).waitFor();
  assert.equal(await popup.locator('body').getAttribute('data-isolated'), 'yes');
  assert.equal(await popup.evaluate(() => window.opener), null);
  await popup.close();
  const materialResponse = page.context().waitForEvent('response', {predicate:r => r.url().endsWith('/api/material')});
  const downloadPromise = page.waitForEvent('download');
  downloadPromise.catch(() => {});
  await page.getByRole('button', {name:'Download slides', exact:true}).click();
  const material = await materialResponse;
  assert.equal(material.status(), 200, await material.text().catch(() => 'Download response'));
  const download = await downloadPromise;
  assert.equal(download.suggestedFilename(), 'pages-test.pdf');
  const chunks = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk);
  assert.deepEqual(Buffer.concat(chunks), pdf);
  await page.reload();
  await page.getByText('Cross-origin private title', {exact:true}).waitFor();
  const visitor = await browser.newPage();
  await visitor.goto(details.base + 'agenda.html');
  await visitor.getByText('Browser Test', {exact:true}).waitFor();
  assert.equal(await visitor.getByText('Cross-origin private title').count(), 0);
  assert.equal(await visitor.locator('input:visible, textarea:visible').count(), 0);
  await visitor.goto(details.base);
  await visitor.locator('button.calendar-date').first().click();
  assert.equal(await visitor.locator('.calendar-response-form, .vote-people').count(), 0);
  await page.goto(details.admin);
  await page.getByRole('button', {name:'Refresh responses', exact:true}).waitFor();
  await page.locator('#admin-notice').filter({hasText:'Showing saved responses'}).waitFor();
  assert.deepEqual(errors, []);
  console.log('Pages subpath: static launch, API outage, mobile layout, live config, responses, agenda, uploads, protected downloads, public privacy and admin passed.');
} finally {
  await browser?.close();
  fixture.kill('SIGINT');
}
