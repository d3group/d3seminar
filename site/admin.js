import {apiFetch, loadConfig, serviceNotice} from './api.js';
import {$, element, slotLabel, dailyHoursLabel, preserveInvitationLinks, updateOrganizerNavigation} from './shared.js';
const eventId = new URLSearchParams(location.search).get('event') || 'seminar-1';
const secret = new URLSearchParams(location.hash.slice(1)).get('key');
const token = new URLSearchParams(location.hash.slice(1)).get('token');
if (secret) history.replaceState(null, '', location.pathname + location.search);
preserveInvitationLinks(token);
if (token) {
  const back = $('back-to-agenda'); back.hidden = false;
  back.href = 'agenda.html?' + new URLSearchParams({event:eventId}) + '#' + new URLSearchParams({token});
}
let dashboard, schedulePreview, mailPreview, roster, editingUser, editingVersion, programmeConfig;
let userBusy = false;
let mailDraft, draftRequest = 0, draftSaving = false, draftTarget = 'draft-body';
let exampleTimer, exampleRequest = 0;
const draftBuffers = new Map();
const names = {available:'Available', unavailable:'Unavailable'};
function notice(text, error = false) {
  $('admin-notice').textContent = text; $('admin-notice').hidden = !text; $('admin-notice').classList.toggle('error', error);
}
function when(slot) { return slotLabel(slot, dashboard.poll.timezone, dashboard.poll.daily_hours); }

async function api(action, data = {}) {
  const response = await apiFetch('admin', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, secret, token, event_id:eventId, ...data})});
  const result = await response.json();
  if (response.status === 403) {
    updateOrganizerNavigation(token);
    $('dashboard').hidden = true; $('admin-panels').hidden = true; notice(result.error, true);
  }
  if (!response.ok || !result.ok) throw new Error(result.error || 'The request failed.');
  return result;
}
async function choose(slot, button) {
  button.disabled = true;
  try {
    await api('finalize', {slot_id:slot.id, snapshot:dashboard.snapshot});
    await refresh();
    $('mail-kind').value = 'confirm';
    updateMailKind();
    notice('Time finalized. Send the calendar invitation with the local email service. No email has been sent yet.');
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
  $('outcome').textContent = d.state.final_event ? `Finalized: ${when(d.state.final_event)}. Send confirmations with the local email service.`
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
    label.append(element('strong', person.name), element('small', person.email), element('small', {professor:'Professor', student:'PhD', member:'Member'}[person.role] || 'Role unassigned'), element('small', person.complete ? 'Complete' : 'Awaiting answers'));
    row.append(label);
    for (const slot of d.slots) {
      const answer = person.answers[slot.id];
      const cell = element('td', '', answer || 'unknown');
      if (names[answer]) {
        const mark = element('strong', answer === 'available' ? '✓' : '✗', 'response-mark');
        mark.setAttribute('aria-hidden', 'true');
        cell.title = names[answer];
        cell.append(mark, element('span', names[answer], 'sr-only'));
      } else cell.textContent = 'Awaiting reply';
      row.append(cell);
    }
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
  mailPreview = null; $('mail-review').hidden = true;
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
    $('schedule-effect').textContent = reviewed.resets_answers ? 'These dates will start a new round. Current answers and notes will be archived; everyone will need to reply again.' : 'Your notes and answers for dates that remain unchanged will be kept.';
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
    notice(result.resets_answers ? 'New dates saved. Previous answers are archived; participants can use their existing links to reply again.' : 'Schedule saved. Answers for unchanged dates are kept.');
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
function updateMailKind() {
  const kind = $('mail-kind').value;
  $('mail-event').disabled = kind === 'initial';
  $('mail-event').value = kind === 'initial' ? 'seminar-1' : kind === 'confirm' ? eventId : 'next';
  $('mail-kind-help').hidden = kind !== 'initial';
  clearMailPreview(); mailNotice('');
  if (!$('panel-email').hidden) loadMailDraft();
}
$('mail-kind').addEventListener('change', updateMailKind);
$('mail-event').addEventListener('change', () => { mailNotice(''); loadMailDraft(); });

function draftChanged(draft = mailDraft) {
  return draft && (draft.subject !== draft.saved.subject || draft.body !== draft.saved.body);
}
function draftNotice(message, error = false) {
  $('draft-notice').textContent = message; $('draft-notice').hidden = !message; $('draft-notice').classList.toggle('error', error);
}
function updateDraftButtons() {
  $('save-draft').disabled = !mailDraft || !draftChanged() || draftSaving;
  $('preview-emails').disabled = !mailDraft || draftChanged() || draftSaving;
}
function updateEmailExample(delay = 300) {
  clearTimeout(exampleTimer);
  const pending = ++exampleRequest, entry = mailDraft;
  $('draft-example').hidden = !entry;
  if (!entry) { $('draft-example-message').hidden = true; return; }
  const {kind, event_id:mailEvent} = entry.saved, {subject, body} = entry;
  $('draft-example').setAttribute('aria-busy', 'true');
  $('draft-example-status').textContent = 'Updating preview…';
  $('draft-example-status').classList.remove('error');
  exampleTimer = setTimeout(async () => {
    try {
      const example = await api('email_example', {kind, mail_event:mailEvent, subject, body});
      if (pending !== exampleRequest) return;
      $('draft-example-subject').textContent = example.subject;
      $('draft-example-from').textContent = `${example.sender_name} <${example.sender}>`;
      $('draft-example-to').textContent = example.to;
      const document = new DOMParser().parseFromString(example.html, 'text/html');
      // The sender escapes draft text. Apply its email styles through our CSP-approved stylesheet.
      document.querySelectorAll('[style]').forEach(node => node.removeAttribute('style'));
      document.querySelectorAll('a').forEach(link => link.removeAttribute('href'));
      $('draft-example-body').replaceChildren(...document.body.childNodes);
      $('draft-example-attachments').hidden = !example.attachments.length;
      $('draft-example-attachments').textContent = 'Attachment: ' + example.attachments.join(', ');
      $('draft-example-message').hidden = false;
      $('draft-example-status').textContent = example.provisional
        ? 'Example uses the first candidate date. The event is not yet confirmed.'
        : 'Preview of your current wording. Nothing has been sent.';
    } catch (error) {
      if (pending !== exampleRequest) return;
      $('draft-example-message').hidden = true;
      $('draft-example-status').textContent = error.message;
      $('draft-example-status').classList.add('error');
    } finally {
      if (pending === exampleRequest) $('draft-example').setAttribute('aria-busy', 'false');
    }
  }, delay);
}
function renderDraft(entry) {
  mailDraft = entry;
  $('draft-heading').textContent = `Draft · ${entry.saved.event_name}`;
  $('draft-subject').value = entry.subject; $('draft-body').value = entry.body;
  $('draft-required').textContent = 'Keep these details in the message: ' + entry.saved.required_fields.map(name => '{' + name + '}').join(', ') + '.';
  $('draft-fields').replaceChildren();
  for (const [name, label] of Object.entries(entry.saved.fields)) {
    const button = element('button', label); button.type = 'button'; button.title = '{' + name + '}';
    button.addEventListener('click', () => {
      if (draftTarget === 'draft-subject' && !mailDraft.saved.subject_fields.includes(name)) {
        draftNotice('Insert this detail in the message, rather than the subject.', true); return;
      }
      const input = $(draftTarget);
      input.setRangeText('{' + name + '}', input.selectionStart, input.selectionEnd, 'end');
      input.focus(); editDraft();
    });
    $('draft-fields').append(button);
  }
  $('mail-draft-form').hidden = false;
  updateDraftButtons();
  updateEmailExample(0);
  draftNotice(draftChanged() ? 'Unsaved changes. Save the draft before previewing recipients.'
    : entry.saved.updated_at ? 'Saved draft. Preview recipients when you are ready.' : 'Default wording. Edit it here, or preview it as it is.');
}
function editDraft() {
  if (!mailDraft) return;
  mailDraft.subject = $('draft-subject').value; mailDraft.body = $('draft-body').value;
  clearMailPreview(); updateDraftButtons();
  updateEmailExample();
  draftNotice(draftChanged() ? 'Unsaved changes. Save the draft before previewing recipients.' : 'Saved wording restored.');
}
for (const id of ['draft-subject', 'draft-body']) {
  $(id).addEventListener('focus', () => { draftTarget = id; });
  $(id).addEventListener('input', editDraft);
}
async function loadMailDraft(reload = false) {
  if (draftSaving) return;
  const kind = $('mail-kind').value, mailEvent = $('mail-event').value, key = kind + ':' + mailEvent;
  const pending = ++draftRequest, buffer = draftBuffers.get(key);
  clearMailPreview(); mailDraft = null; updateDraftButtons(); $('mail-draft-form').hidden = true;
  updateEmailExample();
  if (!reload && draftChanged(buffer)) { renderDraft(buffer); return; }
  draftNotice('Loading draft…');
  try {
    const saved = await api('email_draft', {kind, mail_event:mailEvent});
    if (pending !== draftRequest) return;
    const entry = {saved, subject:saved.subject, body:saved.body};
    draftBuffers.set(key, entry); renderDraft(entry);
  } catch (error) { if (pending === draftRequest) draftNotice(error.message, true); }
}
$('reload-draft').addEventListener('click', () => {
  if (draftChanged() && !confirm('Discard unsaved changes and reload the saved draft?')) return;
  loadMailDraft(true);
});
$('default-draft').addEventListener('click', () => {
  if (!mailDraft) return;
  $('draft-subject').value = mailDraft.saved.default.subject; $('draft-body').value = mailDraft.saved.default.body;
  editDraft(); draftNotice('Default wording loaded. Save the draft to use it.');
});
$('mail-draft-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!mailDraft || draftSaving) return;
  const entry = mailDraft;
  draftSaving = true; updateDraftButtons();
  $('mail-kind').disabled = true; $('mail-event').disabled = true; $('reload-draft').disabled = true;
  $('mail-draft-form').querySelectorAll('input, textarea, button').forEach(control => { control.disabled = true; });
  try {
    entry.saved = await api('save_email_draft', {kind:entry.saved.kind, mail_event:entry.saved.event_id,
      subject:entry.subject, body:entry.body, version:entry.saved.version});
    clearMailPreview();
    draftNotice('Draft saved. Preview recipients and emails, then queue them when ready. Nothing has been sent.');
    await refreshMail();
  } catch (error) { draftNotice(error.message, true); }
  finally {
    draftSaving = false;
    $('mail-kind').disabled = false; $('mail-event').disabled = $('mail-kind').value === 'initial'; $('reload-draft').disabled = false;
    $('mail-draft-form').querySelectorAll('input, textarea, button').forEach(control => { control.disabled = false; });
    updateDraftButtons();
  }
});
window.addEventListener('beforeunload', event => {
  if ([...draftBuffers.values()].some(draft => draftChanged(draft))) { event.preventDefault(); event.returnValue = ''; }
});
$('mail-preview-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!mailDraft || draftChanged() || draftSaving) return;
  const button = $('preview-emails'); button.disabled = true; clearMailPreview();
  const entry = mailDraft, {kind, event_id:mailEvent, version} = entry.saved;
  try {
    const preview = await api('email_preview', {kind, mail_event:mailEvent, draft_version:version});
    if (entry !== mailDraft || version !== mailDraft.saved.version || draftChanged()) return;
    mailPreview = preview;
    $('mail-count').textContent = `${preview.event_name}: ${preview.messages.length} email${preview.messages.length === 1 ? '' : 's'} from ${preview.sender}`;
    $('mail-messages').replaceChildren();
    preview.messages.forEach((mail, index) => {
      const details = element('details', '', 'mail-message'); details.open = index === 0;
      details.append(element('summary', `${mail.name} · ${mail.to}`), element('p', mail.subject, 'mail-subject'), element('pre', mail.body));
      $('mail-messages').append(details);
    });
    $('mail-review').hidden = false;
    $('queue-mail').disabled = !preview.messages.length;
    mailNotice(preview.messages.length ? 'Personal links are hidden in this preview. Queue these emails, then send with the local email service.' : 'No eligible messages for this email type. Previous deliveries and current responses have been checked.');
  } catch (error) { mailNotice(error.message, true); }
  finally { updateDraftButtons(); }
});
$('queue-mail').addEventListener('click', async () => {
  if (!mailPreview) return;
  $('queue-mail').disabled = true;
  try {
    await api('queue_mail', {kind:mailPreview.kind, mail_event:mailPreview.event_id, version:mailPreview.version});
    clearMailPreview();
    await refreshMail(); mailNotice('Emails queued. Nothing has been sent. Run the local sender when you are ready.');
  } catch (error) { mailNotice(error.message, true); $('queue-mail').disabled = false; }
});
const mailKinds = {initial:'Initial outreach', invite:'Timing invitation', remind:'Availability reminder', present:'Talk proposal nudge', confirm:'Final confirmation'};
const deliveryNames = {ready:'Ready for local sender', sent:'Sent', attempted:'Attempted · check delivery', skipped:'No longer eligible', review:'Changed · preview again', cancelled:'Cancelled'};
async function refreshMail() {
  const result = await api('mail_batches');
  $('mail-batches').replaceChildren();
  if (!result.batches.length) $('mail-batches').append(element('p', 'No emails queued yet.', 'admin-help'));
  for (const batch of result.batches) {
    const card = element('details', '', 'mail-message');
    card.append(element('summary', `${batch.event_name} · ${mailKinds[batch.kind]} · ${batch.recipients.length} recipients`));
    card.append(element('p', Object.entries(batch.counts).map(([key, count]) => `${count} ${deliveryNames[key]}`).join(' · '), 'admin-help'));
    if (batch.error) card.append(element('p', batch.error, 'notice error'));
    const list = document.createElement('ul');
    for (const person of batch.recipients) list.append(element('li', `${person.name} · ${person.to} · ${deliveryNames[person.status]}`));
    card.append(list);
    if (!batch.cancelled && (batch.counts.ready || batch.counts.review)) {
      const cancel = element('button', 'Cancel unsent emails', 'text-button'); cancel.type = 'button';
      cancel.addEventListener('click', async () => {
        cancel.disabled = true;
        try { await api('cancel_mail', {batch_id:batch.id}); await refreshMail(); mailNotice('Unsent emails cancelled. Messages already claimed may still be delivered.'); }
        catch (error) { mailNotice(error.message, true); cancel.disabled = false; }
      });
      card.append(cancel);
    }
    $('mail-batches').append(card);
  }
}
$('refresh-mail').addEventListener('click', () => refreshMail().catch(error => mailNotice(error.message, true)));

function userNotice(message, error = false) {
  $('users-notice').hidden = !message; $('users-notice').textContent = message; $('users-notice').classList.toggle('error', error);
}
function renderUsers() {
  $('user-rows').replaceChildren();
  const active = roster.users.filter(person => !person.is_archived);
  $('users-count').textContent = `${active.length} users · ${active.filter(person => person.is_organizer).length} organizers`;
  for (const person of roster.users.filter(person => !person.is_archived || $('show-removed').checked)) {
    const row = document.createElement('tr'), name = element('th', person.name); name.scope = 'row';
    name.append(element('small', person.email || 'Email to follow')); row.append(name, element('td', person.role));
    row.append(element('td', person.is_archived ? 'Removed' : person.is_organizer ? 'Organizer' : person.events.length ? 'Participant' : 'Draft'));
    row.append(element('td', person.events.map(id => programmeConfig.events.find(event => event.id === id)?.name || id).join(', ') || '—'));
    const actions = document.createElement('td'), edit = element('button', person.is_archived ? 'Restore user' : 'Edit user', 'text-button'); edit.type = 'button';
    edit.setAttribute('aria-label', `${person.is_archived ? 'Restore' : 'Edit'} ${person.name}`);
    edit.addEventListener('click', () => openUser(person)); actions.append(edit); row.append(actions); $('user-rows').append(row);
  }
}
async function refreshUsers() { roster = await api('users'); renderUsers(); }
function openUser(person = null) {
  if (userBusy || !roster) return;
  editingUser = person; editingVersion = roster.version;
  $('user-form-heading').textContent = person ? `${person.is_archived ? 'Restore' : 'Edit'} ${person.name}` : 'Add user';
  $('user-name').value = person?.name || ''; $('user-email').value = person?.email || '';
  $('user-role').value = person?.role || 'PhD'; $('user-organizer').checked = Boolean(person?.is_organizer);
  $('user-events').querySelectorAll('input').forEach(input => { input.checked = Boolean(person?.events.includes(input.value)); });
  $('remove-user').hidden = !person || person.is_archived;
  $('user-form').hidden = false; userNotice(''); $('user-name').focus();
}
async function saveUser(user) {
  userBusy = true;
  $('user-form').querySelectorAll('input, select, button').forEach(control => { control.disabled = true; });
  try {
    const saved = await api('save_user', {user, version:editingVersion});
    $('user-form').hidden = true; clearMailPreview();
    if (!saved.is_organizer) {
      updateOrganizerNavigation(token);
      $('admin-panels').hidden = true; notice('User saved. Your organizer access has been removed.'); return;
    }
    await refreshUsers();
    userNotice(user.is_archived ? 'User removed. Their access is revoked; saved responses and talks are retained.' : 'User saved. No email has been sent.');
  } catch (error) { userNotice(error.message, true); }
  finally { userBusy = false; $('user-form').querySelectorAll('input, select, button').forEach(control => { control.disabled = false; }); }
}
$('user-form').addEventListener('submit', event => {
  event.preventDefault();
  if (userBusy) return;
  saveUser({...(editingUser ? {id:editingUser.id} : {}), name:$('user-name').value, email:$('user-email').value,
    role:$('user-role').value, is_organizer:$('user-organizer').checked, is_archived:false,
    events:[...$('user-events').querySelectorAll('input:checked')].map(input => input.value)});
});
$('remove-user').addEventListener('click', () => {
  if (userBusy || !editingUser || !confirm(`Remove ${editingUser.name}? Their invitation links will stop working. Saved responses and talks will be retained.`)) return;
  const user = Object.fromEntries(['id', 'name', 'email', 'role', 'is_organizer', 'events'].map(key => [key, editingUser[key]]));
  saveUser({...user, is_archived:true});
});
$('add-user').addEventListener('click', () => openUser());
$('cancel-user').addEventListener('click', () => { $('user-form').hidden = true; });
$('show-removed').addEventListener('change', renderUsers);
$('refresh-users').addEventListener('click', () => refreshUsers().catch(error => userNotice(error.message, true)));
document.querySelectorAll('[data-panel]').forEach(button => button.addEventListener('click', async () => {
  const panel = button.dataset.panel;
  $('admin-event-title').textContent = panel === 'events' ? programmeConfig.events.find(event => event.id === eventId).name : 'Organizer dashboard';
  if (panel !== 'events') notice('');
  document.querySelectorAll('[data-panel]').forEach(tab => {
    const selected = tab === button; tab.setAttribute('aria-pressed', String(selected)); $('panel-' + tab.dataset.panel).hidden = !selected;
  });
  try {
    if (panel === 'users') await refreshUsers();
    else if (panel === 'email') { await refreshMail(); await loadMailDraft(); }
    else await refresh();
  } catch (error) { notice(error.message, true); }
}));
async function refresh() {
  dashboard = await api('dashboard'); notice('Showing saved responses. Refresh to check for updates.'); render();
}
$('refresh').addEventListener('click', () => refresh().catch(error => notice(error.message, true)));
if (!secret && !token) notice('Open the organizer dashboard through your personal invitation link.', true);
else start().catch(error => notice(error.message, true));
async function start() {
  const config = await loadConfig();
  programmeConfig = config;
  if (config.service_status !== 'ready') throw new Error(serviceNotice(config));
  const selected = config.events.find(event => event.id === eventId);
  if (!selected) throw new Error('Unknown event. Reopen the organizer dashboard.');
  $('admin-event-title').textContent = selected.name;
  document.title = selected.name + ' · ' + config.semester + ' · Organizer';
  $('admin-semester').textContent = selected.semester || config.semester;
  for (const event of config.events) {
    const link = element('a', event.name);
    link.href = 'admin.html?' + new URLSearchParams({event:event.id}) + '#' + new URLSearchParams(secret ? {key:secret} : {token});
    if (event.id === eventId) link.setAttribute('aria-current', 'page');
    $('event-tabs').append(link);
    const option = element('option', event.name); option.value = event.id; $('mail-event').append(option);
    const label = element('label', '', 'consent'), checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.value = event.id; label.append(checkbox, document.createTextNode(event.name)); $('user-events').append(label);
  }
  $('confirm-command').textContent = `python3 scripts/mailer.py confirm --event ${eventId}`;
  updateMailKind();
  await refresh();
  updateOrganizerNavigation(token, eventId);
  $('admin-panels').hidden = false;
}
