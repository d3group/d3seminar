import {apiFetch, loadConfig, apiUrl, serviceNotice} from './api.js';
import {$, element, selectEvent, slotLabel, eventHref, preserveInvitationLinks, loadProgramme, renderProgramme, renderPresentationNudge, agendaSchedule, nextSeminar, eventDisclosure} from './shared.js';

const token = new URLSearchParams(location.hash.slice(1)).get('token');
preserveInvitationLinks(token);
let config, entries = [], editingId, saving = false;
const drafts = new Map();
const expandedEvents = new Map();
const requestedEvent = new URLSearchParams(location.search).get('event');
const canPresent = invitation => ['student', 'professor'].includes(invitation?.role);

function timingLabel(talk) {
  const parts = [];
  if (talk.presentation_minutes != null) parts.push(`${talk.presentation_minutes} min presentation`);
  if (talk.questions_minutes != null) parts.push(`${talk.questions_minutes} min questions`);
  if (parts.length === 2) parts.push(`${talk.presentation_minutes + talk.questions_minutes} min total`);
  return parts.join(' · ');
}

function readTiming() {
  const minutes = id => $(id).value === '' ? null : Number($(id).value);
  return {presentation_minutes:minutes('presentation-minutes'), questions_minutes:minutes('questions-minutes')};
}

function updateTimingTotal() {
  const timing = readTiming();
  const complete = Object.values(timing).every(Number.isInteger) && $('presentation-minutes').validity.valid && $('questions-minutes').validity.valid;
  $('talk-duration-total').textContent = complete ? `${timing.presentation_minutes + timing.questions_minutes} min total` : 'Timings can follow later.';
}

function setStatus(message, kind = '') {
  const status = $('presentation-status');
  status.textContent = message; status.hidden = !message;
  status.classList.toggle('success', kind === 'success'); status.classList.toggle('error', kind === 'error');
}

function showSaved(entry, removed) {
  const talk = entry.invitation.presentation;
  const heading = removed ? 'Proposal withdrawn.' : talk.status === 'placeholder' ? 'Your placeholder is saved.' : 'Your talk is saved.';
  setStatus(heading, 'success');
  const status = $('presentation-status');
  status.replaceChildren(element('h3', heading), element('p', removed ? entry.event.name : `“${talk.title}” · ${entry.event.name}`));
  if (!removed && timingLabel(talk)) status.append(element('p', timingLabel(talk)));
  const view = element('button', 'View in agenda', 'text-button');
  view.type = 'button'; view.addEventListener('click', closeEditor); status.append(view);
  status.focus({preventScroll:true}); status.scrollIntoView({block:'nearest'});
}

function materialsCell(event, talk, kind) {
  const cell = element('td', '', 'talk-material');
  cell.dataset.label = kind === 'explainer' ? 'Explainer' : 'Slides';
  if (talk[kind + '_url']) {
    const link = element('a', 'Link'); link.href = talk[kind + '_url'];
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.setAttribute('aria-label', `${cell.dataset.label} link for ${talk.title}`); cell.append(link);
  }
  const file = talk.files?.[kind];
  if (file) {
    const form = element('form', '', 'material-download');
    form.action = apiUrl('material'); form.method = 'post'; form.target = '_blank'; form.rel = 'noopener';
    for (const [name, value] of Object.entries({...(token ? {token} : {}), event_id:event.id, file_id:file.id})) {
      const input = document.createElement('input'); input.type = 'hidden'; input.name = name; input.value = value; form.append(input);
    }
    const button = element('button', kind === 'explainer' && file.mime.startsWith('text/html') ? 'Open explainer' : 'Download ' + kind, 'text-button');
    button.type = 'submit'; button.title = file.name; form.append(button); cell.append(form);
  }
  if (!cell.childElementCount) cell.append(element('span', 'To follow', 'material-pending'));
  return cell;
}

function durationBar(talk) {
  if (talk.presentation_minutes == null || talk.questions_minutes == null) return element('p', timingLabel(talk) || 'Duration to follow', 'talk-duration-pending');
  const timing = element('div', '', 'talk-duration');
  const bar = element('div', '', 'talk-duration-bar');
  bar.setAttribute('role', 'img'); bar.setAttribute('aria-label', timingLabel(talk));
  const labels = element('div', '', 'talk-duration-labels'); labels.setAttribute('aria-hidden', 'true');
  for (const [minutes, kind, label] of [[talk.presentation_minutes, 'presentation', 'talk'], [talk.questions_minutes, 'questions', 'Q&A']]) {
    const segment = element('span', '', 'duration-' + kind); segment.style.flexGrow = minutes;
    if (minutes) bar.append(segment);
    labels.append(element('span', `${minutes} min ${label}`, 'duration-label-' + kind));
  }
  timing.append(bar, labels);
  return timing;
}

function timeCell(event, slot) {
  const cell = element('td', '', 'talk-timeslot'); cell.dataset.label = 'Time slot';
  if (slot.start) {
    cell.append(element('strong', `${slot.start}–${slot.end}`));
    if (event.duration_days > 1) cell.append(element('span', `Day ${slot.day}`));
  } else cell.append(element('span', slot.reason, 'material-pending'));
  return cell;
}

function talkTable(event, talks, finalEvent) {
  const table = element('table', '', 'talk-table');
  const caption = element('caption', 'Draft running order', 'agenda-order-note');
  caption.setAttribute('aria-label', 'Draft running order · ' + event.name); table.append(caption);
  const schedule = agendaSchedule(event, talks, finalEvent);
  const head = element('thead', ''), headings = element('tr', '');
  for (const label of ['Presenter', 'Time slot', 'Talk & abstract', 'Explainer', 'Slides']) {
    const cell = element('th', label); cell.scope = 'col'; headings.append(cell);
  }
  head.append(headings); table.append(head);
  const body = element('tbody', '');
  const rows = talks.map((talk, index) => ({talk, slot:schedule.slots[index]}));
  rows.push(...schedule.open.map(slot => ({slot})));
  rows.sort((a, b) => a.slot.start && b.slot.start
    ? a.slot.day - b.slot.day || a.slot.start.localeCompare(b.slot.start)
    : Number(Boolean(b.slot.start)) - Number(Boolean(a.slot.start)));
  for (const {talk, slot} of rows) {
    const row = element('tr', '', talk ? 'talk-row' : 'agenda-open-slot');
    if (!talk) {
      const name = element('th', '—'); name.scope = 'row'; name.setAttribute('aria-label', 'No presenter assigned');
      const details = element('td', '', 'talk-description'); details.colSpan = 3;
      const placeholder = element('div', '', 'agenda-open-placeholder');
      placeholder.append(element('strong', 'Open slot'), element('p', slot.minutes ? `${slot.minutes} min available` : 'Timing to be confirmed'));
      details.append(placeholder); row.append(name, timeCell(event, slot), details); body.append(row);
      continue;
    }
    const name = element('th', ''); name.scope = 'row'; name.dataset.label = 'Presenter';
    name.append(element('span', talk.name));
    if (talk.email) {
      const email = element('a', talk.email, 'talk-presenter-email'); email.href = 'mailto:' + encodeURIComponent(talk.email);
      name.append(email);
    }
    const details = element('td', '', 'talk-description'); details.dataset.label = 'Talk & abstract';
    details.append(element('strong', talk.title));
    if (talk.status === 'placeholder') details.append(element('span', 'Provisional title', 'talk-visibility'));
    if (talk.presentation_minutes != null || talk.questions_minutes != null) details.append(durationBar(talk));
    details.append(element('span', talk.visibility === 'public' ? 'Public' : 'Internal', 'talk-visibility'));
    if (talk.abstract) {
      const abstract = element('details', '', 'talk-abstract');
      abstract.append(element('summary', 'Abstract'), element('p', talk.abstract)); details.append(abstract);
    } else details.append(element('p', 'Abstract to follow', 'material-pending'));
    row.append(name, timeCell(event, slot), details, materialsCell(event, talk, 'explainer'), materialsCell(event, talk, 'slides')); body.append(row);
  }
  table.append(body);
  return table;
}

function presenterList(talks) {
  const section = element('section', '', 'public-presenters');
  section.append(element('h3', 'Presenters'));
  const list = element('ul', '');
  for (const talk of talks) list.append(element('li', talk.name));
  section.append(list);
  return section;
}

function renderEvent(event, invitation, error, publicTalks = [], open = false, publicFinal = null) {
  const finalEvent = invitation?.state.final_event || publicFinal;
  const row = element('article', '', 'agenda-event'); row.dataset.event = event.id;
  const heading = element('div', '', 'agenda-event-heading');
  const title = element('h2', event.name); title.id = 'agenda-' + event.id; title.tabIndex = -1;
  row.setAttribute('aria-labelledby', title.id); heading.append(title);
  const month = new Intl.DateTimeFormat('en-GB', {timeZone:event.timezone, month:'long', year:'numeric'});
  const months = [...new Set(event.slots.flatMap(slot => [month.format(new Date(slot.start)), month.format(new Date(Date.parse(slot.end) - 1))]))].join(' · ');
  heading.append(element('p', finalEvent ? slotLabel(finalEvent, event.timezone, event.daily_hours) : months, 'agenda-timing'));
  if (finalEvent) heading.append(element('span', 'Confirmed', 'agenda-date-status'));
  const header = element('div', '', 'agenda-event-header');
  const actions = element('div', '', 'agenda-event-actions');
  const link = element('a', finalEvent ? 'Event details' : 'View event', 'text-link');
  link.href = eventHref(event.id, error ? null : token);
  link.setAttribute('aria-label', `${link.textContent} for ${event.name}`); actions.append(link);
  if (canPresent(invitation)) {
    const edit = element('button', invitation.presentation?.title ? 'Edit your talk' : 'Add a talk', 'secondary');
    edit.type = 'button'; edit.dataset.present = event.id; edit.disabled = saving;
    edit.addEventListener('click', () => openEditor(event.id)); actions.append(edit);
  }
  header.append(actions);
  const content = element('div', '', 'event-content'); content.append(header);
  const details = element('section', '', 'agenda-details');
  const pending = config.service_status !== 'ready';
  if (pending) details.append(element('p', config.service_status === 'pending' ? 'Agenda to follow.' : 'Agenda temporarily unavailable.', 'agenda-empty'));
  else if (error) details.append(element('p', 'This agenda could not be loaded.', 'agenda-empty'));
  else if (finalEvent && invitation) renderProgramme(details, invitation.state.programme, 'h3');
  if (details.childElementCount) content.append(details);
  const talks = invitation?.presentations || publicTalks;
  if (!error && !pending) content.append(invitation ? talkTable(event, talks, finalEvent)
    : talks.length ? presenterList(talks) : element('p', 'No public talks yet.', 'agenda-empty'));
  row.append(eventDisclosure(heading, content, open, expanded => expandedEvents.set(event.id, expanded)));
  $('agenda-list').append(row);
  return row;
}

function renderAgendas() {
  $('agenda-list').replaceChildren();
  const programme = entries.map(entry => ({...entry, event:{...selectEvent(config, entry.event.id), ...entry.event, ...entry.invitation?.poll}}));
  const next = nextSeminar(programme);
  programme.forEach(entry => {
    const open = expandedEvents.get(entry.event.id) ?? (entry === programme[0] || entry.event.id === requestedEvent);
    const row = renderEvent(entry.event, entry.invitation, entry.error, entry.presentations, open, entry.final_event);
    if (entry === next) row.prepend(element('p', 'Up next!', 'next-seminar-note'));
  });
  renderPresentationNudge($('presentation-nudge'), entries, token);
  if (!$('presentation-editor').hidden) $('presentation-nudge').hidden = true;
}

function selectedEntry() { return entries.find(entry => entry.event.id === $('presentation-event').value); }

function visibility() { return document.querySelector('input[name="visibility"]:checked').value; }

function updateVisibilityNote() {
  const provisional = $('talk-placeholder').checked;
  const publicChoice = document.querySelector('input[name="visibility"][value="public"]');
  publicChoice.disabled = provisional;
  if (provisional) document.querySelector('input[name="visibility"][value="internal"]').checked = true;
  if (provisional) {
    $('visibility-note').textContent = 'Provisional topics stay within the group. Update your title and uncheck the provisional option when your talk is ready.';
    return;
  }
  $('visibility-note').textContent = visibility() === 'public'
    ? 'Only your name will appear publicly. Your title, abstract and materials stay within the group. Choose Public only for mature topics nearing publication.'
    : 'Only invited participants can see your talk and materials.';
}

function currentFiles() {
  const talk = selectedEntry().invitation.presentation || {};
  for (const kind of ['explainer', 'slides']) {
    $(kind + '-current').textContent = talk.files?.[kind] ? 'Uploaded: ' + talk.files[kind].name : '';
  }
  $('withdraw-presentation').hidden = !talk.version;
  $('editor-title').textContent = talk.version ? 'Edit your talk' : 'Add your talk';
}

function rememberDraft() {
  if (!editingId) return;
  const draft = {title:$('talk-title').value, abstract:$('talk-abstract').value, status:$('talk-placeholder').checked ? 'placeholder' : 'talk', visibility:visibility(), ...readTiming(),
    explainer_url:$('explainer-url').value, slides_url:$('slides-url').value,
    uploads:Object.fromEntries(['explainer', 'slides'].map(kind => [kind, [...$(kind + '-file').files]]))};
  const saved = entries.find(entry => entry.event.id === editingId).invitation.presentation || {};
  const changed = ['title', 'abstract', 'explainer_url', 'slides_url'].some(key => draft[key] !== (saved[key] || ''));
  const timingChanged = ['presentation_minutes', 'questions_minutes'].some(key => draft[key] !== (saved[key] ?? null));
  if (changed || timingChanged || draft.status !== (saved.status || 'talk') || draft.visibility !== (saved.visibility || 'internal') || Object.values(draft.uploads).some(files => files.length)) drafts.set(editingId, draft);
  else drafts.delete(editingId);
}

function loadEditor() {
  editingId = selectedEntry().event.id;
  const draft = drafts.get(editingId);
  const talk = draft || selectedEntry().invitation.presentation || {};
  $('talk-title').value = talk.title || ''; $('talk-abstract').value = talk.abstract || '';
  $('talk-placeholder').checked = talk.status === 'placeholder';
  $('presentation-minutes').value = talk.presentation_minutes ?? ''; $('questions-minutes').value = talk.questions_minutes ?? '';
  updateTimingTotal();
  document.querySelectorAll('input[name="visibility"]').forEach(input => input.checked = input.value === (talk.visibility || 'internal'));
  updateVisibilityNote();
  for (const kind of ['explainer', 'slides']) {
    $(kind + '-url').value = talk[kind + '_url'] || ''; $(kind + '-file').value = '';
    if (draft?.uploads[kind].length) {
      const transfer = new DataTransfer();
      draft.uploads[kind].forEach(file => transfer.items.add(file)); $(kind + '-file').files = transfer.files;
    }
  }
  currentFiles(); setStatus(draft ? 'Draft restored. Save when ready.' : '');
}

function openEditor(eventId) {
  if (saving || !entries.some(entry => entry.event.id === eventId && canPresent(entry.invitation))) return;
  expandedEvents.set(eventId, true);
  rememberDraft(); $('presentation-event').value = eventId; loadEditor();
  $('presentation-editor').hidden = false; $('presentation-nudge').hidden = true;
  const url = new URL(location.href); url.searchParams.set('present', eventId); history.replaceState(null, '', url);
  $('editor-title').focus();
}

function closeEditor() {
  if (saving) return;
  rememberDraft(); $('presentation-editor').hidden = true;
  const url = new URL(location.href); url.searchParams.delete('present'); history.replaceState(null, '', url);
  expandedEvents.set(editingId, true);
  renderAgendas();
  [...document.querySelectorAll('[data-present]')].find(button => button.dataset.present === editingId)?.focus();
}

function updatePresentation(entry, saved) {
  expandedEvents.set(entry.event.id, true);
  entry.invitation.presentation = saved.presentation;
  entry.invitation.presentations = saved.presentations;
  currentFiles(); renderAgendas();
}

async function result(response) {
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || 'Your presentation could not be saved. Please try again.');
  return data;
}

async function savePresentation(remove = false) {
  const entry = selectedEntry();
  const proposal = {title:$('talk-title').value, abstract:$('talk-abstract').value, status:$('talk-placeholder').checked ? 'placeholder' : 'talk', visibility:visibility(), ...readTiming(),
    explainer_url:$('explainer-url').value, slides_url:$('slides-url').value};
  const files = ['explainer', 'slides'].map(kind => [kind, $(kind + '-file').files[0]]).filter(([, file]) => file);
  const status = $('presentation-status');
  if (!remove && files.some(([, file]) => file.size > 20 * 1024 * 1024)) {
    setStatus('Choose files of at most 20 MB each.', 'error'); return;
  }
  saving = true;
  document.querySelectorAll('#presentation-editor input, #presentation-editor textarea, #presentation-editor select, #presentation-editor button, [data-present]').forEach(node => node.disabled = true);
  $('presentation-form').setAttribute('aria-busy', 'true');
  $('save-presentation').textContent = remove ? 'Withdrawing…' : 'Saving…';
  setStatus(remove ? 'Withdrawing…' : 'Saving your talk…');
  let saved = false;
  try {
    const response = await apiFetch('presentation', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token, event_id:entry.event.id, version:entry.invitation.presentation?.version || '', presentation:proposal, remove})});
    updatePresentation(entry, await result(response)); saved = true;
    if (!remove) {
      for (const [kind, file] of files) {
        const response = await apiFetch('presentation-upload', {method:'POST', body:file, headers:{
          'X-Invitation-Token':token, 'X-Event-Id':entry.event.id, 'X-Material-Kind':kind,
          'X-File-Name':encodeURIComponent(file.name), 'X-Presentation-Version':entry.invitation.presentation.version
        }});
        updatePresentation(entry, await result(response)); $(kind + '-file').value = '';
      }
    }
    drafts.delete(entry.event.id);
    if (remove) loadEditor();
    showSaved(entry, remove);
  } catch (error) {
    setStatus((saved ? 'Talk saved. Materials could not all be uploaded: ' : remove ? 'Your proposal could not be withdrawn. ' : 'Your talk could not be saved. ') + error.message, 'error');
    status.focus({preventScroll:true}); status.scrollIntoView({block:'nearest'});
  } finally {
    saving = false;
    $('presentation-form').setAttribute('aria-busy', 'false'); $('save-presentation').textContent = 'Save talk';
    document.querySelectorAll('#presentation-editor input, #presentation-editor textarea, #presentation-editor select, #presentation-editor button, [data-present]').forEach(node => node.disabled = false);
    updateVisibilityNote();
  }
}

function setupEditor() {
  const editable = entries.filter(entry => canPresent(entry.invitation));
  if (!editable.length) return;
  for (const {event} of editable) {
    const option = element('option', event.name); option.value = event.id; $('presentation-event').append(option);
  }
  const requested = new URLSearchParams(location.search).get('present');
  if (editable.some(entry => entry.event.id === requested)) {
    openEditor(requested);
    if (new URLSearchParams(location.search).get('placeholder') === '1' && !selectedEntry().invitation.presentation?.title) {
      $('talk-placeholder').checked = true; updateVisibilityNote();
    }
  }
  $('presentation-event').addEventListener('change', () => openEditor($('presentation-event').value));
  $('close-editor').addEventListener('click', closeEditor);
  document.querySelectorAll('input[name="visibility"]').forEach(input => input.addEventListener('change', updateVisibilityNote));
  $('talk-placeholder').addEventListener('change', updateVisibilityNote);
  $('presentation-nudge').addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); openEditor(new URL(link.href).searchParams.get('present'));
  });
  $('presentation-form').addEventListener('input', () => { updateTimingTotal(); setStatus('Unsaved changes'); });
  $('presentation-form').addEventListener('invalid', event => { const details = event.target.closest('details'); if (details) details.open = true; }, true);
  $('presentation-form').addEventListener('submit', event => { event.preventDefault(); savePresentation(); });
  $('withdraw-presentation').addEventListener('click', () => savePresentation(true));
}

async function start() {
  config = await loadConfig();
  document.title = 'Agenda · D3 · ' + config.semester;
  entries = config.events.map(event => ({event}));
  if (config.service_status !== 'ready') {
    renderAgendas();
    $('agenda-notice').textContent = serviceNotice(config);
    $('agenda-notice').classList.toggle('error', config.service_status === 'unavailable');
    return;
  }
  if (token) {
    if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('Your personal link is incomplete. Please open the full link from your email.');
    entries = await loadProgramme(config, token, false, true);
    if (!entries.some(entry => entry.invitation)) throw new Error('Your invitation could not be loaded. Please reopen your email link or contact Aabhash.');
  } else {
    entries = await Promise.all(config.events.map(async event => {
      try {
        const response = await apiFetch('public-event', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({event_id:event.id})});
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Public talks could not be loaded.');
        return {event:{...event, ...data.poll, slots:data.final_event ? [data.final_event] : data.poll.slots}, presentations:data.presentations, final_event:data.final_event};
      } catch (error) { return {event, error:error.message}; }
    }));
  }
  renderAgendas(); setupEditor();
  const failed = entries.some(entry => entry.error);
  $('agenda-notice').hidden = Boolean(token) && !failed;
  $('agenda-notice').classList.toggle('error', failed);
  $('agenda-notice').textContent = failed ? 'Some agendas could not be loaded. Please try again.' : 'Public entries show presenter names only. Talk details and materials are shared with invited participants.';
  if ($('presentation-editor').hidden) {
    const target = $('agenda-' + new URLSearchParams(location.search).get('event'));
    if (target) {
      target.focus({preventScroll:true});
      target.scrollIntoView({block:'start'});
    }
  }
}

start().catch(error => { $('agenda-notice').textContent = error.message; $('agenda-notice').classList.add('error'); });
window.addEventListener('hashchange', () => location.reload());
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
