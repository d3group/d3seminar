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
  // Opening and switching response panels must preserve the calendar DOM and viewport.
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height:844});
    await shown(width === 390 ? ['seminar-1'] : ['seminar-1', 'seminar-2']);
    const dates = first.locator('button.calendar-date');
    await dates.first().evaluate(node => node.scrollIntoView({block:'center', behavior:'instant'}));
    const calendar = await first.locator('.calendar-month').first().elementHandle();
    const position = () => page.evaluate(() => ({y:scrollY, x:document.querySelector('.timeline-track').scrollLeft}));
    const before = await position();
    for (const date of [dates.first(), dates.nth(1), dates.nth(1)]) {
      await date.click();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.deepEqual(await position(), before);
      assert.equal(await calendar.evaluate(node => node.isConnected), true);
    }
    await calendar.dispose();
  }
  await page.setViewportSize({width:1440, height:1080});
  await shown(['seminar-1', 'seminar-2']);
  await first.locator('button.calendar-date').first().click();
  await first.getByLabel('Available', {exact:true}).check();
  await page.getByRole('button', {name:'Later events', exact:true}).click();
  await shown(['seminar-3', 'onsite']);
  assert.equal(await page.getByRole('button', {name:'Later events', exact:true}).getAttribute('aria-disabled'), 'true');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.closest('[data-event]')?.dataset.event), 'seminar-3');
  await page.screenshot({path:'test-results/timeline-later.png', fullPage:true});
  await page.getByRole('button', {name:'Earlier events', exact:true}).click();
  await shown(['seminar-1', 'seminar-2']);
  assert.equal(await first.getByLabel('Available', {exact:true}).isChecked(), true);
  await first.getByRole('button', {name:'Save changes', exact:true}).click();
  await first.locator('.calendar-save-status').filter({hasText:'Response saved.'}).waitFor();
  await first.getByRole('button', {name:'Close responses for Seminar 1'}).click();
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
  assert.deepEqual(errors, []);
  console.log('Programme carousel and vertical Agenda: layout, navigation, drafts, saved responses, deep links, talk editing, mobile and reduced motion passed.');
} finally {
  await browser?.close();
  fixture.kill('SIGINT');
}
