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
  page = await browser.newPage({viewport:{width:1440,height:1080}});
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(details.base);
  await page.getByRole('button',{name:'Try the availability poll'}).click();
  await page.getByRole('button',{name:'All times work',exact:true}).click();
  await page.getByRole('button',{name:'Preview my availability'}).click();
  await page.getByText('This was a demo. Nothing was sent or saved.').waitFor();

  // A legacy email link still opens the current poll, without trusting stale fragment details.
  await page.goto(details.invitation);
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  assert.equal(await page.locator('input:checked').count(), 0);
  assert.equal(await page.locator('#calendar').isVisible(), false);
  const first = () => page.locator('.poll-option').first();
  const save = async () => {
    await Promise.all([page.waitForURL('**/api/respond'),page.getByRole('button',{name:'Save my availability'}).click()]);
    await page.getByRole('heading',{name:'Response saved.'}).waitFor();
  };
  const back = async () => {
    await page.getByRole('link',{name:'Return to the seminar'}).click();
    await page.getByText('Your saved answers are shown below', {exact:false}).waitFor();
  };
  await first().getByLabel('Available', {exact:true}).check();
  await page.locator('#comment').fill('Other mornings could work too.');
  await save();
  await page.getByText('Your availability is saved for 1 of 4 options.', {exact:false}).waitFor();
  await back();
  assert.equal(await first().getByLabel('Available', {exact:true}).isChecked(), true);
  assert.equal(await page.locator('#comment').inputValue(), 'Other mornings could work too.');
  const admin = await browser.newPage({viewport:{width:1440,height:1080}});
  admin.setDefaultTimeout(15000);
  admin.on('pageerror', error => errors.push(error.message));
  await admin.goto(details.admin);
  await admin.getByText('0 of 1 people have answered every option', {exact:true}).waitFor();
  assert.equal(new URL(admin.url()).hash, '');
  assert.equal(await admin.locator('#slot-rows tr').nth(1).locator('.unknown').textContent(), '1');

  await page.getByRole('button',{name:'None of these work',exact:true}).click();
  await save(); await back();
  await admin.getByRole('button',{name:'Refresh responses'}).click();
  await admin.getByText('No time works for everyone yet.', {exact:false}).waitFor();
  assert.equal(await admin.getByRole('button',{name:'Finalize this time'}).count(), 0);

  await first().getByLabel('If necessary').check();
  await save(); await back();
  await admin.getByRole('button',{name:'Refresh responses'}).click();
  await admin.getByRole('button',{name:'Ask to reconfirm'}).click();
  await admin.getByText('Reconfirmation requested.', {exact:false}).waitFor();
  await page.reload();
  await page.getByText('Can you commit to', {exact:false}).waitFor();
  assert.equal(await page.locator('.needs-confirmation').count(), 1);
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
  await page.getByRole('heading',{name:'Our seminar time is confirmed.'}).waitFor();
  assert.equal(await page.locator('#response-form').isVisible(), false);
  const download = page.waitForEvent('download');
  await page.getByRole('button',{name:'Add confirmed time to calendar'}).click();
  assert.equal((await download).suggestedFilename(),'d3-seminar.ics');

  await page.goto(details.invitation.replace('T'.repeat(43),'X'.repeat(43)));
  await page.getByText('This invitation has expired or is invalid.',{exact:false}).waitFor();
  assert.equal(await page.locator('#response-form').isVisible(),false);
  await admin.reload();
  await admin.getByText('Open this private dashboard with:', {exact:false}).waitFor();
  assert.equal(await admin.locator('#dashboard').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log('Browser checks passed: demo, legacy link, partial save, persistence, no common time, conditional reconfirmation, private dashboard, finalization, mobile layout, calendar download.');
} catch (error) {
  if (page) console.error('Browser test page:', await page.locator('body').innerText());
  throw error;
} finally {
  await browser?.close();
  fixture.kill('SIGINT');
}
