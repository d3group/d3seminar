import {apiFetch, loadConfig, apiUrl, serviceNotice} from './api.js';
import {selectEvent, slotLabel, durationLabel, dailyHoursLabel, preserveInvitationLinks, eventHref, answeredCount, eventFinished, nextEvent, loadProgramme, programmeProgress, showSavedResponse, renderProgramme} from './shared.js';
preserveInvitationLinks(new URLSearchParams(location.hash.slice(1)).get('token'));
const $ = id => document.getElementById(id);
let config, poll, token, programme = [], saving = false;
const eventId = new URLSearchParams(location.search).get('event') || 'seminar-1';
const choices = [['available', 'Available'], ['unavailable', 'Unavailable']];
const fmt = (value, options) => new Intl.DateTimeFormat('en-GB', {timeZone:poll.timezone, ...options}).format(new Date(value));
const date = value => fmt(value, {weekday:'short', day:'numeric', month:'short', year:'numeric'});
const time = value => fmt(value, {hour:'2-digit', minute:'2-digit'});

function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  $('notice').hidden = !message;
}

function progress(edited = false) {
  const answered = $('poll-options').querySelectorAll('input:checked').length;
  $('answer-progress').textContent = `${answered} of ${poll.slots.length} options answered. ${answered < poll.slots.length ? 'You can save now and finish later.' : 'Ready to save.'}`;
  if (edited) {
    $('status-pill').textContent = 'Unsaved changes';
    $('saved-response').hidden = true;
  }
}

function renderJourney() {
  $('journey-progress').textContent = programmeProgress(programme);
  $('journey-events').replaceChildren();
  for (const {event, invitation} of programme) {
    const row = document.createElement('li');
    const label = document.createElement(invitation ? 'a' : 'span');
    label.textContent = event.name;
    if (invitation) label.href = eventHref(event.id, token);
    if (event.id === eventId) label.setAttribute('aria-current', 'step');
    const status = document.createElement('span');
    status.textContent = !invitation ? 'Unavailable' : invitation.state.final_event ? 'Confirmed'
      : eventFinished(invitation) ? 'Complete' : invitation.updated_at ? `${answeredCount(invitation)}/${invitation.poll.slots.length} answered` : 'To do';
    row.append(label, status); $('journey-events').append(row);
  }
  $('response-journey').hidden = false;
  const next = nextEvent(programme, eventId);
  $('submit-button').textContent = next ? `Save and continue to ${next.event.name}` : 'Save and return to programme';
  const current = programme.find(entry => entry.event.id === eventId).invitation;
  $('continue-link').hidden = !current.state.final_event || !next;
  if (next) {
    $('continue-link').href = eventHref(next.event.id, token);
    $('continue-link').textContent = `Continue to ${next.event.name}`;
  }
}

async function saveResponse(event) {
  event.preventDefault();
  if (saving) return;
  const advance = event.submitter?.value !== 'stay';
  const form = $('response-form');
  const body = new URLSearchParams(new FormData(form));
  saving = true; form.inert = true; form.setAttribute('aria-busy', 'true');
  $('submit-button').disabled = true; $('save-later').disabled = true;
  notice('Saving your availability…');
  try {
    let response, saved;
    try {
      response = await apiFetch('respond', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded', Accept:'application/json'}, body});
      saved = await response.json();
    } catch {
      throw new Error('Could not confirm the save. Your answers are still here. Please try saving again.');
    }
    if (!response.ok || !saved.ok) throw new Error(saved.error || 'Your response could not be saved. Please try again.');
    const current = programme.find(entry => entry.event.id === eventId);
    Object.assign(current.invitation, saved);
    $('comment').value = saved.comment;
    $('status-pill').textContent = 'Saved';
    renderJourney();
    if (advance) {
      const next = nextEvent(programme, eventId);
      const destination = new URL(next ? eventHref(next.event.id, token) : './#' + new URLSearchParams({token}), location.href);
      destination.searchParams.set('saved', eventId);
      location.assign(destination.href);
    } else {
      const count = answeredCount(current.invitation);
      notice(`Response saved. Your availability is saved for ${count} of ${poll.slots.length} options.`
        + (count < poll.slots.length ? ' You can finish the remaining options later.' : ' You can update it here.'));
    }
  } catch (error) {
    notice(error.message, true);
  } finally {
    saving = false; form.inert = false; form.removeAttribute('aria-busy');
    $('submit-button').disabled = false; $('save-later').disabled = false;
  }
}

function renderPoll(answers = {}) {
  $('poll-meta').textContent = `${durationLabel(poll)} · All times in ${poll.timezone}`;
  $('poll-deadline').textContent = `Please reply by ${date(poll.deadline)}, ${time(poll.deadline)}.`;
  $('poll-options').replaceChildren();
  for (const slot of poll.slots) {
    const fieldset = document.createElement('fieldset');
    fieldset.className = 'poll-option';
    fieldset.dataset.slot = slot.id;
    const legend = document.createElement('legend');
    legend.textContent = slotLabel(slot, poll.timezone, poll.daily_hours);
    fieldset.append(legend);
    const row = document.createElement('div');
    row.className = 'availability-choices';
    for (const [value, title] of choices) {
      const label = document.createElement('label');
      label.className = 'availability-choice';
      const input = document.createElement('input');
      input.type = 'radio'; input.name = `answer:${slot.id}`; input.value = value;
      input.checked = answers[slot.id] === value;
      const text = document.createElement('span'); text.textContent = title;
      label.append(input, text); row.append(label);
    }
    const clear = document.createElement('button');
    clear.type = 'button'; clear.className = 'clear-answer'; clear.textContent = 'Clear';
    clear.setAttribute('aria-label', `Clear answer for ${date(slot.start)} at ${time(slot.start)}`);
    clear.addEventListener('click', () => { row.querySelectorAll('input').forEach(input => input.checked = false); progress(true); });
    fieldset.append(row, clear); $('poll-options').append(fieldset);
  }
  progress();
}

async function calendar() {
  try {
    const response = await apiFetch('calendar', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token, event_id:eventId})});
    if (!response.ok) throw new Error('The calendar could not be downloaded. Please reload and try again.');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(await response.blob()); link.download = 'd3-seminar.ics'; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (error) { notice(error.message, true); }
}

async function start() {
  token = new URLSearchParams(location.hash.slice(1)).get('token');
  if (!token) {
    location.replace('./?' + new URLSearchParams({event:eventId}));
    return;
  }
  config = selectEvent(await loadConfig(), eventId); poll = config;
  document.title = config.name + ' · ' + config.semester;
  $('event-semester').textContent = config.semester;
  $('event-title').textContent = config.name;
  $('event-description').textContent = 'A date will be confirmed once everyone is available.';
  $('event-position').textContent = `${config.events.findIndex(event => event.id === eventId) + 1} of ${config.events.length}`;
  $('event-id').value = eventId;
  if (config.service_status !== 'ready') {
    $('status-pill').textContent = 'Not open';
    $('card-title').textContent = 'Availability';
    $('greeting').textContent = 'Please return to your invitation later.';
    notice(serviceNotice(config), config.service_status === 'unavailable');
    return;
  }
  if (config.kind === 'onsite') {
    $('poll-help').textContent = `Each option covers ${config.duration_days} consecutive dates, ${dailyHoursLabel(config.daily_hours)}. Mark Available if you can attend the whole meeting. If you can attend only part, choose Unavailable and leave a note below.`;
    $('all-available').textContent = 'All onsite options work';
    $('all-unavailable').textContent = 'None of these work';
  }
  const home = './' + (token ? '#' + new URLSearchParams({token}) : '');
  $('back-home').href = home; document.querySelector('.brand').href = home;
  $('calendar').addEventListener('click', calendar);
  $('response-form').addEventListener('input', () => progress(true));
  for (const answer of ['available', 'unavailable']) {
    $(`all-${answer}`).addEventListener('click', () => {
      $('poll-options').querySelectorAll(`input[value="${answer}"]`).forEach(input => input.checked = true); progress(true);
    });
  }
  $('response-form').addEventListener('submit', saveResponse);
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('This invitation link is incomplete. Please open the full link from your email.');
  programme = await loadProgramme(config, token);
  const current = programme.find(entry => entry.event.id === eventId);
  const invitation = current.invitation;
  if (!invitation) throw new Error(current.error || 'Your invitation could not be loaded.');
  renderJourney(); showSavedResponse(programme, token);
  // Read current options from the backend, so existing email links survive a new round.
  poll = invitation.poll; renderPoll(invitation.answers);
  $('greeting').textContent = `Hello ${invitation.name}. Please mark all the times that work for you.`;
  $('response-form').action = apiUrl('respond');
  $('token').value = token; $('revision').value = invitation.revision;
  $('comment').value = invitation.comment;
  const state = invitation.state;
  if (state.final_event) {
    $('status-pill').textContent = 'Confirmed';
    $('card-title').textContent = 'Confirmed date';
    $('greeting').textContent = `Hello ${invitation.name}. We found a time everyone can attend.`;
    const event = state.final_event;
    $('final-session').hidden = false;
    $('final-time').textContent = `${slotLabel(event, poll.timezone)} · ${poll.timezone}`;
    $('session-location').textContent = event.location;
    renderProgramme($('final-programme'), state.programme);
    $('calendar').hidden = false;
    $('poll-deadline').hidden = true;
    notice('The time is finalized. Please contact Aabhash if your availability changes.');
    return;
  }
  $('status-pill').textContent = invitation.updated_at ? 'Saved' : 'Not answered';
  $('response-form').hidden = false;
  const count = Object.keys(invitation.answers).length;
  notice(invitation.updated_at ? `Your saved answers are shown below (${count}/${poll.slots.length}). You can update them here.` : 'Nothing is preselected. Save and continue when you’re ready.');

}

start().catch(error => { notice(error.message, true); $('response-form').hidden = true; });
window.addEventListener('hashchange', () => location.reload());
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
