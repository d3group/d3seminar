import {element, slotLabel, voteCounts, participantRings, ringArc, calendarDay, onsiteDays} from './shared.js';

const answerNames = {available:'Available', unavailable:'Unavailable', unknown:'Awaiting reply'};
function answerFor(person, slot) {
  const answer = person.answers[slot.id || slot.slot_id];
  return ['available', 'unavailable'].includes(answer) ? answer : 'unknown';
}
function voteRing(people, slots) {
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
        const mark = document.createElementNS(svg.namespaceURI, 'path');
        const arc = ringArc(ring, index, segment, segments.length);
        mark.setAttribute('d', arc.path);
        mark.classList.add('vote-mark', 'vote-' + answer);
        personMarks.append(mark);
      });
      group.append(personMarks);
    });
    svg.append(group);
  }
  return svg;
}
export function renderCalendar(container, event, {confirmed = false, people = null, totals = null, onSelect = null, openDate = null, responseContainer = null, onInspect = () => {}} = {}) {
  const canInspect = Boolean(people || totals);
  const countsFor = slot => totals ? totals[slot.id || slot.slot_id] : voteCounts(people || [], slot, event.required_participants);
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
    panel.hidden = true; onInspect([]);
    pairedCells.forEach(({cell}) => cell.classList.remove('is-pair-selected'));
    optionLines.forEach(({line}) => line.classList.remove('is-option-selected'));
    selectedDate?.setAttribute('aria-expanded', 'false');
    selectedDate?.focus({preventScroll:true}); selectedDate = null;
  }
  function showVotes(date, slots) {
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
    onInspect(slots);
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
    panel.hidden = false; panel.focus({preventScroll:true});
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
          const date = element(slots ? 'button' : 'span', '', 'calendar-date');
          date.append(element('span', String(day), 'calendar-day-number'));
          if (slots) {
            date.classList.add('date-' + state);
            const label = `${standingNames[state]}: ${slots.map(slot => slotLabel(slot, event.timezone, event.daily_hours)).join('; ')}`;
            date.setAttribute('aria-label', `${day} ${monthLabel}. ${dayNote ? dayNote + '. ' : ''}${label}. ${onSelect ? 'Enter availability for' : canInspect ? 'Show responses for' : 'View'} ${event.name}.`);
            if (canInspect || onSelect) {
              date.dataset.selection = key;
              date.type = 'button';
              if (!onSelect) { date.setAttribute('aria-expanded', 'false'); date.setAttribute('aria-controls', panel.id); }
              if (people) date.append(voteRing(people, slots));
              date.addEventListener('click', () => onSelect ? onSelect(key, slots) : showVotes(date, slots));
              dateButtons.set(key, {date, slots});
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
    container.append(legend);
  }
  if (canInspect && !onSelect) {
    if (responseContainer) responseContainer.replaceChildren(panel);
    else container.append(panel);
    const open = dateButtons.get(openDate);
    if (open) showVotes(open.date, open.slots);
  }
  return {
    showSlot(id, trigger) {
      const slot = event.slots.find(slot => slot.id === id);
      if (slot && canInspect && !onSelect) showVotes(trigger, [slot]);
    }
  };
}
