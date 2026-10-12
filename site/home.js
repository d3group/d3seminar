import {apiFetch, loadConfig, serviceNotice} from './api.js';
import {$, element, selectEvent, slotLabel, eventHref, durationLabel, preserveInvitationLinks, answeredCount, responseComplete, eventFinished, loadProgramme, nextEvent, nextSeminar, eventDisclosure, eventTimeline, programmeProgress, showSavedResponse, voteCounts, renderProgramme, participantRings, ringArc, calendarDay, renderPresentationNudge, onsiteDays} from './shared.js';
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const requestedEvent = new URLSearchParams(location.search).get('event');
preserveInvitationLinks(token);
const expandedEvents = new Map();
let timeline;
const answerNames = {available:'Available', unavailable:'Unavailable', unknown:'Awaiting reply'};
const roleNames = {professor:'Professor', student:'Student'};
function answerFor(person, slot) {
  const answer = person.answers[slot.id || slot.slot_id];
  return ['available', 'unavailable'].includes(answer) ? answer : 'unknown';
}
function voteRing(people, slots, hint) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 60 60'); svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('vote-ring');
  for (const ring of participantRings(people)) {
    const group = document.createElementNS(svg.namespaceURI, 'g');
    group.classList.add('vote-group', 'vote-' + ring.role);
    ring.people.forEach((person, index) => {
      const personMarks = document.createElementNS(svg.namespaceURI, 'g'); personMarks.classList.add('vote-person');
      const answers = slots.map(slot => answerFor(person, slot));
      const segments = new Set(answers).size === 1 ? [answers[0]] : answers;
      segments.forEach((answer, segment) => {
        const label = `${person.name} · ${roleNames[ring.role]} — ${answerNames[answer]}`
          + (segments.length > 1 ? ` · Option ${segment + 1}` : '');
        const mark = document.createElementNS(svg.namespaceURI, 'path');
        const arc = ringArc(ring, index, segment, segments.length);
        mark.setAttribute('d', arc.path);
        mark.classList.add('vote-mark', 'vote-' + answer);
        const title = document.createElementNS(svg.namespaceURI, 'title'); title.textContent = label;
        mark.append(title);
        mark.addEventListener('pointerenter', () => { hint.textContent = label; hint.hidden = false; });
        mark.addEventListener('pointerleave', () => { hint.hidden = true; hint.textContent = ''; });
        personMarks.append(mark);
      });
      group.append(personMarks);
    });
    svg.append(group);
  }
  return svg;
}
function renderCalendar(container, event, confirmed = false, people = null, editor = null, totals = null) {
  const canInspect = Boolean(people || totals);
  const countsFor = slot => totals ? totals[slot.id || slot.slot_id] : voteCounts(people || [], slot, event.required_participants);
  const draft = {...editor?.invitation.answers};
  const changes = () => Object.fromEntries(event.slots.filter(slot => (draft[slot.id] || '') !== (editor.invitation.answers[slot.id] || '')).map(slot => ['answer:' + slot.id, draft[slot.id] || '']));
  let saving = false;
  const counts = new Map(event.slots.map(slot => [slot, countsFor(slot).available]));
  const highest = Math.max(0, ...counts.values());
  const standing = slots => confirmed ? 'confirmed' : highest > 0 && slots.some(slot => counts.get(slot) === highest) ? 'leading' : 'proposed';
  const standingNames = {proposed:'Proposed', leading:'Leading so far', confirmed:'Confirmed'};
  const dateFormat = new Intl.DateTimeFormat('en-GB', {timeZone:event.timezone, year:'numeric', month:'2-digit', day:'2-digit'});
  const dateKey = value => {
    const parts = Object.fromEntries(dateFormat.formatToParts(new Date(value)).map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  };
  const dates = new Map();
  for (const slot of event.slots) {
    const last = dateKey(Date.parse(slot.end) - 1);
    // Iterate calendar days, independently of the browser timezone and DST.
    for (const day = new Date(dateKey(slot.start) + 'T00:00:00Z'); day.toISOString().slice(0, 10) <= last; day.setUTCDate(day.getUTCDate() + 1)) {
      const key = day.toISOString().slice(0, 10);
      if (!dates.has(key)) dates.set(key, []);
      dates.get(key).push(slot);
    }
  }
  container.replaceChildren();
  const hint = element('p', '', 'calendar-vote-hint'); hint.hidden = true;
  const panel = element('section', '', 'calendar-votes');
  panel.id = 'votes-' + event.id; panel.hidden = true; panel.tabIndex = -1;
  panel.setAttribute('aria-label', 'Responses for ' + event.name);
  const pairedCells = [];
  const optionLines = [];
  const onsiteOptions = event.daily_hours?.last_day_end
    ? event.slots.map((slot, index) => ({slot, index, days:onsiteDays(slot, event.timezone, event.daily_hours)})) : [];
  const dateButtons = new Map();
  let selectedDate;
  function closeVotes() {
    if (saving) return;
    panel.hidden = true;
    pairedCells.forEach(({cell}) => cell.classList.remove('is-pair-selected'));
    optionLines.forEach(({line}) => line.classList.remove('is-option-selected'));
    selectedDate?.setAttribute('aria-expanded', 'false');
    selectedDate?.focus(); selectedDate = null;
  }
  function responseForm(slots) {
    const form = element('form', '', 'calendar-response-form');
    form.setAttribute('aria-label', 'Your availability for ' + event.name);
    form.append(element('h5', 'Your availability'));
    const status = element('p', editor.saved ? 'Response saved.' : '', 'calendar-save-status');
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const save = element('button', 'Save changes', 'primary'); save.type = 'submit';
    const update = (edited = false) => {
      const dirty = Object.keys(changes()).length > 0;
      if (edited) editor.saved = false;
      status.textContent = dirty ? 'Unsaved changes' : editor.saved ? 'Response saved.' : '';
      status.classList.remove('error'); save.disabled = !dirty;
      editor.onEdit(dirty);
    };
    for (const slot of slots) {
      const fieldset = element('fieldset', '', 'calendar-choice');
      fieldset.append(element('legend', slotLabel(slot, event.timezone, event.daily_hours)));
      const choices = element('div', '', 'availability-choices');
      for (const value of ['available', 'unavailable']) {
        const label = element('label', '', 'availability-choice');
        const input = document.createElement('input');
        input.type = 'radio'; input.name = 'answer:' + slot.id; input.value = value;
        input.checked = draft[slot.id] === value;
        input.addEventListener('change', () => { draft[slot.id] = value; update(true); });
        label.append(input, element('span', answerNames[value])); choices.append(label);
      }
      const clear = element('button', 'Clear answer', 'text-button'); clear.type = 'button';
      clear.addEventListener('click', () => {
        draft[slot.id] = '';
        choices.querySelectorAll('input').forEach(input => input.checked = false); update(true);
      });
      fieldset.append(choices, clear); form.append(fieldset);
    }
    const actions = element('div', '', 'calendar-save-actions'); actions.append(save, status); form.append(actions);
    update();
    form.addEventListener('submit', async submission => {
      submission.preventDefault();
      if (saving || !Object.keys(changes()).length) return;
      saving = true; status.textContent = 'Saving…'; status.classList.remove('error');
      container.querySelectorAll('button, input').forEach(control => control.disabled = true);
      try {
        const body = new URLSearchParams({token, event_id:event.id, revision:editor.invitation.revision, partial:'true', ...changes()});
        const response = await apiFetch('respond', {method:'POST', headers:{Accept:'application/json'}, body});
        const invitation = await response.json();
        if (!response.ok || !invitation.ok) throw new Error(invitation.error || 'Your response could not be saved. Please try again.');
        editor.onSave(invitation, selectedDate.dataset.selection);
      } catch (error) {
        status.textContent = error.message; status.classList.add('error');
        container.querySelectorAll('button, input').forEach(control => control.disabled = false);
      } finally { saving = false; }
    });
    return form;
  }
  function showVotes(date, slots) {
    if (saving) return;
    if (selectedDate === date) { closeVotes(); return; }
    selectedDate?.setAttribute('aria-expanded', 'false');
    selectedDate = date; date.setAttribute('aria-expanded', 'true');
    pairedCells.forEach(({cell, slots:pairedSlots}) => cell.classList.toggle('is-pair-selected', pairedSlots.some(slot => slots.includes(slot))));
    optionLines.forEach(({line, slot}) => line.classList.toggle('is-option-selected', slots.includes(slot)));
    panel.replaceChildren();
    const header = element('div', '', 'votes-heading');
    const close = element('button', 'Close', 'text-button'); close.type = 'button';
    close.setAttribute('aria-label', 'Close responses for ' + event.name);
    close.addEventListener('click', closeVotes);
    header.append(element('h4', 'Responses'), close); panel.append(header);
    if (editor && !confirmed) panel.append(responseForm(slots));
    for (const slot of slots) {
      panel.append(element('p', slotLabel(slot, event.timezone, event.daily_hours), 'votes-slot'));
      const counts = countsFor(slot);
      const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
      panel.append(element('p', `${counts.available} of ${total} available`, 'votes-count'));
      const breakdown = element('dl', '', 'vote-breakdown');
      for (const [state, count] of Object.entries(counts)) {
        const item = element('div', '', 'vote-' + state);
        item.append(element('dt', answerNames[state]), element('dd', String(count))); breakdown.append(item);
      }
      panel.append(breakdown);
      if (!people) continue;
      if (event.required_participants > people.length) panel.append(element('p', 'Awaiting reply includes people who have not been registered yet.', 'votes-count'));
      const list = element('ul', '', 'vote-people'); list.tabIndex = 0;
      list.setAttribute('aria-label', 'Participant responses for ' + slotLabel(slot, event.timezone, event.daily_hours));
      for (const person of people) {
        const answer = answerFor(person, slot);
        const row = element('li', '');
        const name = element('span', person.name);
        row.append(name, element('span', answerNames[answer], 'vote-' + answer));
        list.append(row);
      }
      panel.append(list);
    }
    panel.hidden = false; panel.focus();
  }
  panel.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closeVotes(); } });
  const months = [...new Set([...dates.keys()].sort().map(date => date.slice(0, 7)))];
  for (const month of months) {
    const first = new Date(month + '-01T00:00:00Z');
    const monthLabel = new Intl.DateTimeFormat('en-GB', {timeZone:'UTC', month:'long', year:'numeric'}).format(first);
    const table = element('table', '', 'calendar-month');
    const caption = element('caption', monthLabel);
    caption.setAttribute('aria-label', `${event.name}: ${monthLabel}, ${confirmed ? 'confirmed' : 'proposed'} dates`);
    const head = element('thead', '');
    const weekdays = element('tr', '');
    for (const name of ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']) {
      const cell = element('th', ''); cell.scope = 'col';
      if (['Saturday', 'Sunday'].includes(name)) cell.classList.add('calendar-weekend');
      const label = element('abbr', name.slice(0, 3)); label.title = name;
      cell.append(label); weekdays.append(cell);
    }
    head.append(weekdays);
    const body = element('tbody', '');
    const offset = (first.getUTCDay() + 6) % 7;
    const days = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
    for (let week = 0; week < Math.ceil((offset + days) / 7); week++) {
      const row = element('tr', '');
      const weekDates = new Map();
      for (let weekday = 0; weekday < 7; weekday++) {
        const cell = element('td', '');
        const day = week * 7 + weekday - offset + 1;
        if (day > 0 && day <= days) {
          const key = `${month}-${String(day).padStart(2, '0')}`;
          weekDates.set(key, weekday);
          const {weekend, holiday} = calendarDay(key);
          const dayNote = [holiday, weekend ? 'Weekend' : ''].filter(Boolean).join(' · ');
          cell.dataset.date = key;
          if (weekend || holiday) cell.classList.add('calendar-day-off');
          const slots = dates.get(key);
          const state = slots ? standing(slots) : null;
          if (event.duration_days > 1 && slots) {
            const previous = slots.some(slot => dateKey(slot.start) < key);
            const next = slots.some(slot => dateKey(Date.parse(slot.end) - 1) > key);
            if (previous || next) {
              cell.classList.add('calendar-pair', 'date-' + state);
              if (previous) cell.classList.add('pair-from-previous');
              if (next) cell.classList.add('pair-to-next');
              // Continuation arrows preserve the pairing across weeks or months.
              if (previous && (weekday === 0 || day === 1)) cell.classList.add('pair-wrap-in');
              if (next && (weekday === 6 || day === days)) cell.classList.add('pair-wrap-out');
              pairedCells.push({cell, slots});
              if (onsiteOptions.length) cell.classList.add('calendar-with-options');
            }
          }
          const date = element(slots ? (canInspect || !token ? 'button' : 'a') : 'span', '', 'calendar-date');
          if (dayNote) date.title = dayNote;
          date.append(element('span', String(day), 'calendar-day-number'));
          if (slots) {
            date.classList.add('date-' + state);
            const label = `${standingNames[state]}: ${slots.map(slot => slotLabel(slot, event.timezone, event.daily_hours)).join('; ')}`;
            date.title = dayNote ? `${dayNote}. ${label}` : label;
            date.setAttribute('aria-label', `${day} ${monthLabel}. ${dayNote ? dayNote + '. ' : ''}${label}. ${canInspect ? 'Show responses for' : 'View'} ${event.name}.`);
            if (canInspect) {
              date.dataset.selection = key;
              date.type = 'button'; date.setAttribute('aria-expanded', 'false'); date.setAttribute('aria-controls', panel.id);
              if (people) date.append(voteRing(people, slots, hint));
              date.addEventListener('click', () => showVotes(date, slots));
              dateButtons.set(key, {date, slots});
            } else if (token) {
              date.href = eventHref(event.id, token); date.title = dayNote ? `${dayNote}. ${label}` : label;
            } else { date.type = 'button'; date.disabled = true; }
          }
          cell.append(date);
        }
        row.append(cell);
      }
      body.append(row);
      const tracks = element('div', '', 'calendar-option-tracks');
      for (const {slot, index, days:optionDays} of onsiteOptions) {
        const visible = optionDays.filter(day => weekDates.has(day.date));
        if (!visible.length) continue;
        const line = element('span', '', `calendar-option option-colour-${index % 3} date-${standing([slot])}`);
        const label = `Option ${index + 1}. ${standingNames[standing([slot])]}: ${slotLabel(slot, event.timezone, event.daily_hours)}`;
        line.setAttribute('role', 'img'); line.setAttribute('aria-label', label); line.title = label;
        line.style.gridColumn = `${weekDates.get(visible[0].date) + 1} / span ${visible.length}`;
        line.style.gridRow = tracks.childElementCount + 1;
        line.style.setProperty('--day-count', visible.length);
        for (const day of visible) {
          const mark = element('span', day.fraction === 0.5 ? '½' : '', 'calendar-option-day' + (day.fraction < 1 ? ' calendar-option-half' : ''));
          mark.setAttribute('aria-hidden', 'true'); line.append(mark);
        }
        for (const [side, continues, arrow] of [['in', visible[0] !== optionDays[0], '‹'], ['out', visible.at(-1) !== optionDays.at(-1), '›']]) {
          if (!continues) continue;
          line.classList.add('option-continues-' + side);
          const mark = element('span', arrow, 'option-continuation option-continuation-' + side);
          mark.setAttribute('aria-hidden', 'true'); line.append(mark);
        }
        tracks.append(line); optionLines.push({line, slot});
      }
      if (tracks.childElementCount) {
        const trackRow = element('tr', '', 'calendar-options-row');
        const cell = element('td', ''); cell.colSpan = 7;
        cell.append(tracks); trackRow.append(cell); body.append(trackRow);
      }
    }
    table.append(caption, head, body); container.append(table);
  }
  if (confirmed) container.append(element('p', 'Confirmed dates', 'calendar-note date-confirmed'));
  if (people) {
    const legend = element('ul', '', 'vote-key');
    const states = ['available', 'unavailable', 'unknown'];
    for (const state of states) {
      const item = element('li', '');
      const mark = element('span', '', 'vote-swatch vote-' + state); mark.setAttribute('aria-hidden', 'true');
      item.append(mark, element('span', answerNames[state])); legend.append(item);
    }
    container.append(legend, hint);
    if (people.some(person => !roleNames[person.role])) container.append(element('p', 'People awaiting a role appear in the response list.', 'calendar-vote-hint'));
  }
  if (canInspect) {
    container.append(panel);
    const open = dateButtons.get(editor?.openDate);
    if (open) showVotes(open.date, open.slots);
  }
}
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
    preview.append(element('strong', first.name.trim().split(/\s+/)[0]));
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
  return {node, disclosure, status, link, metadata, calendar, summary, programme, agenda};
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

function renderCard(entry, invitation, onSave, openDate = null, saved = false) {
  entry.status.hidden = false;
  if (!invitation) {
    renderAgendaPreview(entry.agenda, entry.event);
    entry.status.textContent = 'Invitation unavailable'; entry.link.textContent = 'View event';
    entry.link.setAttribute('aria-label', `View ${entry.event.name}`);
    entry.link.href = eventHref(entry.event.id); return;
  }
  renderAgendaPreview(entry.agenda, entry.event, invitation.presentations || [], true);
  const finalEvent = invitation.state.final_event;
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
  const savedStatus = entry.status.textContent;
  renderCalendar(entry.calendar, {...entry.event, ...invitation.poll, slots:finalEvent ? [finalEvent] : invitation.poll.slots}, Boolean(finalEvent), invitation.participants, {
    invitation, onSave, openDate, saved, onEdit:dirty => { entry.status.textContent = dirty ? 'Unsaved changes' : savedStatus; }
  });
}

function renderProgress(programme) {
  renderPresentationNudge($('presentation-nudge'), programme, token);
  $('journey-progress').textContent = programmeProgress(programme);
  $('programme-progress').hidden = false;
  const valid = programme.filter(entry => entry.invitation);
  const next = nextEvent(programme);
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
  document.title = 'D3 · ' + config.semester;
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
        renderCalendar(entry.calendar, entry.event, Boolean(data.final_event), null, null, data.totals);
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
  cards.forEach((entry, index) => {
    const onSave = (invitation, openDate) => {
      invitation.presentations ??= programme[index].invitation?.presentations;
      programme[index].invitation = invitation;
      renderCard(entry, invitation, onSave, openDate, true);
      renderProgress(programme);
    };
    renderCard(entry, programme[index].invitation, onSave);
  });
  const valid = programme.filter(entry => entry.invitation);
  if (!valid.length) throw new Error('Your invitation could not be loaded. Please reopen your email link or contact Aabhash.');
  const name = element('strong', ''); name.append(element('em', valid[0].invitation.name));
  $('home-greeting').replaceChildren('Hello ', name, '. '); $('home-greeting').hidden = false;
  $('home-notice').hidden = true;
  renderProgress(programme);
  showSavedResponse(programme, token);
}
start().catch(error => { $('home-notice').textContent = error.message; $('home-notice').classList.add('error'); $('home-notice').hidden = false; });
window.addEventListener('hashchange', () => location.reload());
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
