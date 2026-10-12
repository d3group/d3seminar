import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir} from 'node:fs/promises';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';

const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const fixture = spawn('python3', ['tests/browser_fixture.py', '--onsite-half-day'], {cwd:root, stdio:['ignore', 'pipe', 'inherit']});
let browser;
try {
  const details = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Fixture did not start')), 10000);
    createInterface({input:fixture.stdout}).once('line', line => { clearTimeout(timeout); resolve(JSON.parse(line)); });
    fixture.once('exit', code => { clearTimeout(timeout); reject(new Error(`Fixture exited: ${code}`)); });
  });
  browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1440, height:1080}, timezoneId:'America/Los_Angeles'});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const card = page.locator('[data-event="onsite"]');
  await page.goto(details.base);
  await card.locator('.event-disclosure-summary').click();
  await card.locator('.calendar-option').first().waitFor();
  assert.equal(await card.locator('.calendar-month .calendar-option').count(), 6);
  assert.equal(await card.locator('.onsite-options').count(), 0);
  assert.equal(await card.locator('.calendar-option-half').count(), 6);
  assert.ok((await card.boundingBox()).height < 800);
  assert.match(await card.locator('.event-metadata').textContent(), /2½ days/);
  await page.goto(details.home);
  await card.locator('.event-disclosure-summary').click();
  const option = () => card.locator('td[data-date="2027-04-12"] button.calendar-date');
  const track = () => card.locator('.calendar-option').first();
  await option().waitFor();
  assert.match(await track().getAttribute('aria-label'), /12 Apr.*14 Apr.*final day 09:00–13:00/);
  assert.equal(await track().locator('.calendar-option-half').textContent(), '½');
  const lineStyle = () => track().evaluate(node => getComputedStyle(node, '::before').borderTopStyle);
  const positions = await card.locator('.calendar-option').evaluateAll(nodes => nodes.slice(0, 3).map(node => {
    const style = getComputedStyle(node);
    return {column:style.gridColumnStart, colour:style.color};
  }));
  assert.deepEqual(positions.map(p => p.column), ['1', '2', '3']);
  assert.equal(new Set(positions.map(p => p.colour)).size, 3);
  assert.equal(await lineStyle(), 'dotted');
  await option().focus(); await page.keyboard.press('Enter');
  const form = () => card.locator('.calendar-response-form');
  assert.equal(await form().locator('fieldset').count(), 1);
  assert.deepEqual(await card.locator('td.is-pair-selected').evaluateAll(cells => cells.map(cell => cell.dataset.date)), ['2027-04-12', '2027-04-13', '2027-04-14']);
  await form().getByLabel('Available', {exact:true}).check();
  await form().getByRole('button', {name:'Save changes', exact:true}).click();
  await form().getByRole('status').filter({hasText:'Response saved.'}).waitFor();
  assert.equal(await form().locator('fieldset').count(), 1);
  assert.equal(await option().getAttribute('aria-expanded'), 'true');
  assert.equal(await lineStyle(), 'dashed');
  await page.keyboard.press('Escape');
  assert.equal(await option().evaluate(node => node === document.activeElement), true);
  await page.reload();
  await card.locator('.event-disclosure-summary').click();
  await option().click();
  assert.equal(await form().getByLabel('Available', {exact:true}).isChecked(), true);
  await card.locator('td[data-date="2027-04-14"] button').click();
  assert.equal(await form().locator('fieldset').count(), 3);
  await page.keyboard.press('Escape');
  await mkdir(root + 'test-results', {recursive:true});
  await card.screenshot({path:root + 'test-results/onsite-desktop.png'});
  for (const width of [390, 320]) {
    await page.setViewportSize({width, height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await option().click();
    assert.equal(await form().locator('fieldset').count(), 1);
    await page.keyboard.press('Escape');
  }
  await card.screenshot({path:root + 'test-results/onsite-mobile.png'});
  await page.goto(details.onsite);
  await page.locator('.poll-option').first().waitFor();
  assert.match(await page.locator('#poll-help').textContent(), /final day 09:00–13:00/);
  assert.match(await page.locator('.poll-option').first().textContent(), /final day 09:00–13:00/);
  const token = 'T'.repeat(43);
  const invite = await (await page.request.post(details.base + 'api/invitation', {data:{token, event_id:'onsite'}})).json();
  assert.deepEqual(invite.answers, {'option-12':'available'});
  const admin = {secret:'browser-test-only-'.repeat(3), event_id:'onsite'};
  const dashboard = await (await page.request.post(details.base + 'api/admin', {data:{...admin, action:'dashboard'}})).json();
  const finalized = await (await page.request.post(details.base + 'api/admin', {data:{...admin, action:'finalize', slot_id:'option-12', snapshot:dashboard.snapshot}})).json();
  assert.equal(finalized.ok, true);
  await page.goto(details.home);
  await card.locator('.event-disclosure-summary').click();
  await card.locator('.calendar-option.date-confirmed').waitFor();
  assert.equal(await card.locator('.calendar-option').count(), 1);
  assert.equal(await lineStyle(), 'solid');
  await option().click();
  assert.equal(await form().count(), 0);
  assert.equal(await card.locator('.calendar-option-half').count(), 1);
  await page.goto(details.base + '?event=onsite');
  await card.locator('.calendar-option.date-confirmed').waitFor();
  assert.equal(await card.locator('.calendar-option').count(), 1);
  assert.equal(await card.locator('.calendar-option-half').count(), 1);
  await card.locator('button.calendar-date:enabled').first().click();
  await card.getByText('1 of 1 available', {exact:true}).waitFor();
  assert.equal(await card.locator('.calendar-response-form, .vote-people, .vote-ring').count(), 0);
  await page.route('**/config.json', async route => {
    const response = await route.fetch();
    const config = await response.json();
    config.api_url = '';
    config.backend_url = '';
    config.events.find(event => event.id === 'onsite').slots = [
      {id:'week-wrap', start:'2027-04-18T09:00:00+02:00', end:'2027-04-20T13:00:00+02:00'},
      {id:'month-wrap', start:'2027-04-30T09:00:00+02:00', end:'2027-05-02T13:00:00+02:00'}
    ];
    await route.fulfill({response, json:config});
  });
  await page.goto(details.base);
  await card.locator('.event-disclosure-summary').click();
  await card.locator('.calendar-option-half').first().waitFor();
  assert.equal(await card.locator('.calendar-option').count(), 4);
  assert.equal(await card.locator('.option-continues-in').count(), 2);
  assert.equal(await card.locator('.option-continues-out').count(), 2);
  assert.equal(await card.locator('.calendar-option-half').count(), 2);
  assert.deepEqual(await card.locator('.calendar-option-half').evaluateAll(nodes => nodes.map(node => node.parentElement.getAttribute('aria-label').match(/final day (\d+:\d+–\d+:\d+)/)[1])), ['09:00–13:00', '09:00–13:00']);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(errors, []);
  console.log('Onsite: compact calendar options, final morning, independent votes, save/reload, mobile and finalization passed.');
} finally {
  await browser?.close();
  fixture.kill('SIGINT');
}
