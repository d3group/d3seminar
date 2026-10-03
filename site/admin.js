const $ = id => document.getElementById(id);
const secret = new URLSearchParams(location.hash.slice(1)).get('key');
history.replaceState(null, '', location.pathname);
let dashboard;
const names = {available:'Available', if_needed:'If necessary', unavailable:'Unavailable'};
function element(tag, text, className) {
  const node = document.createElement(tag); node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notice(text, error = false) {
  $('admin-notice').textContent = text; $('admin-notice').classList.toggle('error', error);
}
function when(slot) {
  const format = options => new Intl.DateTimeFormat('en-GB', {timeZone:dashboard.poll.timezone, ...options});
  return format({weekday:'short', day:'numeric', month:'short'}).format(new Date(slot.start)) + ' · ' +
    format({hour:'2-digit', minute:'2-digit'}).format(new Date(slot.start)) + '–' +
    format({hour:'2-digit', minute:'2-digit'}).format(new Date(slot.end));
}
async function api(action, data = {}) {
  const response = await fetch('/api/admin', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, secret, ...data})});
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || 'The request failed.');
  return result;
}
async function choose(action, slot, button) {
  button.disabled = true;
  try {
    await api(action, {slot_id:slot.id, snapshot:dashboard.snapshot});
    await refresh();
    notice(action === 'finalize' ? 'Time finalized. Use the confirmation command below to send the calendar invitation. No email has been sent yet.' : 'Reconfirmation requested. Use the check command below to email people who marked this time If necessary.');
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
  $('outcome').textContent = d.state.final_event ? `Finalized: ${when(d.state.final_event)}. Send confirmation emails using the command below.`
    : d.registered !== d.required ? `The roster is incomplete or has the wrong size. Register all ${d.required} required attendees before choosing a time.`
    : ready.length ? `${ready.length} option${ready.length === 1 ? ' works' : 's work'} for everyone. You can finalize one below.`
    : d.slots.some(s => s.unknown) ? 'Waiting for availability. Unknown answers cannot count as agreement.'
    : d.slots.some(s => s.can_propose) ? 'A common time may work. Ask conditional attendees to confirm they can commit.'
    : 'No time works for everyone yet. Collect alternative suggestions, add new candidate times, then run prepare and sync for another round.';
  $('slot-rows').replaceChildren();
  for (const slot of d.slots) {
    const row = document.createElement('tr');
    row.append(element('th', when(slot)));
    for (const key of ['available', 'if_needed', 'unavailable', 'unknown']) row.append(element('td', slot[key], key));
    const cell = document.createElement('td');
    if (d.state.final_event) cell.textContent = d.state.final_event.slot_id === slot.id ? 'Selected' : 'Poll closed';
    else if (slot.ready || slot.can_propose) {
      const action = slot.ready ? 'finalize' : 'propose';
      const selected = d.state.proposed_slot_id === slot.id;
      const button = element('button', slot.ready ? 'Finalize this time' : selected ? 'Awaiting reconfirmation' : 'Ask to reconfirm', 'slot-action');
      button.disabled = !slot.ready && selected;
      button.addEventListener('click', () => choose(action, slot, button)); cell.append(button);
    } else cell.textContent = 'Not ready';
    row.append(cell); $('slot-rows').append(row);
  }
  const head = document.createElement('tr'); head.append(element('th', 'Person'));
  d.slots.forEach(slot => head.append(element('th', when(slot))));
  head.append(element('th', 'Note / last saved')); $('people-head').replaceChildren(head);
  $('people-rows').replaceChildren();
  for (const person of d.participants) {
    const row = document.createElement('tr'), label = document.createElement('th');
    label.append(element('strong', person.name), element('small', person.email), element('small', person.complete ? 'Complete' : 'Awaiting answers'));
    row.append(label);
    for (const slot of d.slots) row.append(element('td', names[person.answers[slot.id]] || 'Unknown', person.answers[slot.id] || 'unknown'));
    const note = element('td', person.comment || 'No note');
    note.append(element('small', person.updated_at ? new Date(person.updated_at).toLocaleString('en-GB') : 'Not saved yet'));
    row.append(note); $('people-rows').append(row);
  }
}
async function refresh() {
  dashboard = await api('dashboard'); notice('Showing saved responses. Refresh to check for updates.'); render();
}
$('refresh').addEventListener('click', () => refresh().catch(error => notice(error.message, true)));
if (!secret) notice('Open this private dashboard with: python3 scripts/seminar.py admin. Reopen it that way after reloading this page.', true);
else refresh().catch(error => notice(error.message, true));
