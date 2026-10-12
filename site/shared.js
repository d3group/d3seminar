import {apiFetch} from './api.js';

export const $ = id => document.getElementById(id);

export function element(tag, text, className) {
  const node = document.createElement(tag); node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function selectEvent(config, id = 'seminar-1') {
  const event = config.events.find(event => event.id === id);
  if (!event) throw new Error('This event could not be found. Return to the programme to choose an event.');
  return {...config, ...event, event_id:event.id};
}

export function nextSeminar(entries, now = Date.now()) {
  let next, earliest = Infinity;
  for (const entry of entries) {
    if (entry.event.kind !== 'seminar') continue;
    const final = entry.invitation?.state.final_event;
    const slots = final ? [final] : entry.invitation?.poll?.slots || entry.event.slots;
    for (const slot of slots) {
      const start = Date.parse(slot.start);
      if (Date.parse(slot.end) > now && start < earliest) { next = entry; earliest = start; }
    }
  }
  return next;
}

export function eventDisclosure(heading, content, open, onToggle) {
  const details = element('details', '', 'event-disclosure'); details.open = open;
  const summary = element('summary', '', 'event-disclosure-summary');
  const arrows = element('span', '›››', 'event-disclosure-arrows'); arrows.setAttribute('aria-hidden', 'true');
  summary.append(heading, arrows);
  summary.addEventListener('click', () => onToggle(!details.open));
  details.append(summary, content);
  return details;
}

export function eventTimeline(container, events, initialId) {
  const wrapper = element('section', '', 'event-timeline');
  wrapper.setAttribute('aria-label', 'Event timeline'); wrapper.setAttribute('aria-roledescription', 'carousel');
  const arrows = element('div', '', 'timeline-arrows');
  const arrow = (text, label, direction) => {
    const button = element('button', text); button.type = 'button';
    button.setAttribute('aria-label', label); button.setAttribute('aria-controls', container.id);
    button.addEventListener('click', () => move(index + direction * count())); arrows.append(button);
    return button;
  };
  const previous = arrow('←', 'Earlier events', -1), next = arrow('→', 'Later events', 1);
  const status = element('p', '', 'sr-only'); status.setAttribute('aria-live', 'polite');
  const stage = element('div', '', 'timeline-stage');
  wrapper.append(status, stage);
  container.before(wrapper); stage.append(arrows, container); container.classList.add('timeline-track');
  container.tabIndex = 0; container.setAttribute('aria-label', 'Events, from earlier to later');
  const mobile = matchMedia('(max-width: 680px)'), reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const count = () => mobile.matches ? 1 : 2;
  const last = () => Math.max(0, events.length - count());
  const step = () => container.firstElementChild.getBoundingClientRect().width + parseFloat(getComputedStyle(container).columnGap);
  let index = Math.max(0, events.findIndex(event => event.id === initialId)), width = 0;
  function update() {
    index = Math.min(last(), Math.max(0, Math.round(container.scrollLeft / step())));
    [...container.children].forEach((card, i) => { card.inert = i < index || i >= index + count(); });
    previous.setAttribute('aria-disabled', String(index === 0)); next.setAttribute('aria-disabled', String(index === last()));
    const text = 'Showing ' + events.slice(index, index + count()).map(event => event.name).join(' and ');
    if (status.textContent !== text) status.textContent = text;
    const box = container.getBoundingClientRect();
    const heights = [...container.children].filter(card => {
      const rect = card.getBoundingClientRect();
      return rect.right > box.left + 8 && rect.left < box.right - 8;
    }).map(card => card.getBoundingClientRect().height);
    container.style.height = `${Math.ceil(Math.max(0, ...heights)) + 48}px`;
  }
  function move(target, behavior = 'smooth') {
    const position = Math.max(0, Math.min(last(), target));
    container.scrollTo({left:position * step(), behavior:reduced.matches ? 'instant' : behavior});
    update();
  }
  wrapper.addEventListener('keydown', event => {
    if (event.target !== container && !arrows.contains(event.target)) return;
    const target = {ArrowLeft:index - count(), ArrowRight:index + count(), Home:0, End:last()}[event.key];
    if (target == null) return;
    event.preventDefault(); move(target);
  });
  container.addEventListener('scroll', update, {passive:true});
  container.addEventListener('focusin', event => {
    const card = event.target.closest('[data-event]');
    if (!card) return;
    const target = events.findIndex(entry => entry.id === card.dataset.event);
    if (target < index || target >= index + count()) move(target, 'instant');
  });
  const cardsObserver = new ResizeObserver(update);
  const resizeObserver = new ResizeObserver(() => {
    if (width === container.clientWidth) return;
    width = container.clientWidth; move(index, 'instant');
  });
  resizeObserver.observe(container);
  function refresh() {
    cardsObserver.disconnect();
    [...container.children].forEach((card, i) => {
      card.setAttribute('aria-label', `${events[i].name}, ${i + 1} of ${events.length}`);
      card.setAttribute('aria-roledescription', 'slide'); cardsObserver.observe(card);
    });
    move(index, 'instant');
  }
  refresh();
  return {refresh};
}

export function durationLabel(event) {
  if (event.duration_days) {
    const hours = event.daily_hours;
    const days = hours?.last_day_end ? event.duration_days - 1 + dayFraction(hours, hours.last_day_end) : event.duration_days;
    const label = days % 1 === 0.5 ? `${Math.floor(days)}½` : String(Math.round(days * 100) / 100);
    return `${label} days${hours ? ` · ${dailyHoursLabel(hours)}` : ''}`;
  }
  const minutes = (new Date(event.slots[0].end) - new Date(event.slots[0].start)) / 60000;
  return minutes % 60 === 0 ? `${minutes / 60} hours` : `${minutes} minutes`;
}

export function agendaSchedule(event, talks, finalEvent = null) {
  const minutes = value => { const [hour, minute] = value.split(':').map(Number); return hour * 60 + minute; };
  const clock = value => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
  let windows;
  if (event.daily_hours) {
    const hours = event.daily_hours;
    windows = Array.from({length:event.duration_days}, (_, day) => ({start:minutes(hours.start),
      end:minutes(day === event.duration_days - 1 ? hours.last_day_end || hours.end : hours.end)}));
  } else {
    const format = new Intl.DateTimeFormat('en-GB', {timeZone:event.timezone, hour:'2-digit', minute:'2-digit', hourCycle:'h23'});
    const options = (finalEvent ? [finalEvent] : event.slots).map(slot => ({
      start:minutes(format.format(new Date(slot.start))), end:minutes(format.format(new Date(slot.end)))
    }));
    if (!options.length || options.some(slot => slot.start !== options[0].start || slot.end !== options[0].end || slot.end <= slot.start)) {
      return {slots:talks.map(() => ({reason:'Time to be agreed'})), open:talks.length ? [] : [{reason:'Time to be agreed'}]};
    }
    windows = [options[0]];
  }
  let day = 0, cursor = windows[0].start, missingTiming = false;
  const open = [];
  const remaining = () => {
    const end = windows[day].end;
    if (cursor < end) open.push({start:clock(cursor), end:clock(end), day:day + 1, minutes:end - cursor});
  };
  const slots = talks.map(talk => {
    if (talk.presentation_minutes == null || talk.questions_minutes == null) {
      missingTiming = true; return {reason:'Duration to follow'};
    }
    if (missingTiming) return {reason:'Awaiting earlier timings'};
    const duration = talk.presentation_minutes + talk.questions_minutes;
    while (day < windows.length && cursor + duration > windows[day].end) {
      remaining();
      day++; cursor = windows[day]?.start;
    }
    if (day === windows.length) return {reason:'Needs more time'};
    const start = cursor; cursor += duration;
    return {start:clock(start), end:clock(cursor), day:day + 1};
  });
  while (!missingTiming && day < windows.length) {
    remaining(); day++; cursor = windows[day]?.start;
  }
  return {slots, open};
}

export function dailyHoursLabel(hours) {
  const full = `${hours.start}–${hours.end}`;
  return hours.last_day_end ? `${full}; final day ${hours.start}–${hours.last_day_end}` : `${full} each day`;
}

function dayFraction(hours, end) {
  const minutes = value => value.split(':').reduce((hour, minute) => hour * 60 + Number(minute), 0);
  return (minutes(end) - minutes(hours.start)) / (minutes(hours.end) - minutes(hours.start));
}

export function onsiteDays(slot, timezone, hours) {
  const format = new Intl.DateTimeFormat('en-CA', {timeZone:timezone, year:'numeric', month:'2-digit', day:'2-digit'});
  const first = format.format(new Date(slot.start)), last = format.format(new Date(Date.parse(slot.end) - 1));
  const days = [];
  for (const day = new Date(first + 'T00:00:00Z'); day.toISOString().slice(0, 10) <= last; day.setUTCDate(day.getUTCDate() + 1)) {
    const date = day.toISOString().slice(0, 10);
    const end = date === last ? hours.last_day_end || hours.end : hours.end;
    days.push({date, start:hours.start, end, fraction:dayFraction(hours, end)});
  }
  return days;
}

export function calendarDay(key) {
  const date = new Date(key + 'T00:00:00Z');
  const fixed = {'01-01':'New Year’s Day', '01-06':'Epiphany', '05-01':'Labour Day', '08-15':'Assumption Day',
    '10-03':'German Unity Day', '11-01':'All Saints’ Day', '12-25':'Christmas Day', '12-26':'Boxing Day'};
  const easter = {'2026':'2026-04-05', '2027':'2027-03-28'}[key.slice(0, 4)];
  const offset = easter ? (date - new Date(easter + 'T00:00:00Z')) / 86400000 : null;
  const movable = {'-2':'Good Friday', 1:'Easter Monday', 39:'Ascension Day', 50:'Whit Monday', 60:'Corpus Christi'};
  const university = key === '2026-11-18' ? 'JMU teaching holiday · Day of Repentance'
    : key >= '2026-12-24' && key <= '2027-01-06' ? 'JMU Christmas break' : '';
  return {weekend:[0, 6].includes(date.getUTCDay()), holiday:fixed[key.slice(5)] || movable[offset] || university};
}

export function slotLabel(slot, timezone, dailyHours = slot.daily_hours) {
  const format = options => new Intl.DateTimeFormat('en-GB', {timeZone:timezone, ...options});
  const day = value => format({weekday:'short', day:'numeric', month:'short', year:'numeric'}).format(new Date(value));
  const time = value => format({hour:'2-digit', minute:'2-digit'}).format(new Date(value));
  if (dailyHours) return `${day(slot.start)} → ${day(slot.end)} · ${dailyHoursLabel(dailyHours)}`;
  return day(slot.start) === day(slot.end)
    ? `${day(slot.start)} · ${time(slot.start)}–${time(slot.end)}`
    : `${day(slot.start)}, ${time(slot.start)} → ${day(slot.end)}, ${time(slot.end)}`;
}

export function eventHref(id, token) {
  return 'event.html?' + new URLSearchParams({event:id}) + (token ? '#' + new URLSearchParams({token}) : '');
}

export function answeredCount(invitation) {
  return invitation.poll.slots.filter(slot => ['available', 'unavailable'].includes(invitation.answers[slot.id])).length;
}

export function responseComplete(invitation) {
  return answeredCount(invitation) === invitation.poll.slots.length;
}

export function eventFinished(invitation) {
  return Boolean(invitation.state.final_event) || responseComplete(invitation);
}

export function nextEvent(entries, currentId) {
  const index = entries.findIndex(entry => entry.event.id === currentId);
  const ordered = index < 0 ? entries : [...entries.slice(index + 1), ...entries.slice(0, index)];
  return ordered.find(entry => entry.invitation && !eventFinished(entry.invitation));
}

export function voteCounts(people, slot, required) {
  const counts = {available:0, unavailable:0, unknown:0};
  people.forEach(person => {
    const answer = person.answers[slot.id || slot.slot_id];
    counts[['available', 'unavailable'].includes(answer) ? answer : 'unknown']++;
  });
  counts.unknown += Math.max(0, required - people.length);
  return counts;
}

export function participantRings(people) {
  return [
    {role:'professor', label:'Professors', capacity:5, radius:[14, 20], people:people.filter(person => person.role === 'professor')},
    {role:'student', label:'Students', capacity:15, radius:[23, 29], people:people.filter(person => person.role === 'student')}
  ];
}

export function ringArc(ring, index, part = 0, parts = 1) {
  const width = (ring.radius[1] - ring.radius[0]) / parts;
  const radius = ring.radius[0] + (part + 0.5) * width;
  const middle = (ring.radius[0] + ring.radius[1]) / 2;
  const step = 2 * Math.PI / ring.capacity;
  const sweep = step - 1.5 / middle;
  const angle = index * step - Math.PI / 2;
  const inner = radius - width / 2, outer = radius + width / 2;
  const start = angle - sweep / 2, end = angle + sweep / 2;
  const point = (radius, angle) => `${30 + radius * Math.cos(angle)} ${30 + radius * Math.sin(angle)}`;
  const path = `M ${point(outer, start)} A ${outer} ${outer} 0 0 1 ${point(outer, end)}`
    + ` L ${point(inner, end)} A ${inner} ${inner} 0 0 0 ${point(inner, start)} Z`;
  return {radius, width, sweep, path};
}

export function renderProgramme(container, programme = {}, heading = 'h4') {
  container.replaceChildren();
  container.append(element(heading, programme.topic || 'Programme to follow'));
  for (const [key, label] of [['presenters', 'Presenters'], ['room', 'Room'], ['agenda', 'Agenda']]) {
    if (!programme[key]) continue;
    const text = document.createElement('p');
    text.append(element('strong', label + ': '), document.createTextNode(programme[key])); container.append(text);
  }
  container.hidden = false;
}

export async function loadProgramme(config, token, includeVotes = false, includePresentations = false) {
  return Promise.all(config.events.map(async event => {
    try {
      const response = await apiFetch('invitation', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token, event_id:event.id, include_votes:includeVotes, include_presentations:includePresentations})});
      const invitation = await response.json();
      if (!response.ok || !invitation.ok) throw new Error(invitation.error || 'Invitation unavailable.');
      return {event, invitation};
    } catch (error) { return {event, error:error.message}; }
  }));
}

export function presentationProgress(entries) {
  const student = entries.filter(entry => entry.invitation?.role === 'student');
  if (!student.length) return null;
  const proposed = entry => Boolean(entry.invitation.presentation?.title);
  const detailsPending = entry => proposed(entry) && !entry.invitation.presentation.abstract;
  const seminars = student.filter(entry => entry.event.kind === 'seminar');
  const onsite = student.find(entry => entry.event.kind === 'onsite');
  const seminar = seminars.some(proposed), onsiteDone = Boolean(onsite && proposed(onsite));
  const next = !seminar ? seminars[0] : !onsiteDone ? onsite : seminars.find(proposed);
  const text = seminar && onsiteDone ? 'Your seminar and onsite proposals are saved. You can add details and materials later.'
    : seminar ? 'Please propose a talk for the onsite.'
    : onsiteDone ? 'Please propose a talk for at least one of the three seminars.'
    : 'Please propose a talk for at least one of the three seminars and for the onsite.';
  return {seminar, onsite:onsiteDone, next:next?.event.id, text,
    seminarDetailsPending:seminars.some(detailsPending), onsiteDetailsPending:Boolean(onsite && detailsPending(onsite))};
}

export function renderPresentationNudge(container, entries, token) {
  const progress = presentationProgress(entries);
  container.hidden = !progress;
  if (!progress) return;
  const copy = document.createElement('div');
  const steps = element('p', '', 'presentation-milestones');
  const status = (added, pending) => !added ? 'to add' : pending ? 'added (details to follow)' : 'added';
  steps.textContent = `Seminar proposal: ${status(progress.seminar, progress.seminarDetailsPending)} · Onsite proposal: ${status(progress.onsite, progress.onsiteDetailsPending)}`;
  copy.append(element('h2', 'Your presentations'), element('p', progress.text), steps);
  const link = element('a', progress.seminar && progress.onsite ? 'Update your presentations' : 'Propose a talk', 'text-link');
  link.href = 'agenda.html' + (progress.next ? '?' + new URLSearchParams({present:progress.next}) : '') + '#' + new URLSearchParams({token});
  container.replaceChildren(copy, link);
}

export function programmeProgress(entries) {
  const complete = entries.filter(entry => entry.invitation && eventFinished(entry.invitation)).length;
  return `You’ve confirmed ${complete} of ${entries.length} events.`
    + (entries.some(entry => !entry.invitation) ? ' Some invitations could not be loaded.' : '')
    + (complete === entries.length ? ' You’re all done for now.' : '');
}

export function showSavedResponse(entries, token) {
  const url = new URL(location.href);
  const savedId = url.searchParams.get('saved');
  if (!savedId) return;
  url.searchParams.delete('saved'); history.replaceState(null, '', url);
  const entry = entries.find(entry => entry.event.id === savedId);
  if (!entry?.invitation?.updated_at) return;
  const banner = $('saved-response');
  const text = document.createElement('p');
  const count = answeredCount(entry.invitation), total = entry.invitation.poll.slots.length;
  text.textContent = `Response saved for ${entry.event.name}. ${count} of ${total} options answered.`
    + (count < total && !entry.invitation.state.final_event ? ' You can finish it later.' : '');
  const link = element('a', 'Review saved response'); link.href = eventHref(savedId, token);
  banner.replaceChildren(text, link); banner.hidden = false;
}

export function preserveInvitationLinks(token) {
  if (!/^[A-Za-z0-9_-]{43}$/.test(token || '')) return;
  document.querySelectorAll('[data-invitation-link]').forEach(link => {
    const url = new URL(link.href);
    if (url.origin !== location.origin) return;
    url.hash = new URLSearchParams({token}).toString();
    link.href = url.href;
  });
}
