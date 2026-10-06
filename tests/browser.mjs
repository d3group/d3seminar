import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir} from 'node:fs/promises';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';

const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const fixture = spawn('python3', ['tests/browser_fixture.py'], {cwd:root, stdio:['ignore','pipe','inherit']});
let browser, page;
try {
  const details = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Browser fixture did not start')), 15000);
    const lines = createInterface({input:fixture.stdout});
    lines.once('line', line => {clearTimeout(timeout);resolve(JSON.parse(line));});
    fixture.once('exit', code => {clearTimeout(timeout);reject(new Error(`Fixture exited: ${code}`));});
  });
  browser = await chromium.launch({headless:true});
  page = await browser.newPage({viewport:{width:1440,height:1080}, timezoneId:'America/Los_Angeles'});
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(details.base);
  const navigation = () => page.getByRole('navigation', {name:'Main navigation'});
  await navigation().getByRole('link', {name:'About', exact:true}).click();
  await page.getByRole('heading', {name:'About the D3 Seminar', exact:true}).waitFor();
  assert.equal(await navigation().getByRole('link', {name:'About', exact:true}).getAttribute('aria-current'), 'page');
  assert.equal(await page.locator('.about-format dt').count(), 3);
  assert.equal(await navigation().getByRole('link', {name:'Programme', exact:true}).getAttribute('href'), './');
  await mkdir(new URL('../test-results/',import.meta.url),{recursive:true});
  await page.screenshot({path:root+'test-results/about-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:root+'test-results/about-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1080});
  await navigation().getByRole('link', {name:'Agenda', exact:true}).click();
  await page.locator('.agenda-event').nth(3).waitFor();
  assert.equal(await navigation().getByRole('link', {name:'Agenda', exact:true}).getAttribute('aria-current'), 'page');
  assert.deepEqual(await page.locator('.agenda-event h2').allTextContents(), ['Seminar 1', 'Seminar 2', 'Seminar 3', 'Onsite meeting']);
  assert.equal(await page.getByText('No public talks yet.', {exact:true}).count(), 4);
  assert.match(await page.locator('#agenda-notice').textContent(), /shared with invited participants/);
  await navigation().getByRole('link', {name:'Programme', exact:true}).click();
  await page.locator('.event-card').nth(3).waitFor();
  assert.equal(await page.locator('.event-card').count(), 4);
  await page.locator('.masthead img').evaluateAll(images => Promise.all(images.map(image => image.decode())));
  assert.equal(await page.locator('.masthead img').count(), 2);
  assert.deepEqual(await page.locator('.event-card h3').allTextContents(), ['Seminar 1', 'Seminar 2', 'Seminar 3', 'Onsite meeting']);
  await page.locator('[data-event="onsite"] .event-disclosure-summary').click();
  await page.getByText('2 days · 09:00–17:00 each day', {exact:true}).waitFor();
  assert.equal(await page.getByText('2 hours', {exact:true}).count(), 0);
  const eventCalendar = id => page.locator(`[data-event="${id}"] .event-calendar`);
  const outlineStyle = (locator, pseudo = null) => locator.evaluate((node, pseudo) => getComputedStyle(node, pseudo).borderBottomStyle, pseudo);
  assert.equal(await page.locator('.calendar-month').count(), 4);
  await eventCalendar('seminar-1').locator('button.calendar-date:enabled').first().waitFor();
  assert.deepEqual(await eventCalendar('seminar-1').locator('button.calendar-date').allTextContents(), ['9', '12', '13', '14']);
  assert.deepEqual(await eventCalendar('onsite').locator('button.calendar-date').allTextContents(), ['2', '3', '5', '6', '9', '10']);
  assert.equal(await eventCalendar('onsite').locator('.pair-to-next').count(), 3);
  assert.equal(await eventCalendar('onsite').locator('.pair-from-previous').count(), 3);
  assert.equal(await eventCalendar('seminar-1').locator('.calendar-pair').count(), 0);
  assert.equal(await outlineStyle(eventCalendar('seminar-1').locator('button.calendar-date').first()), 'dotted');
  assert.equal(await outlineStyle(eventCalendar('onsite').locator('.calendar-pair').first(), '::after'), 'dotted');
  assert.deepEqual(await eventCalendar('seminar-1').locator('tbody tr').first().locator('td').allTextContents(), ['', '', '', '1', '2', '3', '4']);
  assert.match(await eventCalendar('seminar-1').locator('button.calendar-date').first().getAttribute('title'), /14:00–16:00/);
  await mkdir(new URL('../test-results/',import.meta.url),{recursive:true});
  await page.locator('[data-event="onsite"] .event-disclosure-summary').click();
  await page.screenshot({path:root+'test-results/home-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:root+'test-results/home-mobile.png',fullPage:true});
  await page.setViewportSize({width:320,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.setViewportSize({width:1440,height:1080});
  await eventCalendar('seminar-1').locator('button.calendar-date').first().click();
  await page.getByText('0 of 1 available', {exact:true}).waitFor();
  assert.deepEqual(await eventCalendar('seminar-1').locator('.vote-breakdown dd').allTextContents(), ['0', '0', '1']);
  assert.equal(await page.locator('.calendar-response-form, .vote-people, .vote-ring, input, textarea').count(), 0);
  assert.equal(await page.locator('#programme-progress').isVisible(), false);
  assert.equal((await page.locator('body').textContent()).includes('Browser Test'), false);
  await page.goto(details.base + 'event.html?event=onsite#demo=1');
  await page.waitForURL(details.base + '?event=onsite');
  await eventCalendar('onsite').locator('button.calendar-date:enabled').first().waitFor();
  assert.equal(await page.locator('[data-event="onsite"] details').first().getAttribute('open'), '');
  assert.equal(await page.locator('input, textarea, #demo-button').count(), 0);

  // Existing personal links now open the programme and retain access when navigating.
  await page.goto(details.home);
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  await navigation().getByRole('link', {name:'About', exact:true}).click();
  await page.getByRole('heading', {name:'About the D3 Seminar', exact:true}).waitFor();
  assert.equal(new URL(page.url()).hash, '#token=' + 'T'.repeat(43));
  assert.equal(new URL(await page.locator('.brand').getAttribute('href')).hash, '#token=' + 'T'.repeat(43));
  assert.equal(new URL(await page.locator('.university-brand').getAttribute('href')).hash, '');
  assert.equal(await page.locator('.about-copy a[href^="mailto:"]').getAttribute('href').then(href => href.includes('token')), false);
  await navigation().getByRole('link', {name:'Programme', exact:true}).click();
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  const dateButton = eventCalendar('seminar-1').locator('button.calendar-date').first();
  assert.equal(await outlineStyle(dateButton), 'dotted');
  assert.equal(await page.locator('.event-grid button.date-leading').count(), 0);
  assert.equal(await dateButton.locator('.vote-mark.vote-unknown').count(), 1);
  assert.equal(await dateButton.locator('.vote-student .vote-person').count(), 1);
  assert.equal(await dateButton.locator('.vote-professor .vote-person').count(), 0);
  assert.equal(await dateButton.locator('.vote-unknown').evaluate(node => getComputedStyle(node).stroke), 'rgba(0, 0, 0, 0)');
  await dateButton.focus();
  await page.keyboard.press('Enter');
  await eventCalendar('seminar-1').locator('.calendar-votes').waitFor();
  assert.deepEqual(await eventCalendar('seminar-1').locator('.vote-people li').allTextContents(), ['Browser TestAwaiting reply']);
  await page.keyboard.press('Escape');
  assert.equal(await eventCalendar('seminar-1').locator('.calendar-votes').isVisible(), false);
  assert.equal(await dateButton.evaluate(button => button === document.activeElement), true);
  await page.getByRole('link',{name:'Confirm Seminar 1',exact:true}).click();
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  assert.equal(new URL(await navigation().getByRole('link', {name:'About', exact:true}).getAttribute('href')).hash, '#token=' + 'T'.repeat(43));
  assert.equal(await page.locator('input:checked').count(), 0);
  assert.equal(await page.getByLabel('If necessary', {exact:true}).count(), 0);
  assert.equal(await page.locator('#calendar').isVisible(), false);
  const first = () => page.locator('.poll-option').first();
  const save = async () => {
    await page.getByRole('button',{name:'Save for later',exact:true}).click();
    await page.locator('#notice').filter({hasText:'Response saved.'}).waitFor();
  };
  const back = async () => {
    await page.reload();
    await page.getByText('Your saved answers are shown below', {exact:false}).waitFor();
  };
  await first().getByLabel('Available', {exact:true}).check();
  await page.locator('#comment').fill('Other mornings could work too.');
  await save();
  await page.getByText('Your availability is saved for 1 of 4 options.', {exact:false}).waitFor();
  await back();
  assert.equal(await first().getByLabel('Available', {exact:true}).isChecked(), true);
  assert.equal(await page.locator('#comment').inputValue(), 'Other mornings could work too.');

  // Public visitors see current totals without identities, notes, or editing controls.
  const visitor = await browser.newPage();
  visitor.on('pageerror', error => errors.push(error.message));
  await visitor.goto(details.base);
  const publicDate = visitor.locator('[data-event="seminar-1"] button.calendar-date:enabled').first();
  await publicDate.click();
  await visitor.getByText('1 of 1 available', {exact:true}).waitFor();
  assert.equal(await outlineStyle(publicDate), 'dashed');
  assert.equal(await visitor.locator('.calendar-response-form, .vote-people, .vote-ring, input, textarea').count(), 0);
  assert.doesNotMatch(await visitor.locator('body').textContent(), /Browser Test|Other mornings/);
  await visitor.screenshot({path:root+'test-results/public-responses-desktop.png', fullPage:true});
  await visitor.setViewportSize({width:390,height:844});
  assert.equal(await visitor.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), true);
  await visitor.screenshot({path:root+'test-results/public-responses-mobile.png', fullPage:true});
  await visitor.close();

  // Calendar edits preserve other choices and notes, and failed saves keep the draft.
  await page.goto(details.home);
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  const inlineCard = page.locator('[data-event="seminar-1"]');
  const inlineDates = () => inlineCard.locator('button.calendar-date');
  const inlineForm = () => inlineCard.locator('.calendar-response-form');
  const inlineSave = async () => {
    await inlineForm().getByRole('button', {name:'Save changes', exact:true}).click();
    await inlineForm().getByRole('status').filter({hasText:'Response saved.'}).waitFor();
  };
  await inlineDates().nth(1).click();
  await inlineForm().getByLabel('Available', {exact:true}).check();
  await page.route('**/api/respond', route => route.fulfill({status:503, json:{ok:false, error:'Please try again.'}}));
  await inlineForm().getByRole('button', {name:'Save changes', exact:true}).click();
  await inlineForm().getByRole('status').filter({hasText:'Please try again.'}).waitFor();
  assert.equal(await inlineForm().getByLabel('Available', {exact:true}).isChecked(), true);
  assert.equal(await inlineDates().nth(1).locator('.vote-unknown').count(), 1);
  await page.unroute('**/api/respond');
  await inlineSave();
  assert.equal(await inlineDates().nth(0).locator('.vote-available').count(), 1);
  assert.equal(await inlineDates().nth(1).locator('.vote-available').count(), 1);
  assert.match(await inlineCard.locator('.event-status').textContent(), /2\/4 options/);
  await inlineForm().getByRole('button', {name:'Clear answer', exact:true}).click();
  await inlineSave();
  assert.equal(await inlineDates().nth(1).locator('.vote-unknown').count(), 1);
  for (const index of [2, 3, 1]) {
    await inlineDates().nth(index).click();
    await inlineForm().getByLabel('Unavailable', {exact:true}).check();
  }
  await inlineSave();
  assert.match(await page.locator('#journey-progress').textContent(), /confirmed 1 of 4/);
  await inlineCard.screenshot({path:root+'test-results/calendar-editor-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), true);
  await inlineCard.screenshot({path:root+'test-results/calendar-editor-mobile.png'});
  await page.setViewportSize({width:320,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), true);
  await page.setViewportSize({width:1440,height:1080});
  for (const index of [2, 3, 1]) {
    await inlineDates().nth(index).click();
    await inlineForm().getByRole('button', {name:'Clear answer', exact:true}).click();
  }
  await inlineSave();
  assert.match(await page.locator('#journey-progress').textContent(), /confirmed 0 of 4/);
  await page.goto(details.invitation);
  await page.getByText('Your saved answers are shown below', {exact:false}).waitFor();
  assert.equal(await first().getByLabel('Available', {exact:true}).isChecked(), true);
  assert.equal(await page.locator('input:checked').count(), 1);
  assert.equal(await page.locator('#comment').inputValue(), 'Other mornings could work too.');
  const admin = await browser.newPage({viewport:{width:1440,height:1080}});
  admin.setDefaultTimeout(15000);
  admin.on('pageerror', error => errors.push(error.message));
  await admin.goto(details.admin);
  await admin.getByText('0 of 1 people have answered every option', {exact:true}).waitFor();
  await admin.getByRole('button', {name:'Preview recipients and emails',exact:true}).click();
  await admin.locator('#mail-count').filter({hasText:'1 email from organizer@example.com'}).waitFor();
  assert.match(await admin.locator('.mail-message pre').textContent(), /Hello Browser Test/);
  assert.equal(await admin.locator('#smtp-password, #send-mail, #mail-send-form').count(), 0);
  assert.match(await admin.locator('#mail-notice').textContent(), /local email service/);
  await admin.getByText('Edit the event programme', {exact:true}).click();
  await admin.locator('#edit-topic').fill('Research in progress');
  await admin.locator('#edit-presenters').fill('Ada and Bea');
  await admin.locator('#edit-room').fill('Room 2');
  await admin.locator('#edit-agenda').fill('Research update\nDiscussion');
  await admin.getByRole('button', {name:'Save programme',exact:true}).click();
  await admin.locator('#admin-notice').filter({hasText:'Programme saved.'}).waitFor();
  await admin.getByText('Edit candidate dates and deadline', {exact:true}).click();
  await admin.locator('#edit-deadline').fill('2026-10-08T17:00');
  await admin.getByRole('button', {name:'Review date changes',exact:true}).click();
  await admin.locator('#schedule-effect').filter({hasText:'saved answers and notes will be kept'}).waitFor();
  await admin.getByRole('button', {name:'Save reviewed changes',exact:true}).click();
  await admin.locator('#admin-notice').filter({hasText:'Schedule saved.'}).waitFor();
  await page.reload();
  await page.getByText('Your saved answers are shown below', {exact:false}).waitFor();
  assert.equal(await first().getByLabel('Available', {exact:true}).isChecked(), true);
  assert.equal(await admin.locator('.event-tabs a').count(), 4);
  await admin.getByRole('link',{name:'Seminar 2', exact:true}).click();
  await admin.getByRole('heading',{name:'Seminar 2', exact:true}).waitFor();
  await admin.getByText('0 of 1 people have answered every option', {exact:true}).waitFor();
  assert.equal(await admin.locator('#slot-rows tr').first().locator('.available').textContent(), '0');
  await admin.getByRole('link',{name:'Seminar 1', exact:true}).click();
  await admin.getByText('0 of 1 people have answered every option', {exact:true}).waitFor();
  assert.equal(new URL(admin.url()).hash, '');
  assert.equal(await admin.locator('#slot-rows tr').nth(1).locator('.unknown').textContent(), '1');

  await page.getByRole('button',{name:'None of these work',exact:true}).click();
  await save(); await back();
  await admin.getByRole('button',{name:'Refresh responses'}).click();
  await admin.getByText('No time works for everyone yet.', {exact:false}).waitFor();
  assert.equal(await admin.getByRole('button',{name:'Finalize this time'}).count(), 0);

  await first().getByLabel('Available', {exact:true}).check();
  await save(); await back();
  await mkdir(new URL('../test-results/',import.meta.url),{recursive:true});
  await page.screenshot({path:root+'test-results/desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:root+'test-results/mobile.png',fullPage:true});
  await admin.getByRole('button',{name:'Refresh responses'}).click();
  await admin.getByRole('button',{name:'Finalize this time'}).waitFor();
  await admin.screenshot({path:root+'test-results/admin.png',fullPage:true});
  await admin.setViewportSize({width:390,height:844});
  assert.equal(await admin.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await admin.getByRole('button',{name:'Finalize this time'}).click();
  await admin.getByText('Time finalized.', {exact:false}).waitFor();
  await page.reload();
  await page.getByRole('heading',{name:'Our event time is confirmed.'}).waitFor();
  assert.match(await page.locator('#final-programme').textContent(), /Research in progress.*Ada and Bea.*Room 2.*Research update/s);
  assert.equal(await page.locator('#response-form').isVisible(), false);
  const download = page.waitForEvent('download');
  await page.getByRole('button',{name:'Add confirmed time to calendar'}).click();
  assert.equal((await download).suggestedFilename(),'d3-seminar.ics');

  await page.getByRole('link',{name:'All four events',exact:false}).click();
  await page.locator('[data-event="seminar-1"] .event-metadata').getByText('Confirmed', {exact:true}).waitFor();
  assert.deepEqual(await eventCalendar('seminar-1').locator('button.calendar-date .calendar-day-number').allTextContents(), ['9']);
  assert.equal(await eventCalendar('seminar-1').locator('.vote-mark.vote-available').count(), 1);
  assert.match(await page.locator('[data-event="seminar-1"] .event-programme').textContent(), /Research in progress/);
  assert.equal(await outlineStyle(eventCalendar('seminar-1').locator('button.calendar-date')), 'solid');
  await eventCalendar('seminar-1').locator('button.calendar-date').click();
  assert.equal(await eventCalendar('seminar-1').locator('.calendar-response-form').count(), 0);
  assert.deepEqual(await eventCalendar('seminar-1').locator('.vote-people li').allTextContents(), ['Browser TestAvailable']);
  assert.equal(await eventCalendar('seminar-1').locator('.calendar-votes').textContent().then(text => text.includes('Other mornings')), false);
  await page.getByRole('button',{name:'Close responses for Seminar 1'}).click();
  assert.equal(await eventCalendar('seminar-1').locator('.calendar-note').textContent(), 'Confirmed dates');
  await page.locator('#programme-progress').screenshot({path:root+'test-results/progress-action-mobile.png'});
  await page.setViewportSize({width:1440,height:1080});
  await page.locator('#programme-progress').screenshot({path:root+'test-results/progress-action-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:root+'test-results/home-personal-mobile.png',fullPage:true});

  await navigation().getByRole('link', {name:'Agenda', exact:true}).click();
  const agenda = page.locator('.agenda-event[data-event="seminar-1"]');
  await agenda.getByRole('heading', {name:'Research in progress', exact:true}).waitFor();
  assert.match(await agenda.textContent(), /Ada and Bea.*Room 2.*Research update\nDiscussion/s);
  assert.match(await agenda.locator('.agenda-timing').textContent(), /9 Oct 2026.*14:00–16:00/);
  assert.equal(await agenda.locator('.agenda-date-status').textContent(), 'Confirmed');
  assert.equal(await page.locator('.agenda-open-slot').count(), 5);
  assert.equal(await page.locator('.talk-row').count(), 0);
  assert.equal(await page.locator('#agenda-notice').isVisible(), false);
  assert.equal(new URL(await navigation().getByRole('link', {name:'About', exact:true}).getAttribute('href')).hash, '#token=' + 'T'.repeat(43));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:root+'test-results/agenda-mobile.png',fullPage:true});
  await page.setViewportSize({width:320,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.setViewportSize({width:1440,height:1080});
  await page.screenshot({path:root+'test-results/agenda-desktop.png',fullPage:true});
  await agenda.getByRole('link', {name:'Event details for Seminar 1', exact:true}).click();
  await page.getByRole('heading',{name:'Our event time is confirmed.'}).waitFor();
  assert.equal(new URL(page.url()).hash, '#token=' + 'T'.repeat(43));
  const publicAgenda = await browser.newPage();
  publicAgenda.on('pageerror', error => errors.push(error.message));
  await publicAgenda.goto(details.base + 'agenda.html');
  await publicAgenda.locator('.agenda-event').nth(3).waitFor();
  assert.equal(await publicAgenda.getByText('No public talks yet.', {exact:true}).count(), 4);
  assert.equal(await publicAgenda.getByText('Research in progress', {exact:true}).count(), 0);
  assert.match(await publicAgenda.locator('[data-event="seminar-1"] .agenda-timing').textContent(), /9 Oct 2026.*14:00–16:00/);
  await publicAgenda.getByRole('link', {name:'Event details for Seminar 1', exact:true}).click();
  await publicAgenda.waitForURL(details.base + '?event=seminar-1');
  const confirmedPublic = publicAgenda.locator('[data-event="seminar-1"] button.calendar-date:enabled');
  await confirmedPublic.waitFor();
  assert.equal(await confirmedPublic.count(), 1);
  assert.equal(await outlineStyle(confirmedPublic), 'solid');
  await confirmedPublic.click();
  await publicAgenda.getByText('1 of 1 available', {exact:true}).waitFor();
  assert.doesNotMatch(await publicAgenda.locator('body').textContent(), /Browser Test|Research in progress|Ada and Bea|Room 2/);
  assert.equal(await publicAgenda.locator('.calendar-response-form, input, textarea').count(), 0);
  await publicAgenda.goto(details.base + 'agenda.html#token=' + 'Z'.repeat(43));
  await publicAgenda.locator('#agenda-notice.error').waitFor();
  assert.equal(await publicAgenda.locator('.agenda-event').count(), 0);
  await publicAgenda.close();
  await navigation().getByRole('link', {name:'Programme', exact:true}).click();
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('[data-event="seminar-2"] .event-status').textContent(), 'Awaiting your response');
  await page.locator('[data-event="onsite"] .event-disclosure-summary').click();
  await page.getByRole('link',{name:'Share availability for Onsite meeting', exact:true}).click();
  await page.getByRole('heading',{name:'Onsite meeting',exact:true}).waitFor();
  await page.getByText('Each option covers 2 consecutive dates, 09:00–17:00 each day.', {exact:false}).waitFor();
  assert.equal(await page.locator('input:checked').count(), 0);
  assert.equal(await page.getByLabel('If necessary', {exact:true}).count(), 0);
  assert.match(await page.locator('.poll-option legend').first().textContent(), /2 Nov.*3 Nov.*09:00–17:00 each day/);
  await first().getByLabel('Unavailable').check();
  await save();
  await page.getByText('Your availability is saved for 1 of 3 options.', {exact:false}).waitFor();
  await back();
  assert.equal(await first().getByLabel('Unavailable').isChecked(), true);
  await page.screenshot({path:root+'test-results/onsite-mobile.png',fullPage:true});
  await page.getByRole('link',{name:'All four events',exact:false}).click();
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  const onsiteDays = eventCalendar('onsite').locator('button.calendar-date');
  await page.locator('[data-event="onsite"] .event-disclosure-summary').click();
  assert.equal(await eventCalendar('onsite').locator('button.date-leading').count(), 0);
  for (const index of [0, 1]) {
    assert.equal(await onsiteDays.nth(index).locator('.vote-mark.vote-unavailable').count(), 1);
    await onsiteDays.nth(index).click();
    assert.deepEqual(await eventCalendar('onsite').locator('.vote-people li').allTextContents(), ['Browser TestUnavailable']);
    assert.match(await eventCalendar('onsite').locator('.votes-slot').textContent(), /2 Nov.*3 Nov/);
    assert.deepEqual(await eventCalendar('onsite').locator('.is-pair-selected .calendar-day-number').allTextContents(), ['2', '3']);
  }
  const pairForm = eventCalendar('onsite').locator('.calendar-response-form');
  assert.match(await pairForm.locator('legend').textContent(), /2 Nov.*3 Nov.*09:00–17:00 each day/);
  await pairForm.getByLabel('Available', {exact:true}).check();
  await pairForm.getByRole('button', {name:'Save changes', exact:true}).click();
  await pairForm.getByRole('status').filter({hasText:'Response saved.'}).waitFor();
  for (const index of [0, 1]) assert.equal(await onsiteDays.nth(index).locator('.vote-available').count(), 1);
  assert.equal(await onsiteDays.nth(2).locator('.vote-unknown').count(), 1);
  await page.reload();
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  await page.locator('[data-event="onsite"] .event-disclosure-summary').click();
  await onsiteDays.nth(0).click();
  assert.equal(await pairForm.getByLabel('Available', {exact:true}).isChecked(), true);
  await pairForm.getByLabel('Unavailable', {exact:true}).check();
  await pairForm.getByRole('button', {name:'Save changes', exact:true}).click();
  await pairForm.getByRole('status').filter({hasText:'Response saved.'}).waitFor();
  for (const index of [0, 1]) assert.equal(await onsiteDays.nth(index).locator('.vote-unavailable').count(), 1);
  await page.getByRole('button', {name:'Close responses for Onsite meeting'}).click();
  assert.equal(await eventCalendar('onsite').locator('.is-pair-selected').count(), 0);
  assert.equal(await onsiteDays.nth(2).locator('.vote-mark.vote-unknown').count(), 1);
  await page.goto(details.invitation);
  await page.getByRole('heading',{name:'Our event time is confirmed.'}).waitFor();

  // Continue past finished events, revisit partial replies, and finish on the programme.
  await page.locator('#journey-events').getByRole('link', {name:'Seminar 2',exact:true}).click();
  await page.locator('#journey-progress').filter({hasText:'1 of 4'}).waitFor();
  await first().getByLabel('Available', {exact:true}).check();
  await page.getByRole('button', {name:'Save and continue to Seminar 3',exact:true}).click();
  await page.locator('#event-title').filter({hasText:'Seminar 3'}).waitFor();
  await page.locator('#saved-response').filter({hasText:'Response saved for Seminar 2. 1 of 4 options answered.'}).waitFor();
  assert.match(await page.locator('#journey-progress').textContent(), /1 of 4/);
  assert.equal(new URL(page.url()).hash, '#token=' + 'T'.repeat(43));
  await page.getByRole('button', {name:'None of these work',exact:true}).click();
  await page.getByRole('button', {name:'Save and continue to Onsite meeting',exact:true}).click();
  await page.locator('#journey-progress').filter({hasText:'2 of 4'}).waitFor();
  await page.getByRole('button', {name:'All onsite options work',exact:true}).click();
  await page.getByRole('button', {name:'Save and continue to Seminar 2',exact:true}).click();
  await page.locator('#journey-progress').filter({hasText:'3 of 4'}).waitFor();
  assert.equal(await first().getByLabel('Available', {exact:true}).isChecked(), true);
  await page.getByRole('button', {name:'All times work',exact:true}).click();
  await page.locator('#comment').fill('Keep this note if saving fails.');
  await page.route('**/api/respond', route => route.abort(), {times:1});
  await page.getByRole('button', {name:'Save and return to programme',exact:true}).click();
  await page.locator('#notice.error').filter({hasText:'Could not confirm the save'}).waitFor();
  assert.equal(new URL(page.url()).searchParams.get('event'), 'seminar-2');
  assert.equal(await page.locator('input:checked').count(), 4);
  assert.equal(await page.locator('#comment').inputValue(), 'Keep this note if saving fails.');
  await page.route('**/api/respond', route => route.fulfill({status:400, json:{ok:false, error:'The candidate times have changed. Reopen your invitation and check all options again.'}}), {times:1});
  await page.getByRole('button', {name:'Save and return to programme',exact:true}).click();
  await page.locator('#notice.error').filter({hasText:'candidate times have changed'}).waitFor();
  assert.equal(await page.locator('input:checked').count(), 4);
  await page.getByRole('button', {name:'Save and return to programme',exact:true}).click();
  await page.locator('#journey-progress').filter({hasText:'4 of 4'}).waitFor();
  await page.locator('#saved-response').filter({hasText:'Response saved for Seminar 2. 4 of 4 options answered.'}).waitFor();
  assert.equal(await page.locator('#continue-link').isVisible(), false);
  assert.equal(await page.locator('[data-event="seminar-2"] .event-status').textContent(), 'Your response saved');
  await page.screenshot({path:root+'test-results/programme-complete-mobile.png',fullPage:true});

  await admin.getByRole('link', {name:'Seminar 2',exact:true}).click();
  await admin.locator('#response-count').filter({hasText:'1 of 1'}).waitFor();
  await admin.getByText('Edit candidate dates and deadline', {exact:true}).click();
  await admin.locator('#schedule-options input').first().fill('2026-11-02T14:00');
  await admin.getByRole('button', {name:'Review date changes',exact:true}).click();
  await admin.locator('#schedule-effect').filter({hasText:'start a new round'}).waitFor();
  await admin.getByRole('button', {name:'Save reviewed changes',exact:true}).click();
  await admin.locator('#admin-notice.error').filter({hasText:'Confirm that you want to start a new round'}).waitFor();
  assert.match(await admin.locator('#response-count').textContent(), /1 of 1/);
  await admin.locator('#reset-consent').check();
  await admin.getByRole('button', {name:'Save reviewed changes',exact:true}).click();
  await admin.locator('#admin-notice').filter({hasText:'New dates saved.'}).waitFor();
  assert.match(await admin.locator('#response-count').textContent(), /0 of 1/);
  await admin.setViewportSize({width:1440,height:1080});
  await admin.screenshot({path:root+'test-results/organizer-editors-desktop.png',fullPage:true});
  await admin.setViewportSize({width:390,height:844});
  assert.equal(await admin.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await admin.screenshot({path:root+'test-results/organizer-editors-mobile.png',fullPage:true});
  await page.goto(details.invitation.replace('seminar-1', 'seminar-2'));
  await page.locator('#response-form').waitFor();
  assert.equal(await page.locator('input:checked').count(), 0);
  assert.equal(await page.getByLabel('If necessary', {exact:true}).count(), 0);
  assert.match(await first().locator('legend').textContent(), /2 Nov 2026/);

  await page.goto(details.invitation.replace('T'.repeat(43),'X'.repeat(43)));
  await page.getByText('This invitation has expired or is invalid.',{exact:false}).waitFor();
  assert.equal(await page.locator('#response-form').isVisible(),false);
  await page.goto(details.home.replace('T'.repeat(43), 'X'.repeat(43)));
  await page.getByText('Your invitation could not be loaded.', {exact:false}).waitFor();
  assert.equal(await page.locator('.vote-ring').count(), 0);
  assert.equal(await page.locator('body').textContent().then(text => text.includes('Browser Test')), false);
  await admin.reload();
  await admin.getByText('Open this private dashboard with:', {exact:false}).waitFor();
  assert.equal(await admin.locator('#dashboard').isVisible(),false);

  // Exercise calendar-day boundaries without changing any persisted event data.
  const edgePage = await browser.newPage({viewport:{width:390,height:844}, timezoneId:'America/Los_Angeles'});
  edgePage.on('pageerror', error => errors.push(error.message));
  await edgePage.route('**/config.json', async route => {
    const response = await route.fetch();
    const config = await response.json();
    config.slots = [{id:'berlin-date', start:'2026-10-08T22:00:00Z', end:'2026-10-09T00:00:00Z'}];
    config.events.find(event => event.id === 'seminar-1').slots = config.slots;
    config.events.find(event => event.id === 'seminar-3').slots = [{id:'midnight-end', start:'2026-10-26T22:00:00+01:00', end:'2026-10-27T00:00:00+01:00'}];
    config.events.find(event => event.id === 'onsite').slots = [
      {id:'week-wrap', start:'2026-12-27T09:00:00+01:00', end:'2026-12-28T17:00:00+01:00'},
      {id:'new-year', start:'2026-12-31T09:00:00+01:00', end:'2027-01-01T17:00:00+01:00'}
    ];
    await route.fulfill({response, json:config});
  });
  await edgePage.route('**/api/public-event', route => route.fulfill({status:503, json:{ok:false, error:'Temporarily unavailable'}}));
  await edgePage.goto(details.base);
  await edgePage.locator('.event-card').nth(3).waitFor();
  assert.deepEqual(await edgePage.locator('[data-event="seminar-1"] button.calendar-date').allTextContents(), ['9']);
  assert.deepEqual(await edgePage.locator('[data-event="seminar-3"] button.calendar-date').allTextContents(), ['26']);
  assert.deepEqual(await edgePage.locator('[data-event="onsite"] caption').allTextContents(), ['December 2026', 'January 2027']);
  assert.deepEqual(await edgePage.locator('[data-event="onsite"] button.calendar-date').allTextContents(), ['27', '28', '31', '1']);
  assert.deepEqual(await edgePage.locator('[data-event="onsite"] .pair-wrap-out .calendar-day-number').allTextContents(), ['27', '31']);
  assert.deepEqual(await edgePage.locator('[data-event="onsite"] .pair-wrap-in .calendar-day-number').allTextContents(), ['28', '1']);
  const holidayCalendar = edgePage.locator('[data-event="onsite"]');
  await holidayCalendar.locator('.event-disclosure-summary').click();
  for (const date of ['2026-12-24', '2026-12-28', '2027-01-01', '2027-01-06', '2027-01-09']) {
    assert.equal(await holidayCalendar.locator(`[data-date="${date}"].calendar-day-off`).count(), 1);
  }
  assert.equal(await holidayCalendar.locator('[data-date="2027-01-07"].calendar-day-off').count(), 0);
  const holidayLink = holidayCalendar.locator('[data-date="2027-01-01"] button');
  assert.match(await holidayLink.getAttribute('title'), /New Year’s Day/);
  assert.ok(Number(await holidayLink.evaluate(node => getComputedStyle(node).opacity)) < 1);
  assert.equal(await holidayLink.isDisabled(), true);
  assert.equal(await edgePage.locator('.event-calendar .notice.error').count(), 4);
  assert.equal(await edgePage.locator('input, textarea').count(), 0);
  assert.equal(await edgePage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await holidayCalendar.screenshot({path:root+'test-results/calendar-holidays-mobile.png'});
  await edgePage.close();

  // A synthetic 20-person response view exercises chart density without adding real invitees.
  const votesPage = await browser.newPage({viewport:{width:1440,height:1080}});
  votesPage.on('pageerror', error => errors.push(error.message));
  let voteMode = 'tie';
  await votesPage.route('**/api/invitation', async route => {
    const response = await route.fetch();
    const invitation = await response.json();
    invitation.state = {};
    if (voteMode === 'overlap' && route.request().postDataJSON().event_id === 'onsite') {
      invitation.poll.slots = [
        {...invitation.poll.slots[0], start:'2027-04-14T09:00:00+02:00', end:'2027-04-15T17:00:00+02:00'},
        {...invitation.poll.slots[1], start:'2027-04-15T09:00:00+02:00', end:'2027-04-16T17:00:00+02:00'}
      ];
    }
    invitation.participants = Array.from({length:20}, (_, index) => ({
      name:index === 19 ? '<b>Test Person 20</b>' : `Test Person ${String(index + 1).padStart(2, '0')}`,
      role:index < 5 ? 'professor' : 'student',
      answers:Object.fromEntries(invitation.poll.slots.flatMap((slot, slotIndex) => {
        const answer = ['available', 'unavailable', null, null][(index + slotIndex) % 4];
        return answer ? [[slot.id, answer]] : [];
      }))
    }));
    if (voteMode === 'leader') invitation.participants[1].answers[invitation.poll.slots[0].id] = 'available';
    if (voteMode === 'empty') invitation.participants.forEach(person => { person.answers = {}; });
    if (voteMode === 'final') {
      const slot = invitation.poll.slots[0];
      invitation.state.final_event = {...slot, slot_id:slot.id, daily_hours:invitation.poll.daily_hours};
      invitation.participants.forEach(person => { person.answers[slot.id] = 'available'; });
    }
    await route.fulfill({response, json:invitation});
  });
  await votesPage.goto(details.home);
  await votesPage.getByText('Hello Browser Test.', {exact:false}).waitFor();
  await votesPage.locator('[data-event="onsite"] .event-disclosure-summary').click();
  const votesCard = votesPage.locator('[data-event="seminar-1"]');
  const voteDates = votesCard.locator('button.calendar-date');
  assert.equal(await votesCard.locator('button.date-leading').count(), 4);
  assert.equal(await outlineStyle(voteDates.first()), 'dashed');
  assert.equal(await voteDates.first().locator('.vote-mark').count(), 20);
  for (const [answer, count] of [['available', 5], ['unavailable', 5], ['unknown', 10]]) {
    assert.equal(await voteDates.first().locator('.vote-' + answer).count(), count);
  }
  assert.equal(await voteDates.first().locator('.vote-professor .vote-person').count(), 5);
  assert.equal(await voteDates.first().locator('.vote-student .vote-person').count(), 15);
  const arcLength = selector => voteDates.first().locator(selector).first().evaluate(mark => mark.getTotalLength());
  assert.ok(await arcLength('.vote-professor .vote-mark') > await arcLength('.vote-student .vote-mark'));
  for (const answer of ['available', 'unavailable']) {
    const colour = role => voteDates.first().locator(`.vote-${role} .vote-${answer}`).first().evaluate((mark, answer) => getComputedStyle(mark)[answer === 'available' ? 'fill' : 'stroke'], answer);
    assert.notEqual(await colour('professor'), await colour('student'));
  }
  assert.equal(await voteDates.first().locator('.vote-available').first().evaluate(mark => getComputedStyle(mark).stroke), 'none');
  assert.equal(await voteDates.first().locator('.vote-unavailable').first().evaluate(mark => getComputedStyle(mark).fill), 'none');
  for (const mark of await voteDates.first().locator('.vote-unknown').all()) {
    assert.equal(await mark.evaluate(node => getComputedStyle(node).stroke), 'rgba(0, 0, 0, 0)');
  }
  assert.equal(await voteDates.nth(0).locator('.vote-mark').nth(3).getAttribute('d'), await voteDates.nth(1).locator('.vote-mark').nth(3).getAttribute('d'));
  await voteDates.first().locator('.vote-mark').nth(1).hover();
  assert.equal(await votesCard.locator('.calendar-vote-hint').textContent(), 'Test Person 02 · Professor — Unavailable');
  await votesPage.mouse.move(0, 0);
  await votesCard.screenshot({path:root+'test-results/votes-card-desktop.png'});
  await votesPage.locator('[data-event="onsite"]').screenshot({path:root+'test-results/onsite-pairs-desktop.png'});
  await voteDates.first().click();
  assert.equal(await votesCard.locator('.vote-people li').count(), 20);
  assert.equal(await votesCard.locator('.vote-people b').count(), 0);
  assert.equal(await votesCard.locator('.votes-count').textContent(), '5 of 20 available');
  assert.deepEqual(await votesCard.locator('.vote-breakdown dd').allTextContents(), ['5', '5', '10']);
  await votesPage.setViewportSize({width:390,height:844});
  assert.equal(await votesPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await votesCard.screenshot({path:root+'test-results/votes-card-mobile.png'});
  await votesPage.locator('[data-event="onsite"]').screenshot({path:root+'test-results/onsite-pairs-mobile.png'});
  await votesPage.setViewportSize({width:320,height:844});
  assert.equal(await votesPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);

  voteMode = 'leader';
  await votesPage.reload();
  await votesPage.getByText('Hello Browser Test.', {exact:false}).waitFor();
  assert.equal(await votesCard.locator('button.date-leading').count(), 1);
  assert.equal(await outlineStyle(voteDates.first()), 'dashed');
  assert.equal(await outlineStyle(voteDates.nth(1)), 'dotted');
  const onsiteCard = votesPage.locator('[data-event="onsite"]');
  await onsiteCard.locator('.event-disclosure-summary').click();
  assert.deepEqual(await onsiteCard.locator('button.date-leading .calendar-day-number').allTextContents(), ['2', '3']);
  assert.equal(await outlineStyle(onsiteCard.locator('.calendar-pair').nth(0), '::after'), 'dashed');
  assert.equal(await outlineStyle(onsiteCard.locator('.calendar-pair').nth(1), '::after'), 'dashed');
  assert.equal(await outlineStyle(onsiteCard.locator('.calendar-pair').nth(2), '::after'), 'dotted');
  await votesPage.setViewportSize({width:1440,height:1080});
  await votesCard.screenshot({path:root+'test-results/votes-card-desktop.png'});
  await onsiteCard.screenshot({path:root+'test-results/onsite-pairs-desktop.png'});
  await votesPage.setViewportSize({width:390,height:844});
  assert.equal(await votesPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await onsiteCard.screenshot({path:root+'test-results/onsite-pairs-mobile.png'});

  voteMode = 'empty';
  await votesPage.reload();
  await votesPage.getByText('Hello Browser Test.', {exact:false}).waitFor();
  assert.equal(await votesPage.locator('.event-grid button.date-leading').count(), 0);
  assert.equal(await outlineStyle(voteDates.first()), 'dotted');

  voteMode = 'final';
  await votesPage.reload();
  await votesPage.getByText('Hello Browser Test.', {exact:false}).waitFor();
  await onsiteCard.locator('.event-disclosure-summary').click();
  assert.equal(await onsiteCard.locator('button.calendar-date').count(), 2);
  assert.equal(await outlineStyle(onsiteCard.locator('button.calendar-date').first()), 'solid');
  assert.equal(await outlineStyle(onsiteCard.locator('.calendar-pair').first(), '::after'), 'solid');
  assert.equal(await votesPage.locator('.event-grid button.date-leading').count(), 0);
  await onsiteCard.screenshot({path:root+'test-results/onsite-confirmed-mobile.png'});

  // A date shared by two onsite options retains each person's separate answers.
  voteMode = 'overlap';
  await votesPage.reload();
  await votesPage.getByText('Hello Browser Test.', {exact:false}).waitFor();
  await onsiteCard.locator('.event-disclosure-summary').click();
  const sharedDay = onsiteCard.locator('button.calendar-date').filter({has: votesPage.locator('.calendar-day-number', {hasText:/^15$/})});
  const sharedPerson = sharedDay.locator('.vote-professor .vote-person').first();
  assert.equal(await sharedPerson.locator('.vote-available').count(), 1);
  assert.equal(await sharedPerson.locator('.vote-unavailable').count(), 1);
  assert.equal(await sharedDay.locator('.vote-person').count(), 20);
  await sharedDay.click();
  assert.equal(await onsiteCard.locator('.calendar-response-form fieldset').count(), 2);
  assert.match(await onsiteCard.locator('.calendar-response-form legend').nth(0).textContent(), /14 Apr.*15 Apr/);
  assert.match(await onsiteCard.locator('.calendar-response-form legend').nth(1).textContent(), /15 Apr.*16 Apr/);
  assert.equal(await onsiteCard.locator('.votes-slot').count(), 2);
  assert.deepEqual(await onsiteCard.locator('.is-pair-selected .calendar-day-number').allTextContents(), ['14', '15', '16']);
  assert.match(await onsiteCard.locator('.vote-people').nth(0).locator('li').first().textContent(), /Available$/);
  assert.match(await onsiteCard.locator('.vote-people').nth(1).locator('li').first().textContent(), /Unavailable$/);
  await onsiteCard.screenshot({path:root+'test-results/onsite-overlapping-mobile.png'});
  await votesPage.close();
  assert.deepEqual(errors,[]);
  console.log('Browser checks passed: calendar response breakdowns, organiser date edits, deadline preservation, reviewed new rounds, private email previews, confirmed programmes, four-event save flow, private access, radial vote charts, connected onsite dates, mobile layout, calendar download.');
} catch (error) {
  if (page) console.error('Browser test page:', await page.locator('body').innerText());
  throw error;
} finally {
  await browser?.close();
  fixture.kill('SIGINT');
}
