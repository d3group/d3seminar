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
  await page.getByRole('button',{name:'Try the response form'}).click();
  await page.getByLabel('Yes, this works').check();
  await page.getByRole('button',{name:'Preview my response'}).click();
  await page.getByText('This was a demo. Nothing was sent or saved.').waitFor();

  await page.goto(details.invitation);
  await page.getByText('Hello Browser Test.', {exact:false}).waitFor();
  await page.getByLabel('I need another time').check();
  await page.getByRole('button',{name:'Send my response'}).click();
  assert.equal(await page.locator('#comment').evaluate(node=>node.validity.valueMissing), true);
  await page.getByLabel('A note for the organizer').fill('Could we use Friday morning instead?');
  await Promise.all([page.waitForURL('**/api/respond'),page.getByRole('button',{name:'Send my response'}).click()]);
  await page.getByRole('heading',{name:'Response saved.'}).waitFor();
  await page.getByRole('link',{name:'Return to the seminar'}).click();
  await page.getByText('Your previous response is saved.',{exact:false}).waitFor();
  assert.equal(await page.getByLabel('I need another time').isChecked(), true);
  assert.equal(await page.locator('#comment').inputValue(),'Could we use Friday morning instead?');
  await page.getByLabel('Yes, this works').check();
  await page.locator('#comment').fill('');
  await Promise.all([page.waitForURL('**/api/respond'),page.getByRole('button',{name:'Send my response'}).click()]);
  await page.getByText('Your proposed time is confirmed.').waitFor();
  await page.getByRole('link',{name:'Return to the seminar'}).click();
  await page.getByText('Your previous response is saved.',{exact:false}).waitFor();
  assert.equal(await page.getByLabel('Yes, this works').isChecked(), true);
  await mkdir(new URL('../test-results/',import.meta.url),{recursive:true});
  await page.screenshot({path:root+'test-results/desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:root+'test-results/mobile.png',fullPage:true});
  const download = page.waitForEvent('download');
  await page.getByRole('button',{name:'Add proposed time to calendar'}).click();
  assert.equal((await download).suggestedFilename(),'d3-seminar-proposed.ics');

  await page.goto(details.invitation.replace('T'.repeat(43),'X'.repeat(43)));
  await page.getByText('This invitation has expired or is invalid.',{exact:false}).waitFor();
  assert.equal(await page.locator('#response-form').isVisible(),false);
  await page.goto(details.invitation.replace(/revision=[a-f0-9]+/,'revision='+'0'.repeat(64)));
  await page.getByText('The seminar details have changed.',{exact:false}).waitFor();
  assert.equal(await page.locator('#response-form').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log('Browser checks passed: demo, change request, persistence, confirmation, invalid/stale links, mobile layout, calendar download.');
} catch (error) {
  if (page) console.error('Browser test page:', await page.locator('body').innerText());
  throw error;
} finally {
  await browser?.close();
  fixture.kill('SIGINT');
}
