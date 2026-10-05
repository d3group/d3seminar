import {slotLabel, dailyHoursLabel} from './shared.js';
const $ = id => document.getElementById(id);
const eventId = new URLSearchParams(location.search).get('event') || 'seminar-1';
const secret = new URLSearchParams(location.hash.slice(1)).get('key');
history.replaceState(null, '', location.pathname + location.search);
let dashboard, schedulePreview, mailPreview;
const names = {available:'Available', unavailable:'Unavailable'};
function element(tag, text, className) {
  const node = document.createElement(tag); node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notice(text, error = false) {
  $('admin-notice').textContent = text; $('admin-notice').classList.toggle('error', error);
}
function when(slot) { return slotLabel(slot, dashboard.poll.timezone, dashboard.poll.daily_hours); }

async function api(action, data = {}) {
  const response = await fetch('/api/admin', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, secret, event_id:eventId, ...data})});
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || 'The request failed.');
  return result;
}
async function choose(slot, button) {
  button.disabled = true;
  try {
    await api('finalize', {slot_id:slot.id, snapshot:dashboard.snapshot});
    await refresh();
    $('mail-kind').value = 'confirm';
    notice('Time finalized. Preview the confirmation emails below to send the calendar invitation. No email has been sent yet.');
  } catch (error) { notice(error.message, true); button.disabled = false; }
}
function render() {
  const d = dashboard;
  $('dashboard').hidden = !d.poll;
  if (!d.poll) { notice('No poll is registered yet. Run prepare and sync from the project folder.'); return; }
  $('response-count').textContent = `${d.completed} of ${d.required} people have answered every option`;
  $('roster-count').textContent = `${d.registered} registered · ${d.required} required to attend`;
  $('admin-timezone').textContent = `All times in ${d.poll.timezone}.`;
  $('admin-deadline').textContent = 'Reply deadline: ' + new Intl.DateTimeFormat('en-GB', {timeZone:d.poll.timezone, dateStyle:'medium', timeStyle:'short'}).format(new Date(d.poll.deadline));
  const ready = d.slots.filter(s => s.ready);
  $('outcome').textContent = d.state.final_event ? `Finalized: ${when(d.state.final_event)}. Preview the confirmation emails below.`
    : d.registered !== d.required ? `The roster is incomplete or has the wrong size. Register all ${d.required} required attendees before choosing a time.`
    : ready.length ? `${ready.length} option${ready.length === 1 ? ' works' : 's work'} for everyone. You can finalize one below.`
    : d.slots.some(s => s.unknown) ? 'Waiting for availability. Unanswered options cannot count as agreement.'
    : 'No time works for everyone yet. Review the notes and use “Edit candidate dates and deadline” below to start another round.';
  $('slot-rows').replaceChildren();
  for (const slot of d.slots) {
    const row = document.createElement('tr');
    row.append(element('th', when(slot)));
    for (const key of ['available', 'unavailable', 'unknown']) row.append(element('td', slot[key], key));
    const cell = document.createElement('td');
    if (d.state.final_event) cell.textContent = d.state.final_event.slot_id === slot.id ? 'Selected' : 'Poll closed';
    else if (slot.ready) {
      const button = element('button', 'Finalize this time', 'slot-action');
      button.addEventListener('click', () => choose(slot, button)); cell.append(button);
    } else cell.textContent = 'Not ready';
    row.append(cell); $('slot-rows').append(row);
  }
  const head = document.createElement('tr'); head.append(element('th', 'Person'));
  d.slots.forEach(slot => head.append(element('th', when(slot))));
  head.append(element('th', 'Note / last saved')); $('people-head').replaceChildren(head);
  $('people-rows').replaceChildren();
  for (const person of d.participants) {
    const row = document.createElement('tr'), label = document.createElement('th');
    label.append(element('strong', person.name), element('small', person.email), element('small', {professor:'Professor', student:'Student'}[person.role] || 'Role unassigned'), element('small', person.complete ? 'Complete' : 'Awaiting answers'));
    row.append(label);
    for (const slot of d.slots) row.append(element('td', names[person.answers[slot.id]] || 'Awaiting reply', person.answers[slot.id] || 'unknown'));
    const note = element('td', person.comment || 'No note');
    note.append(element('small', person.updated_at ? new Date(person.updated_at).toLocaleString('en-GB') : 'Not saved yet'));
    row.append(note); $('people-rows').append(row);
  }
  renderEditors();
}

function localInput(value, dateOnly = false) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {timeZone:dashboard.poll.timezone, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}).formatToParts(new Date(value)).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}${dateOnly ? '' : `T${parts.hour}:${parts.minute}`}`;
}
function clearSchedulePreview() { schedulePreview = null; $('schedule-review').hidden = true; }
function clearMailPreview() {
  mailPreview = null; $('mail-review').hidden = true; $('mail-consent').checked = false; $('smtp-password').value = '';
}
function addOption(slot) {
  const onsite = Boolean(dashboard.poll.daily_hours);
  const row = element('div', '', 'schedule-option'); row.dataset.id = slot?.id || '';
  const label = element('label', onsite ? 'First day of the onsite option' : 'Candidate start time');
  const input = document.createElement('input'); input.type = onsite ? 'date' : 'datetime-local'; input.required = true;
  if (slot) input.value = localInput(slot.start, onsite);
  label.append(input);
  const remove = element('button', 'Remove', 'text-button'); remove.type = 'button';
  remove.addEventListener('click', () => { row.remove(); clearSchedulePreview(); updateOptionButtons(); });
  row.append(label, remove); $('schedule-options').append(row); updateOptionButtons();
}
function updateOptionButtons() {
  const count = $('schedule-options').children.length;
  $('schedule-options').querySelectorAll('button').forEach(button => { button.disabled = count <= 2; });
  $('add-option').disabled = count >= 12;
}
function renderEditors() {
  clearSchedulePreview(); clearMailPreview();
  $('schedule-editor').hidden = Boolean(dashboard.state.final_event);
  $('schedule-help').textContent = `All times are in ${dashboard.poll.timezone}. ` + (dashboard.poll.daily_hours
    ? `Each option spans ${dashboard.poll.duration_days} consecutive dates from the selected day, ${dailyHoursLabel(dashboard.poll.daily_hours)}. `
    : 'Each seminar reserves two hours. ') + 'Adding options or updating the deadline keeps saved answers. Changing or removing an existing date starts a new round.';
  $('edit-deadline').value = localInput(dashboard.poll.deadline);
  $('schedule-options').replaceChildren(); dashboard.poll.slots.forEach(addOption);
  for (const key of ['topic', 'presenters', 'room', 'agenda']) $('edit-' + key).value = dashboard.state.programme?.[key] || '';
}
function proposal() {
  return {deadline:$('edit-deadline').value, options:[...$('schedule-options').children].map(row => ({id:row.dataset.id, start:row.querySelector('input').value}))};
}
function mailNotice(message, error = false) {
  $('mail-notice').textContent = message; $('mail-notice').hidden = !message; $('mail-notice').classList.toggle('error', error);
}
$('schedule-form').addEventListener('input', clearSchedulePreview);
$('add-option').addEventListener('click', () => { addOption(); clearSchedulePreview(); });
$('schedule-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const submitted = proposal();
    const reviewed = await api('preview_schedule', {proposal:submitted});
    if (JSON.stringify(submitted) !== JSON.stringify(proposal())) return;
    schedulePreview = {...reviewed, proposal:submitted};
    $('schedule-effect').textContent = reviewed.resets_answers ? 'These dates will start a new round. Current answers and notes will be archived; everyone will need to reply again.' : 'Your saved answers and notes will be kept.';
    $('schedule-review-options').replaceChildren(...reviewed.options.map(option => element('li', option)));
    $('reset-consent-label').hidden = !reviewed.resets_answers; $('reset-consent').checked = false;
    $('schedule-review').hidden = false;
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$('save-schedule').addEventListener('click', async () => {
  if (!schedulePreview) return;
  $('save-schedule').disabled = true;
  try {
    const result = await api('save_schedule', {proposal:schedulePreview.proposal, preview:schedulePreview.preview, confirm_reset:$('reset-consent').checked});
    await refresh();
    notice(result.resets_answers ? 'New dates saved. Previous answers are archived; participants can use their existing links to reply again.' : 'Schedule saved. Existing answers are unchanged.');
  } catch (error) { notice(error.message, true); }
  finally { $('save-schedule').disabled = false; }
});
$('programme-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const programme = Object.fromEntries(['topic', 'presenters', 'room', 'agenda'].map(key => [key, $('edit-' + key).value]));
    await api('save_programme', {programme, snapshot:dashboard.snapshot});
    await refresh(); notice('Programme saved. Availability is unchanged. No email has been sent.');
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$('mail-kind').addEventListener('change', () => { clearMailPreview(); mailNotice(''); });
$('mail-preview-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; clearMailPreview();
  const kind = $('mail-kind').value;
  try {
    const preview = await api('preview_mail', {kind});
    if (kind !== $('mail-kind').value) return;
    mailPreview = {...preview, kind};
    $('mail-count').textContent = `${preview.messages.length} email${preview.messages.length === 1 ? '' : 's'} from ${preview.sender}`;
    $('mail-messages').replaceChildren();
    preview.messages.forEach((mail, index) => {
      const details = element('details', '', 'mail-message'); details.open = index === 0;
      details.append(element('summary', `${mail.name} · ${mail.to}`), element('p', mail.subject, 'mail-subject'), element('pre', mail.body));
      $('mail-messages').append(details);
    });
    $('mail-review').hidden = false; $('mail-send-form').hidden = !preview.messages.length;
    $('send-mail').textContent = `Send ${preview.messages.length} reviewed email${preview.messages.length === 1 ? '' : 's'}`;
    mailNotice(preview.messages.length ? 'Preview only. No email has been sent.' : 'No eligible messages. Replies already completed and messages already sent or attempted are excluded.');
  } catch (error) { mailNotice(error.message, true); }
  finally { button.disabled = false; }
});
$('mail-send-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!mailPreview || !$('mail-consent').checked) return;
  const reviewed = mailPreview;
  let password = $('smtp-password').value; $('smtp-password').value = '';
  $('send-mail').disabled = true; mailNotice('Sending the reviewed emails…');
  try {
    const result = await api('send_mail', {kind:reviewed.kind, preview:reviewed.preview, password});
    mailNotice(`${result.sent} email${result.sent === 1 ? '' : 's'} accepted by the university mail server.${result.skipped ? ` ${result.skipped} skipped because responses changed.` : ''}`);
  } catch (error) { mailNotice(error.message, true); }
  finally { password = null; clearMailPreview(); $('send-mail').disabled = false; }
});
async function refresh() {
  dashboard = await api('dashboard'); notice('Showing saved responses. Refresh to check for updates.'); render();
}
$('refresh').addEventListener('click', () => refresh().catch(error => notice(error.message, true)));
if (!secret) notice('Open this private dashboard with: python3 scripts/seminar.py admin. Reopen it that way after reloading this page.', true);
else start().catch(error => notice(error.message, true));
async function start() {
  const response = await fetch('config.json', {cache:'no-store'});
  const config = await response.json();
  const selected = config.events.find(event => event.id === eventId);
  if (!selected) throw new Error('Unknown event. Reopen the organizer dashboard.');
  $('admin-event-title').textContent = selected.name;
  document.title = selected.name + ' · ' + config.semester + ' · Organizer';
  $('admin-semester').textContent = selected.semester || config.semester;
  for (const event of config.events) {
    const link = element('a', event.name);
    link.href = 'admin?' + new URLSearchParams({event:event.id}) + '#' + new URLSearchParams({key:secret});
    if (event.id === eventId) link.setAttribute('aria-current', 'page');
    $('event-tabs').append(link);
  }
  document.querySelectorAll('.mail-steps dd code').forEach(code => {
    code.textContent = code.textContent.replace('seminar.py ', `seminar.py --event ${eventId} `);
  });
  await refresh();
}
