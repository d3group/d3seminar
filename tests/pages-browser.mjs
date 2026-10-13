import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createInterface} from 'node:readline';
import {readFile, mkdir} from 'node:fs/promises';

const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = spawn('python3', ['tests/browser_fixture.py', '--pages'], {stdio:['ignore', 'pipe', 'inherit']});
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
      assert.equal(await preview.locator('.event-reply-deadline:visible').count(), 0);
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
  await page.getByText('Hello Browser.', {exact:false}).waitFor();
  assert.doesNotMatch(await page.title(), /STALE STATIC COPY/);
  const deadlineText = 'Please set your preferences by Wednesday, 7 October 2026.';
  assert.equal(await page.locator('[data-event="seminar-1"] .event-reply-deadline').textContent(), deadlineText);
  const secondDeadline = page.locator('[data-event="seminar-2"] .event-reply-deadline');
  assert.equal(await secondDeadline.isVisible(), true);
  const adminKey = new URLSearchParams(new URL(details.admin).hash.slice(1)).get('key');
  const adminApi = async (action, data = {}) => {
    const response = await page.request.post(details.api + 'api/admin', {data:{action, secret:adminKey, event_id:'seminar-2', ...data}});
    assert.equal(response.status(), 200);
    return response.json();
  };
  const secondPoll = (await adminApi('dashboard')).poll;
  const proposal = {deadline:'2026-10-08T17:00', options:secondPoll.slots.map(slot => ({id:slot.id, start:slot.start.slice(0,16)}))};
  const review = await adminApi('preview_schedule', {proposal});
  assert.equal(review.resets_answers, false);
  await adminApi('save_schedule', {proposal, preview:review.preview, confirm_reset:false});
  await page.reload();
  await secondDeadline.getByText('Thursday, 8 October 2026', {exact:true}).waitFor();
  assert.equal(await page.locator('[data-event="seminar-1"] .event-reply-deadline').textContent(), deadlineText);
  await page.getByRole('link', {name:'Confirm Seminar 1', exact:true}).click();
  assert.equal(new URL(page.url()).pathname, '/d3seminar/event.html');
  await page.locator('#poll-deadline').getByText(deadlineText, {exact:true}).waitFor();
  await page.locator('.poll-option').first().getByLabel('Available', {exact:true}).check();
  await page.getByRole('button', {name:'Save for later', exact:true}).click();
  await page.locator('#notice').filter({hasText:'Response saved.'}).waitFor();
  await page.reload();
  await page.locator('.poll-option input:checked').waitFor();
  const otherTab = await browser.newPage();
  await otherTab.goto(details.home);
  const firstCard = otherTab.locator('[data-event="seminar-1"]');
  await firstCard.locator('button.calendar-date').first().click();
  await firstCard.getByLabel('Unavailable', {exact:true}).check();
  await firstCard.getByRole('button', {name:'Save changes', exact:true}).click();
  await firstCard.locator('.calendar-save-status').filter({hasText:'Response saved.'}).waitFor();
  const readSaved = async () => {
    const response = await page.request.post(details.api + 'api/invitation', {data:{token:'T'.repeat(43), event_id:'seminar-1'}});
    assert.equal(response.status(), 200);
    return response.json();
  };
  const newer = await readSaved();
  await page.locator('.poll-option').nth(1).getByLabel('Available', {exact:true}).check();
  await page.getByLabel('A note for the organizer', {exact:false}).fill('Keep this unsaved draft visible.');
  const rejected = page.waitForResponse(response => response.url().endsWith('/api/respond'));
  await page.getByRole('button', {name:'Save for later', exact:true}).click();
  assert.equal((await rejected).status(), 409);
  await page.locator('#notice.error').filter({hasText:'out of date'}).waitFor();
  assert.equal(await page.locator('.poll-option').nth(1).getByLabel('Available', {exact:true}).isChecked(), true);
  assert.equal(await page.locator('#comment').inputValue(), 'Keep this unsaved draft visible.');
  assert.equal(await page.getByRole('button', {name:'Save for later', exact:true}).isEnabled(), true);
  const unchanged = await readSaved();
  assert.deepEqual(unchanged.answers, newer.answers);
  assert.equal(unchanged.comment, newer.comment);
  assert.equal(unchanged.response_version, newer.response_version);
  await page.reload();
  await page.locator('.poll-option input:checked').waitFor();
  assert.equal(await page.locator('.poll-option').first().getByLabel('Unavailable', {exact:true}).isChecked(), true);
  await page.locator('.poll-option').nth(1).getByLabel('Available', {exact:true}).check();
  await page.getByRole('button', {name:'Save for later', exact:true}).click();
  await page.locator('#notice').filter({hasText:'Response saved.'}).waitFor();
  await page.locator('#comment').fill('A second save in the same tab.');
  await page.getByRole('button', {name:'Save for later', exact:true}).click();
  await page.locator('#notice').filter({hasText:'Response saved.'}).waitFor();
  assert.equal((await readSaved()).comment, 'A second save in the same tab.');
  await otherTab.close();
  await page.goto(details.base + 'agenda.html?present=seminar-3&placeholder=1#token=' + 'T'.repeat(43));
  await page.getByLabel('Talk title', {exact:true}).fill('Provisional research topic');
  await page.getByRole('button', {name:'Save talk', exact:true}).click();
  await page.getByRole('heading', {name:'Your placeholder is saved.', exact:true}).waitFor();
  await page.getByRole('button', {name:'View in agenda', exact:true}).click();
  const nudge = page.locator('#presentation-nudge');
  assert.match(await nudge.textContent(), /Seminar proposal: added \(details to follow\).*Onsite proposal: to add/);
  await nudge.getByRole('link', {name:'Propose a talk', exact:true}).click();
  assert.equal(await page.locator('#presentation-event').inputValue(), 'onsite');
  await page.getByLabel('Talk title', {exact:true}).fill('Onsite research discussion');
  await page.getByRole('button', {name:'Save talk', exact:true}).click();
  await page.getByRole('heading', {name:'Your talk is saved.', exact:true}).waitFor();
  await page.getByRole('button', {name:'View in agenda', exact:true}).click();
  const proposalsSaved = /Seminar proposal: added \(details to follow\).*Onsite proposal: added \(details to follow\)/;
  assert.match(await nudge.textContent(), proposalsSaved);
  assert.equal(await nudge.getByRole('link', {name:'Propose a talk', exact:true}).count(), 0);
  await nudge.getByRole('link', {name:'Update your presentations', exact:true}).waitFor();
  await page.reload();
  await nudge.waitFor();
  assert.match(await nudge.textContent(), proposalsSaved);
  await page.goto(details.home);
  await nudge.waitFor();
  assert.match(await nudge.textContent(), proposalsSaved);
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
  await page.getByRole('link', {name:'browser-test@example.com', exact:true}).waitFor();
  const visitor = await browser.newPage();
  await visitor.goto(details.base + 'agenda.html');
  await visitor.getByText('Browser Test', {exact:true}).waitFor();
  assert.equal(await visitor.getByText('Cross-origin private title').count(), 0);
  assert.equal(await visitor.locator('input:visible, textarea:visible').count(), 0);
  await visitor.goto(details.base);
  await visitor.locator('button.calendar-date').first().click();
  assert.equal(await visitor.locator('.calendar-response-form, .vote-people').count(), 0);
  assert.equal(await visitor.locator('[data-event="seminar-1"] .event-reply-deadline').textContent(), deadlineText);
  await page.goto(details.admin);
  await page.getByRole('button', {name:'Refresh responses', exact:true}).waitFor();
  await page.locator('#admin-notice').filter({hasText:'Showing saved responses'}).waitFor();
  const onsite = await (await page.request.post(details.api + 'api/invitation', {data:{token:'T'.repeat(43), event_id:'onsite'}})).json();
  const onsiteSave = await page.request.post(details.api + 'api/respond', {form:{token:'T'.repeat(43), event_id:'onsite',
    revision:onsite.revision, partial:'true', ['answer:' + onsite.poll.slots[0].id]:'available'}});
  assert.equal(onsiteSave.status(), 200);
  for (const eventId of ['onsite', 'seminar-2', 'seminar-3', 'seminar-1']) {
    const eventLink = page.locator(`#event-tabs a[href*="event=${eventId}"]`);
    await eventLink.click();
    await page.locator('#admin-notice').filter({hasText:'Showing saved responses'}).waitFor();
    if (eventId === 'onsite') {
      await page.getByRole('button', {name:'Finalize this time', exact:true}).click();
      await page.locator('#admin-notice').filter({hasText:'Time finalized.'}).waitFor();
    }
    await page.getByRole('button', {name:'Email', exact:true}).click();
    await page.locator('#mail-kind').selectOption('confirm');
    const previewResponse = page.waitForResponse(response => response.url().endsWith('/api/admin') && response.request().postDataJSON().action === 'email_preview');
    await page.getByRole('button', {name:'Preview recipients and emails', exact:true}).click();
    const result = await (await previewResponse).json();
    assert.equal(result.ok, true, result.error);
    await page.locator('#mail-review').waitFor();
    assert.equal(result.event_id, eventId);
    assert.equal(result.messages.length, eventId === 'onsite' ? 1 : 0);
    const commands = await page.locator('.mail-steps dd code').allTextContents();
    const confirmation = commands.find(command => command.includes(' confirm')).trim().split(/\s+/);
    assert.deepEqual(confirmation.slice(0, 3), ['python3', 'scripts/mailer.py', 'confirm']);
    assert.equal(confirmation[confirmation.indexOf('--event') + 1], result.event_id);
    assert.equal(confirmation.includes('--send'), false);
    for (const command of commands.filter(command => !command.includes(' confirm'))) {
      assert.equal(command.includes('--event'), false);
    }
    await page.getByRole('button', {name:'Events', exact:true}).click();
  }
  await visitor.goto(details.base + '?event=onsite');
  await visitor.locator('[data-event="onsite"] .date-confirmed').first().waitFor();
  assert.equal(await visitor.locator('[data-event="onsite"] .event-reply-deadline').isVisible(), false);
  const agendaApi = async (action, data) => {
    const response = await page.request.post(details.api + 'api/' + action, {data:{event_id:'seminar-2', ...data}});
    const result = await response.json();
    assert.equal(response.status(), 200, result.error);
    return result;
  };
  const invitation = await agendaApi('invitation', {token:'T'.repeat(43)});
  await agendaApi('admin', {action:'sync', secret:new URLSearchParams(new URL(details.admin).hash.slice(1)).get('key'),
    invites:[['d', 'Zoe', 'T'], ['e', 'Änne', 'A']].map(([id, name, token]) => ({
      id:id.repeat(24), name, role:'student', email:id + '@example.com',
      token_hash:createHash('sha256').update(token.repeat(43)).digest('hex'),
      poll:invitation.poll, revision:invitation.revision, site_url:details.base
    }))});
  for (const token of ['T', 'A']) await agendaApi('presentation', {token:token.repeat(43),
    presentation:{title:'Research update ' + token, presentation_minutes:20, questions_minutes:10}});
  await page.goto(details.base + 'agenda.html?present=seminar-2#token=' + 'T'.repeat(43));
  const orderedRows = page.locator('[data-event="seminar-2"] .talk-row');
  const runningOrder = () => orderedRows.evaluateAll(rows => rows.map(row => [row.querySelector('.talk-presenter > span').textContent, row.querySelector('.talk-timeslot strong').textContent]));
  await orderedRows.nth(1).waitFor({state:'attached'});
  const beforeSave = await runningOrder();
  assert.deepEqual(beforeSave, [['Zoe', '14:00–14:30'], ['Änne', '14:30–15:00']]);
  const anne = await agendaApi('invitation', {token:'A'.repeat(43)});
  await agendaApi('presentation', {token:'A'.repeat(43), version:anne.presentation.version,
    presentation:{...anne.presentation, title:'Updated in another window'}});
  const saveTalk = async () => {
    await page.getByRole('button', {name:'Save talk', exact:true}).click();
    await page.locator('#presentation-status.success').waitFor();
    assert.deepEqual(await runningOrder(), beforeSave);
  };
  await page.locator('#talk-abstract').fill('An updated abstract.');
  await saveTalk();
  assert.match(await orderedRows.nth(1).textContent(), /Updated in another window/);
  await page.locator('#slides-file').setInputFiles({name:'order-test.pdf', mimeType:'application/pdf', buffer:pdf});
  await saveTalk();
  assert.equal(await orderedRows.first().getByRole('button', {name:'Download slides', exact:true}).count(), 1);
  await page.reload();
  await orderedRows.nth(1).waitFor({state:'attached'});
  assert.deepEqual(await runningOrder(), beforeSave);
  await page.getByRole('button', {name:'Withdraw proposal', exact:true}).click();
  await page.locator('#presentation-status.success').filter({hasText:'Proposal withdrawn.'}).waitFor();
  assert.deepEqual(await runningOrder(), [['Änne', '14:00–14:30']]);
  await page.reload();
  await orderedRows.first().waitFor({state:'attached'});
  assert.deepEqual(await runningOrder(), [['Änne', '14:00–14:30']]);
  assert.deepEqual(errors, []);
  console.log('Pages subpath: static launch, API outage, mobile layout, live config, responses, agenda, uploads, protected downloads, public privacy and admin passed.');
} finally {
  await browser?.close();
  fixture.kill('SIGINT');
}
