import {renderCalendar} from './calendar.js';
import {apiFetch, loadConfig, serviceNotice} from './api.js';
import {$, element, selectEvent, slotLabel, eventHref, durationLabel, deadlineLabel, preserveInvitationLinks, answeredCount, responseComplete, eventFinished, loadProgramme, nextEvent, nextSeminar, eventDisclosure, eventTimeline, programmeProgress, showSavedResponse, renderProgramme, renderPresentationNudge} from './shared.js';
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const requestedEvent = new URLSearchParams(location.search).get('event');
preserveInvitationLinks(token);
const expandedEvents = new Map();
let timeline;
function renderAgendaPreview(container, event, talks = null, invited = false) {
  container.replaceChildren();
  const header = element('div', '', 'agenda-preview-heading');
  const count = talks?.length;
  const heading = element('div', '', 'agenda-preview-title');
  heading.append(element('h4', 'Agenda'));
  if (count) heading.append(element('span', `${count} ${invited ? '' : 'public '}talk${count === 1 ? '' : 's'}`, 'agenda-preview-count'));
  const link = element('a', 'View →');
  link.href = 'agenda.html?' + new URLSearchParams({event:event.id}) + (invited ? '#' + new URLSearchParams({token}) : '');
  link.setAttribute('aria-label', 'View agenda for ' + event.name);
  header.append(heading, link); container.append(header);
  if (count) {
    const first = talks[0];
    const preview = element('p', '', 'agenda-preview-talk');
    const names = first.presenters?.map(person => person.name) || first.name.split(' & ');
    preview.append(element('strong', names.map(name => name.trim().split(/\s+/)[0]).join(' & ')));
    if (invited) preview.append(element('span', ' · ' + first.title));
    container.append(preview);
  } else if (talks !== null) container.append(element('p', `No ${invited ? '' : 'public '}talks yet`, 'agenda-preview-empty'));
}

function card(event, invited) {
  const node = document.createElement('article'); node.className = 'event-card';
  node.dataset.event = event.id;
  const title = element('h3', event.name);
  const heading = element('div', '', 'event-card-heading');
  const month = new Intl.DateTimeFormat('en-GB', {timeZone:event.timezone, month:'long', year:'numeric'});
  const months = [...new Set(event.slots.flatMap(slot => [month.format(new Date(slot.start)), month.format(new Date(Date.parse(slot.end) - 1))]))].join(' · ');
  heading.append(title, element('p', months, 'event-summary-period'));
  const deadline = element('p', '', 'event-reply-deadline'); deadline.hidden = true;
  heading.append(deadline);
  const metadata = element('div', '', 'event-metadata');
  if (event.kind === 'onsite') metadata.append(element('span', event.semester, 'event-term'), element('span', durationLabel(event)));
  metadata.hidden = !metadata.childElementCount;
  const calendar = element('div', '', 'event-calendar');
  renderCalendar(calendar, event);
  const summary = element('div', '', 'event-summary'); summary.hidden = true;
  const programme = element('section', '', 'event-programme'); programme.hidden = true;
  programme.setAttribute('aria-label', event.name + ' programme');
  const agenda = element('section', '', 'agenda-preview');
  agenda.setAttribute('aria-label', event.name + ' agenda preview');
  renderAgendaPreview(agenda, event, null, invited);
  const bottom = element('div', '', 'event-card-bottom');
  bottom.hidden = !invited;
  const status = element('span', '', 'event-status');
  status.hidden = true;
  const link = element('a', token ? 'Share availability' : 'View event', 'event-link');
  link.href = eventHref(event.id, token); link.setAttribute('aria-label', `${token ? 'Share availability for' : 'View'} ${event.name}`);
  bottom.append(status, link);
  const content = element('div', '', 'event-content'); content.append(metadata, summary, programme, calendar, agenda, bottom);
  const disclosure = eventDisclosure(heading, content, false, open => expandedEvents.set(event.id, open));
  node.append(disclosure);
  $('event-grid').append(node);
  return {node, disclosure, status, link, metadata, calendar, summary, programme, agenda, deadline};
}

function renderDeadline(entry, poll, finalized, completed = false) {
  entry.deadline.hidden = Boolean(finalized);
  entry.deadline.classList.toggle('preferences-saved', completed);
  if (finalized) return;
  if (completed) {
    entry.deadline.textContent = 'Thank you for setting your preferences.';
    return;
  }
  const time = element('time', deadlineLabel(poll)); time.dateTime = poll.deadline;
  entry.deadline.replaceChildren('Please set your preferences by ', time, '.');
}

function markNextSeminar(cards, programme = cards) {
  const next = nextSeminar(cards.map((entry, index) => ({...entry, invitation:programme[index].invitation})));
  for (const entry of cards) {
    entry.node.querySelector('.next-seminar-note')?.remove();
    if (entry.event.id === next?.event.id) entry.node.prepend(element('p', 'Up next!', 'next-seminar-note'));
    entry.disclosure.open = expandedEvents.get(entry.event.id) ?? (entry === cards[0] || entry.event.id === requestedEvent);
  }
  if (timeline) timeline.refresh();
  else timeline = eventTimeline($('event-grid'), cards.map(entry => entry.event), requestedEvent || cards[0].event.id);
}

function renderCard(entry, invitation) {
  entry.status.hidden = false;
  if (!invitation) {
    renderAgendaPreview(entry.agenda, entry.event);
    entry.status.textContent = 'Invitation unavailable'; entry.link.textContent = 'View event';
    entry.link.setAttribute('aria-label', `View ${entry.event.name}`);
    entry.link.href = eventHref(entry.event.id); return;
  }
  renderAgendaPreview(entry.agenda, entry.event, invitation.presentations || [], true);
  entry.node.querySelector('[data-organizer-link]')?.remove();
  if (invitation.is_organizer) {
    const manage = element('a', 'Organize event', 'text-link'); manage.dataset.organizerLink = '';
    manage.href = 'admin.html?' + new URLSearchParams({event:entry.event.id}) + '#' + new URLSearchParams({token});
    entry.node.querySelector('.event-card-bottom').append(manage);
  }
  const finalEvent = invitation.state.final_event;
  renderDeadline(entry, invitation.poll, finalEvent, responseComplete(invitation));
  if (invitation.state.final_event) {
    entry.summary.replaceChildren(element('p', slotLabel(finalEvent, invitation.poll.timezone, invitation.poll.daily_hours)));
    entry.summary.hidden = false;
    entry.status.textContent = 'Your attendance confirmed'; entry.link.textContent = 'View details';
    if (!entry.metadata.querySelector('.event-phase')) entry.metadata.append(element('span', 'Confirmed', 'event-phase'));
    entry.metadata.hidden = false;
    renderProgramme(entry.programme, invitation.state.programme);
  } else {
    entry.status.textContent = invitation.updated_at
      ? `Your response saved${responseComplete(invitation) ? '' : ` · ${answeredCount(invitation)}/${invitation.poll.slots.length} options`}`
      : 'Awaiting your response';
    entry.link.textContent = invitation.updated_at ? (responseComplete(invitation) ? 'Update availability' : 'Finish your response') : 'Share availability';
  }
  entry.link.setAttribute('aria-label', `${entry.link.textContent} for ${entry.event.name}`);
  renderCalendar(entry.calendar, {...entry.event, ...invitation.poll, slots:finalEvent ? [finalEvent] : invitation.poll.slots}, {
    confirmed:Boolean(finalEvent), people:invitation.participants,
    onSelect:date => {
      const destination = new URL(eventHref(entry.event.id, token), location.href);
      destination.searchParams.set('date', date);
      location.assign(destination.href);
    }
  });
}

function renderProgress(programme) {
  renderPresentationNudge($('presentation-nudge'), programme, token);
  const valid = programme.filter(entry => entry.invitation);
  const next = nextEvent(programme);
  const completed = valid.find(entry => responseComplete(entry.invitation));
  const month = entry => {
    const poll = entry.invitation.poll;
    const start = Math.min(...poll.slots.map(slot => Date.parse(slot.start)));
    return new Intl.DateTimeFormat('en-GB', {timeZone:poll.timezone, month:'long', year:'numeric'}).format(new Date(start));
  };
  const allAnswered = valid.length === programme.length && valid.every(entry => responseComplete(entry.invitation));
  $('home-intro').textContent = allAnswered
    ? `Thank you for setting your preferences for all ${programme.length} events.`
    : completed ? `Thank you for setting your preferences for ${completed === nextSeminar(programme) ? 'the next seminar' : completed.event.name} in ${month(completed)}.`
    : 'Please enter your availability for the next D3 seminars.';
  $('home-intro').classList.toggle('preferences-saved', Boolean(completed));
  $('home-deadline').hidden = !next;
  $('home-deadline').replaceChildren();
  if (next) {
    const link = element('a', `${next.event.name} in ${month(next)}`); link.href = eventHref(next.event.id, token);
    $('home-deadline').append(completed ? ' You can already set your preferences for ' : ' Please set your preferences for ', link);
    if (!completed) {
      const poll = next.invitation.poll;
      const time = element('time', deadlineLabel(poll)); time.dateTime = poll.deadline;
      $('home-deadline').append(' by ', time);
    }
    $('home-deadline').append('.');
  }
  $('journey-progress').textContent = programmeProgress(programme);
  $('programme-progress').hidden = false;
  $('progress-steps').replaceChildren();
  $('continue-link').hidden = !next;
  $('progress-title').textContent = next ? 'Confirm your availability'
    : valid.length === programme.length ? 'Your availability is confirmed' : 'Some invitations could not load';
  programme.forEach((entry, index) => {
    const confirmed = entry.invitation && eventFinished(entry.invitation);
    const partial = entry.invitation && answeredCount(entry.invitation) > 0;
    const state = !entry.invitation ? 'unavailable' : confirmed ? 'confirmed' : entry === next ? 'next' : 'pending';
    const item = element('li', '', 'step-' + state);
    const step = element(entry.invitation ? 'a' : 'span', '', 'progress-step');
    if (entry.invitation) step.href = eventHref(entry.event.id, token);
    if (entry === next) step.setAttribute('aria-current', 'step');
    const number = element('span', confirmed ? '✓' : String(index + 1).padStart(2, '0'), 'progress-step-number');
    number.setAttribute('aria-hidden', 'true');
    const status = confirmed ? 'Confirmed' : !entry.invitation ? 'Unavailable' : partial ? 'Finish response' : entry === next ? 'Up next' : 'To confirm';
    step.append(number, element('span', entry.event.name, 'progress-step-name'), element('span', status, 'progress-step-status'));
    item.append(step); $('progress-steps').append(item);
  });
  if (next) {
    $('continue-link').textContent = `Confirm ${next.event.name}`;
    const arrow = element('span', '→'); arrow.setAttribute('aria-hidden', 'true'); $('continue-link').append(arrow);
    $('continue-link').href = eventHref(next.event.id, token); $('continue-link').hidden = false;
  }
}

async function start() {
  const config = await loadConfig();
  document.title = 'D3 Seminars and Onsite';
  const cards = config.events.map(entry => ({event:selectEvent(config, entry.id), ...card(selectEvent(config, entry.id), config.service_status === 'ready' && Boolean(token))}));
  markNextSeminar(cards);
  if (config.service_status !== 'ready') {
    $('home-lead').hidden = false;
    $('home-intro').textContent = 'Dates and programme for the next D3 seminars.';
    $('home-notice').textContent = serviceNotice(config);
    $('home-notice').classList.toggle('error', config.service_status === 'unavailable');
    return;
  }
  if (!token) {
    $('home-notice').hidden = true;
    await Promise.all(cards.map(async entry => {
      try {
        const response = await apiFetch('public-event', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({event_id:entry.event.id})});
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Public responses could not be loaded.');
        entry.event = {...entry.event, ...data.poll, slots:data.final_event ? [data.final_event] : data.poll.slots};
        renderDeadline(entry, data.poll, data.final_event);
        renderCalendar(entry.calendar, entry.event, {confirmed:Boolean(data.final_event), totals:data.totals});
        renderAgendaPreview(entry.agenda, entry.event, data.presentations);
      } catch {
        entry.calendar.append(element('p', 'Availability totals could not be loaded. Please refresh to try again.', 'notice error'));
      }
    }));
    markNextSeminar(cards);
    return;
  }
  $('home-intro').textContent = 'Please enter your availability for the next D3 seminars.';
  $('home-lead').hidden = false;
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('Your personal link is incomplete. Please open the full link from your email.');
  const programme = await loadProgramme(config, token, true, true);
  markNextSeminar(cards, programme);
  cards.forEach((entry, index) => renderCard(entry, programme[index].invitation));
  const valid = programme.filter(entry => entry.invitation);
  if (!valid.length) throw new Error('Your invitation could not be loaded. Please reopen your email link or contact Aabhash.');
  const name = element('strong', ''); name.append(element('em', valid[0].invitation.name.trim().split(/\s+/)[0]));
  $('home-greeting').replaceChildren('Hello ', name, '. '); $('home-greeting').hidden = false;
  $('home-notice').hidden = true;
  renderProgress(programme);
  showSavedResponse(programme, token);
}
start().catch(error => { $('home-notice').textContent = error.message; $('home-notice').classList.add('error'); $('home-notice').hidden = false; });
window.addEventListener('hashchange', () => location.reload());
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
