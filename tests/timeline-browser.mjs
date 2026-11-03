import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir} from 'node:fs/promises';
import {chromium} from 'playwright';

const fixture = spawn('python3', ['tests/browser_fixture.py', '--pages', '--presentations', '--onsite-half-day'], {stdio:['ignore', 'pipe', 'inherit']});
let browser;
try {
  const details = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Timeline fixture did not start')), 15000);
    createInterface({input:fixture.stdout}).once('line', line => {clearTimeout(timer); resolve(JSON.parse(line));});
    fixture.once('exit', code => {clearTimeout(timer); reject(new Error(`Fixture exited: ${code}`));});
  });
  browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1440, height:1080}});
  page.setDefaultTimeout(10000);
  await page.clock.setFixedTime(new Date('2026-10-01T12:00:00Z'));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const shown = async ids => {
    await page.waitForFunction(ids => {
      const track = document.querySelector('.timeline-track');
      if (!track) return false;
      const box = track.getBoundingClientRect();
      const cards = [...track.children].filter(card => {
        const rect = card.getBoundingClientRect();
        return rect.left >= box.left - 2 && rect.right <= box.right + 2;
      });
      return JSON.stringify(cards.map(card => card.dataset.event)) === JSON.stringify(ids);
    }, ids);
    await page.waitForFunction(() => {
      const track = document.querySelector('.timeline-track');
      return Math.abs(track.getBoundingClientRect().height - parseFloat(track.style.height)) < 1;
    });
  };
  const verticalAgenda = async () => {
    await page.locator('#agenda-list > article').nth(3).waitFor({state:'visible'});
    assert.equal(await page.locator('.event-timeline, .timeline-track, .timeline-arrows').count(), 0);
    const cards = await page.locator('#agenda-list > article').evaluateAll(nodes => nodes.map(node => {
      const rect = node.getBoundingClientRect();
      return {x:rect.x, y:rect.y, bottom:rect.bottom, width:rect.width, inert:node.inert};
    }));
    assert.ok(cards.every((card, index) => !card.inert && card.x === cards[0].x && card.width === cards[0].width
      && (!index || card.y > cards[index - 1].bottom)));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  };
  await mkdir('test-results', {recursive:true});
  await page.goto(details.home);
  await page.locator('#home-greeting').waitFor({state:'visible'});
  await shown(['seminar-1', 'seminar-2']);
  assert.deepEqual(await page.locator('.event-disclosure').evaluateAll(nodes => nodes.map(node => node.open)), [true, false, false, false]);
  const cardBox = await page.locator('[data-event="seminar-2"]').boundingBox();
  const arrowBox = await page.getByRole('button', {name:'Later events', exact:true}).boundingBox();
  assert.ok(arrowBox.x >= cardBox.x + cardBox.width && arrowBox.y > cardBox.y && arrowBox.y + arrowBox.height < cardBox.y + cardBox.height);
  assert.equal(await page.getByRole('button', {name:'Earlier events', exact:true}).getAttribute('aria-disabled'), 'true');
  const first = page.locator('[data-event="seminar-1"]');
  // Programme dates navigate to the same calendar at a larger size, on desktop and mobile.
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height:844});
    await shown(width === 390 ? ['seminar-1'] : ['seminar-1', 'seminar-2']);
    const date = first.locator('button.calendar-date').first();
    const small = await date.boundingBox();
    await date.click();
    await page.locator('#availability-calendar .calendar-month').waitFor();
    assert.equal(new URL(page.url()).pathname.split('/').at(-1), 'event.html');
    assert.equal(new URL(page.url()).searchParams.get('event'), 'seminar-1');
    const large = await page.locator('#availability-calendar button.calendar-date').first().boundingBox();
    assert.ok(large.width > small.width);
    assert.equal(await page.locator('.selected-date-option').count(), 1);
    const cards = await page.locator('#poll-options .poll-option').evaluateAll(nodes => nodes.map(node => ({y:node.getBoundingClientRect().y, width:node.clientWidth, content:node.scrollWidth})));
    if (width === 1440) assert.equal(new Set(cards.slice(0, 4).map(card => card.y)).size, 1);
    assert.ok(cards.every(card => card.content <= card.width));
    const responses = await page.locator('#availability-responses').boundingBox();
    const privacy = await page.locator('#response-form .privacy').boundingBox();
    assert.ok(responses.y >= privacy.y + privacy.height);
    assert.equal(await page.locator('#greeting strong em').textContent(), 'Browser');
    const choice = page.locator('#poll-options .poll-option').nth(1);
    const heading = choice.getByRole('button', {name:/Show responses/});
    await heading.click();
    assert.equal(await heading.getAttribute('aria-expanded'), 'true');
    assert.equal(await choice.locator('input:checked').count(), 0);
    await choice.getByLabel('Available', {exact:true}).check();
    assert.equal(await heading.getAttribute('aria-expanded'), 'true');
    await choice.getByRole('button', {name:/Clear answer/}).click();
    assert.equal(await heading.getAttribute('aria-expanded'), 'true');
    await heading.focus(); await page.keyboard.press('Enter');
    assert.equal(await heading.getAttribute('aria-expanded'), 'false');
    await choice.click({position:{x:3, y:3}});
    assert.equal(await heading.getAttribute('aria-expanded'), 'true');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path:`test-results/availability-calendar-${width}.png`, fullPage:true});
    await page.getByRole('link', {name:'All four events', exact:true}).click();
    await first.locator('.calendar-month').waitFor();
  }
  await page.setViewportSize({width:1440, height:1080});
  await shown(['seminar-1', 'seminar-2']);
  await page.getByRole('button', {name:'Later events', exact:true}).click();
  await shown(['seminar-3', 'onsite']);
  assert.equal(await page.getByRole('button', {name:'Later events', exact:true}).getAttribute('aria-disabled'), 'true');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.closest('[data-event]')?.dataset.event), 'seminar-3');
  await page.screenshot({path:'test-results/timeline-later.png', fullPage:true});
  await page.getByRole('button', {name:'Earlier events', exact:true}).click();
  await shown(['seminar-1', 'seminar-2']);
  await page.screenshot({path:'test-results/timeline-programme.png', fullPage:true});
  await page.getByRole('button', {name:'Later events', exact:true}).focus();
  await page.keyboard.press('End');
  await shown(['seminar-3', 'onsite']);
  await page.locator('[data-event="onsite"] > details > summary').click();
  await page.getByRole('link', {name:'View agenda for Onsite meeting', exact:true}).click();
  await verticalAgenda();
  await page.locator('#agenda-onsite:focus').waitFor();
  await page.locator('[data-event="onsite"]').getByRole('button', {name:'Add a talk', exact:true}).click();
  await page.getByLabel('Talk title', {exact:true}).fill('A later research discussion');
  await page.getByLabel('Presentation (minutes)', {exact:true}).fill('20');
  await page.getByLabel('Questions (minutes)', {exact:true}).fill('10');
  await page.getByRole('button', {name:'Save talk', exact:true}).click();
  await page.locator('#presentation-status.success').waitFor();
  await page.getByRole('button', {name:'View in agenda', exact:true}).click();
  await verticalAgenda();
  await page.locator('.talk-table').getByText('A later research discussion', {exact:true}).waitFor();
  assert.equal(await page.locator('[data-event="onsite"] .talk-table').evaluate(node => getComputedStyle(node).display), 'table');
  await page.screenshot({path:'test-results/agenda-vertical.png', fullPage:true});
  await page.setViewportSize({width:390, height:844});
  await verticalAgenda();
  await page.screenshot({path:'test-results/agenda-vertical-mobile.png', fullPage:true});
  await page.goto(details.base + '?event=onsite');
  await shown(['onsite']);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({path:'test-results/timeline-mobile.png', fullPage:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.getByRole('button', {name:'Earlier events', exact:true}).click();
  await shown(['seminar-3']);
  assert.equal(await page.locator('.timeline-track').evaluate(node => getComputedStyle(node).scrollBehavior), 'auto');
  await page.setViewportSize({width:1440, height:1080});
  await shown(['seminar-3', 'onsite']);
  await page.goto(details.base + 'agenda.html');
  await verticalAgenda();
  assert.deepEqual(await page.locator('.event-disclosure').evaluateAll(nodes => nodes.map(node => node.open)), [true, false, false, false]);
  await page.goto(details.base + '?event=onsite');
  await shown(['seminar-3', 'onsite']);
  assert.equal(await page.locator('.calendar-response-form, [data-present]').count(), 0);
  // Closing availability preserves saved choices, while the agenda remains editable.
  const api = async (action, data) => {
    const response = await page.request.post(details.api + 'api/' + action, {
      headers:{Accept:'application/json'}, ...(action === 'respond' ? {form:data} : {data})});
    const result = await response.json();
    assert.equal(response.ok(), true, result.error);
    return result;
  };
  const token = 'T'.repeat(43), event_id = 'seminar-1';
  const invite = await api('invitation', {token, event_id});
  const saved = await api('respond', {token, event_id, revision:invite.revision, response_version:invite.response_version,
    comment:'Preserve this note.', ['answer:' + invite.poll.slots[0].id]:'available'});
  for (const [person, title] of [['T', 'First proposal'], ['P', 'Second proposal']]) {
    await api('presentation', {event_id, token:person.repeat(43), version:'', presentation:{title, visibility:'public'}});
  }
  const secret = new URLSearchParams(new URL(details.admin).hash.slice(1)).get('key');
  const denied = await page.request.post(details.api + 'api/admin', {data:{action:'close_availability', token, event_id}});
  assert.equal(denied.status(), 403);
  const dashboard = await api('admin', {action:'dashboard', event_id, secret});
  await api('admin', {action:'close_availability', event_id, secret, snapshot:dashboard.snapshot});
  const rejected = await page.request.post(details.api + 'api/respond', {headers:{Accept:'application/json'}, form:{event_id, token, revision:invite.revision,
    partial:'true', ['answer:' + invite.poll.slots[0].id]:'unavailable'}});
  assert.equal(rejected.ok(), false);
  assert.match((await rejected.json()).error, /closed/);
  const after = await api('invitation', {token, event_id});
  for (const key of ['answers', 'comment', 'updated_at', 'response_version']) assert.deepEqual(after[key], saved[key]);
  await page.goto(details.invitation);
  await page.locator('#status-pill').filter({hasText:'Closed'}).waitFor();
  assert.equal(await page.locator('#poll-options input:enabled').count(), 0);
  assert.equal(await page.locator('#poll-options input:checked').count(), 1);
  assert.equal(await page.locator('#poll-options .clear-answer:visible').count(), 0);
  assert.equal(await page.locator('#all-available').isVisible(), false);
  assert.equal(await page.locator('#submit-button').isVisible(), false);
  assert.equal(await page.locator('#comment').inputValue(), 'Preserve this note.');
  assert.equal(await page.locator('#comment').getAttribute('readonly'), '');
  const assertClosedCalendar = async calendar => {
    assert.equal(await calendar.locator('button.calendar-date:enabled').count(), 0);
    assert.ok(await calendar.locator('button.calendar-date:disabled').count() > 0);
    assert.equal(await calendar.locator('button.calendar-date[aria-expanded]').count(), 0);
    assert.equal(await calendar.locator('table').first().evaluate(table => getComputedStyle(table).filter), 'grayscale(1)');
  };
  await assertClosedCalendar(page.locator('#availability-calendar'));
  assert.ok(await page.locator('#availability-calendar .vote-mark.vote-available').count() > 0);
  await page.locator('#poll-options .response-toggle').first().click();
  await page.locator('#availability-responses .calendar-votes:not([hidden])').waitFor();
  await page.goto(details.home);
  await first.getByText('Second proposal', {exact:false}).waitFor();
  assert.equal(await first.locator('.agenda-preview-talk').count(), 2);
  assert.equal(await first.getByRole('link', {name:'View responses for Seminar 1'}).count(), 1);
  await assertClosedCalendar(first.locator('.event-calendar'));
  assert.ok(await first.locator('.vote-mark.vote-available').count() > 0);
  assert.match(await page.locator('#continue-link').textContent(), /Seminar 2/);
  const previewBox = await first.locator('.agenda-preview').boundingBox();
  const calendarBox = await first.locator('.event-calendar').boundingBox();
  assert.ok(previewBox.y + previewBox.height <= calendarBox.y);
  await page.screenshot({path:'test-results/closed-availability-programme.png', fullPage:true});
  await page.goto(details.base);
  await first.locator('.agenda-preview-talk').nth(1).waitFor();
  await assertClosedCalendar(first.locator('.event-calendar'));
  assert.doesNotMatch(await first.locator('.agenda-preview').textContent(), /First proposal|Second proposal|example.com/);
  // Archive November after moving its talks; saved votes and old invitation links survive.
  let source = await api('invitation', {token, event_id, include_presentations:true});
  for (const talk of [...source.presentations]) {
    const moved = await api('admin', {action:'move_presentation', secret, token, event_id,
      destination_event_id:'seminar-2', participant_id:talk.participant_id, version:source.presentation_order_version});
    source = moved.source;
  }
  const archiveDashboard = await api('admin', {action:'dashboard', secret, event_id});
  await api('admin', {action:'archive_event', secret, event_id, successor_event_id:'seminar-2', snapshot:archiveDashboard.snapshot});
  const archived = await api('invitation', {token, event_id});
  assert.deepEqual(archived.answers, saved.answers);
  assert.equal(archived.comment, saved.comment);
  await page.goto(details.home);
  const december = page.locator('.event-card[data-event="seminar-2"]');
  await december.locator('.agenda-preview-talk').nth(1).waitFor();
  assert.equal(await page.locator('.event-card[data-event="seminar-1"]').count(), 0);
  assert.equal(await page.locator('.event-card').count(), 3);
  assert.equal(await december.locator('.next-seminar-note').count(), 1);
  await page.goto(details.invitation);
  await page.locator('#poll-options input').first().waitFor();
  assert.equal(new URL(page.url()).searchParams.get('event'), 'seminar-2');
  await page.goto(details.base + 'agenda.html?event=seminar-1#token=' + token);
  await page.locator('#agenda-list').getByText('First proposal', {exact:true}).waitFor();
  assert.equal(await page.locator('#agenda-list [data-event="seminar-1"]').count(), 0);
  assert.equal(await page.locator('#agenda-list').getByText('Second proposal', {exact:true}).count(), 1);
  const onsiteInvite = await api('invitation', {token, event_id:'onsite'});
  const disabledIds = onsiteInvite.poll.slots.slice(3).map(slot => slot.id);
  assert.ok(disabledIds.length > 0);
  await api('respond', {token, event_id:'onsite', revision:onsiteInvite.revision, response_version:onsiteInvite.response_version,
    comment:'Keep the later April choice.', ['answer:' + disabledIds[0]]:'unavailable'});
  const onsiteDashboard = await api('admin', {action:'dashboard', secret, event_id:'onsite'});
  await api('admin', {action:'disable_options', secret, event_id:'onsite', slot_ids:disabledIds, snapshot:onsiteDashboard.snapshot});
  await page.goto(details.base + 'event.html?event=onsite#token=' + token);
  await page.locator('#poll-options .poll-option.is-disabled').first().waitFor();
  assert.equal(await page.locator('#poll-options fieldset:not(:disabled)').count(), 3);
  const disabledCards = page.locator('#poll-options fieldset:disabled');
  assert.equal(await disabledCards.count(), disabledIds.length);
  assert.equal(await disabledCards.locator('input:checked').count(), 1);
  assert.ok(await page.locator('.calendar-option-disabled button.calendar-date:disabled').count() > 0);
  assert.ok(await page.locator('.calendar-option-disabled .vote-unavailable').count() > 0);
  await page.locator('#all-available').click();
  assert.equal(await disabledCards.locator('input[value="available"]:checked').count(), 0);
  assert.equal(await page.locator('#poll-options input:checked:not(:disabled)').count(), 3);
  await page.locator('#save-later').click();
  await page.locator('#status-pill').filter({hasText:'Saved'}).waitFor();
  const onsiteSaved = await api('invitation', {token, event_id:'onsite'});
  assert.equal(onsiteSaved.answers[disabledIds[0]], 'unavailable');
  assert.equal(onsiteSaved.comment, 'Keep the later April choice.');
  assert.equal(await disabledCards.locator('input:checked').count(), 1);
  for (const confirmedId of ['seminar-2', 'seminar-3']) {
    const before = await api('invitation', {token, event_id:confirmedId});
    const dashboard = await api('admin', {action:'dashboard', secret, event_id:confirmedId});
    const chosen = before.poll.slots[0].id;
    await api('admin', {action:'finalize', secret, event_id:confirmedId, slot_id:chosen,
      snapshot:dashboard.snapshot, organizer_confirmed:true});
    const after = await api('invitation', {token, event_id:confirmedId});
    assert.deepEqual(after.answers, before.answers);
    assert.equal(after.comment, before.comment);
    assert.deepEqual(after.poll, before.poll);
    for (const address of [details.home, details.base, details.base + 'event.html?event=' + confirmedId + '#token=' + token]) {
      await page.goto(address);
      const calendar = address.includes('event.html') ? page.locator('#availability-calendar')
        : page.locator(`.event-card[data-event="${confirmedId}"] .event-calendar`);
      await calendar.locator('button.date-confirmed').waitFor({state:'attached'});
      assert.equal(await calendar.locator('button.date-confirmed').count(), 1);
      assert.equal(await calendar.locator('button.calendar-date:disabled').count(), before.poll.slots.length - 1);
      const shadow = await calendar.locator('button.date-confirmed').evaluate(node => getComputedStyle(node).boxShadow);
      assert.notEqual(shadow, 'none');
      assert.equal(await calendar.locator('button.calendar-date:enabled').count(), 1);
    }
  }
  assert.deepEqual(errors, []);
  console.log('Programme carousel and vertical Agenda: layout, navigation, drafts, saved responses, deep links, talk editing, mobile and reduced motion passed.');
} finally {
  await browser?.close();
  fixture.kill('SIGINT');
}
