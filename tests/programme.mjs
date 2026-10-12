import assert from 'node:assert/strict';
import {test} from 'node:test';
import {answeredCount, responseComplete, nextEvent, nextSeminar, voteCounts, participantRings, ringArc, calendarDay, durationLabel, deadlineLabel, slotLabel, onsiteDays, agendaSchedule, presentationProgress} from '../site/shared.js';

const poll = {required_participants:20, slots:[{id:'a'}, {id:'b'}]};
const invitation = (answers = {}, state = {}) => ({poll, answers, state});
const entry = (id, answers = {}, state = {}) => ({event:{id}, invitation:invitation(answers, state)});

test('reply deadlines use the event timezone through daylight-saving changes', () => {
  assert.equal(deadlineLabel({deadline:'2026-10-16T15:00:00Z', timezone:'Europe/Berlin'}), 'Friday, 16 October 2026');
  assert.equal(deadlineLabel({deadline:'2026-11-10T16:00:00Z', timezone:'Europe/Berlin'}), 'Tuesday, 10 November 2026');
});

test('saved titles count as proposals while missing abstracts remain details to follow', () => {
  const entries = ['seminar-1', 'seminar-2', 'seminar-3', 'onsite'].map(id => ({
    event:{id, kind:id === 'onsite' ? 'onsite' : 'seminar'}, invitation:{role:'student', presentation:{}}
  }));
  let progress = presentationProgress(entries);
  assert.equal(progress.seminar, false);
  assert.equal(progress.onsite, false);
  assert.equal(progress.next, 'seminar-1');
  entries[2].invitation.presentation = {title:'Provisional research topic', status:'placeholder'};
  progress = presentationProgress(entries);
  assert.equal(progress.seminar, true);
  assert.equal(progress.seminarDetailsPending, true);
  assert.equal(progress.next, 'onsite');
  entries[3].invitation.presentation = {title:'Onsite discussion', abstract:''};
  progress = presentationProgress(entries);
  assert.equal(progress.seminar, true);
  assert.equal(progress.onsite, true);
  assert.equal(progress.onsiteDetailsPending, true);
  assert.match(progress.text, /proposals are saved/);
  assert.doesNotMatch(progress.text, /Please propose|Please add/);
  entries[2].invitation.presentation.abstract = 'A provisional abstract.';
  entries[3].invitation.presentation.abstract = 'An onsite abstract.';
  progress = presentationProgress(entries);
  assert.equal(progress.seminarDetailsPending, false);
  assert.equal(progress.onsiteDetailsPending, false);
  entries[0].invitation.presentation = {title:'Another idea'};
  assert.equal(presentationProgress(entries).seminarDetailsPending, true);
  entries[0].invitation.presentation = {};
  entries[2].invitation.presentation = {};
  progress = presentationProgress(entries);
  assert.equal(progress.seminar, false);
  assert.equal(progress.seminarDetailsPending, false);
  assert.equal(progress.onsite, true);
  assert.equal(progress.next, 'seminar-1');
  assert.equal(presentationProgress(entries.map(entry => ({...entry, invitation:{...entry.invitation, role:'professor'}}))), null);
});

test('the next seminar follows meeting dates, including finalized dates, rather than poll completion', () => {
  const slot = day => ({start:`${day}T14:00:00+01:00`, end:`${day}T16:00:00+01:00`});
  const first = {event:{id:'first', kind:'seminar', slots:[slot('2026-11-02'), slot('2026-11-13')]},
    invitation:invitation({a:'yes', b:'yes'})};
  first.invitation.poll = {...poll, slots:first.event.slots.map((slot, index) => ({...slot, id:['a', 'b'][index]}))};
  const second = {event:{id:'second', kind:'seminar', slots:[slot('2026-12-01')]}};
  const onsite = {event:{id:'onsite', kind:'onsite', slots:[slot('2026-10-12')]}};
  const programme = [onsite, second, first];
  const next = date => nextSeminar(programme, Date.parse(date));
  assert.equal(next('2026-10-04'), first);
  assert.equal(next('2026-11-03'), first);
  assert.equal(next('2026-11-14'), second);
  first.invitation.state.final_event = slot('2026-11-02');
  assert.equal(next('2026-11-02T15:00:00+01:00'), first);
  assert.equal(next('2026-11-02T16:00:00+01:00'), second);
  assert.equal(next('2026-12-02'), undefined);
  assert.equal(nextSeminar([], Date.parse('2026-10-04')), undefined);
});

test('agenda clock slots follow local event hours, known durations and the current order', () => {
  const event = {timezone:'Europe/Berlin', slots:[
    {start:'2026-11-02T14:00:00+01:00', end:'2026-11-02T16:00:00+01:00'},
    {start:'2026-11-03T14:00:00+01:00', end:'2026-11-03T16:00:00+01:00'}
  ]};
  const talk = (presentation_minutes = 20, questions_minutes = 10) => ({presentation_minutes, questions_minutes});
  assert.deepEqual(agendaSchedule(event, [talk(), talk(), talk(60, 0), talk()]).slots, [
    {start:'14:00', end:'14:30', day:1}, {start:'14:30', end:'15:00', day:1},
    {start:'15:00', end:'16:00', day:1}, {reason:'Needs more time'}
  ]);
  assert.deepEqual(agendaSchedule(event, [talk(), talk(20, null), talk()]).slots, [
    {start:'14:00', end:'14:30', day:1}, {reason:'Duration to follow'}, {reason:'Awaiting earlier timings'}
  ]);
  const alternative = {start:'2026-11-04T10:00:00+01:00', end:'2026-11-04T12:00:00+01:00'};
  const mixed = {...event, slots:[...event.slots, alternative]};
  assert.deepEqual(agendaSchedule(mixed, [talk()]).slots, [{reason:'Time to be agreed'}]);
  assert.deepEqual(agendaSchedule(mixed, [talk()], alternative).slots, [{start:'10:00', end:'10:30', day:1}]);
  const onsite = {...event, duration_days:3, daily_hours:{start:'09:00', end:'17:00', last_day_end:'13:00'}};
  assert.deepEqual(agendaSchedule(onsite, [talk(480, 0), talk(470, 0), talk(), talk(210, 0), talk()]).slots, [
    {start:'09:00', end:'17:00', day:1}, {start:'09:00', end:'16:50', day:2},
    {start:'09:00', end:'09:30', day:3}, {start:'09:30', end:'13:00', day:3}, {reason:'Needs more time'}
  ]);
});

test('empty agenda slots cover only known free time, including gaps between onsite days', () => {
  const event = {timezone:'Europe/Berlin', slots:[{start:'2026-11-02T14:00:00+01:00', end:'2026-11-02T16:00:00+01:00'}]};
  const talk = (presentation_minutes, questions_minutes = 0) => ({presentation_minutes, questions_minutes});
  assert.deepEqual(agendaSchedule(event, []).open, [{start:'14:00', end:'16:00', day:1, minutes:120}]);
  assert.deepEqual(agendaSchedule(event, [talk(25, 10)]).open, [{start:'14:35', end:'16:00', day:1, minutes:85}]);
  assert.deepEqual(agendaSchedule(event, [talk(120)]).open, []);
  assert.deepEqual(agendaSchedule(event, [talk(25, null)]).open, []);
  assert.deepEqual(agendaSchedule(event, [talk(25, 10), talk(null)]).open, []);
  const onsite = {...event, duration_days:3, daily_hours:{start:'09:00', end:'17:00', last_day_end:'13:00'}};
  assert.deepEqual(agendaSchedule(onsite, []).open, [
    {start:'09:00', end:'17:00', day:1, minutes:480}, {start:'09:00', end:'17:00', day:2, minutes:480},
    {start:'09:00', end:'13:00', day:3, minutes:240}
  ]);
  assert.deepEqual(agendaSchedule(onsite, [talk(470), talk(30)]).open, [
    {start:'16:50', end:'17:00', day:1, minutes:10}, {start:'09:30', end:'17:00', day:2, minutes:450},
    {start:'09:00', end:'13:00', day:3, minutes:240}
  ]);
  const mixed = {...event, slots:[...event.slots, {start:'2026-11-03T10:00:00+01:00', end:'2026-11-03T12:00:00+01:00'}]};
  assert.deepEqual(agendaSchedule(mixed, []).open, [{reason:'Time to be agreed'}]);
});

test('onsite branches preserve local dates through DST and show the final morning', () => {
  const hours = {start:'09:00', end:'17:00', last_day_end:'13:00'};
  const slot = {start:'2026-10-24T09:00:00+02:00', end:'2026-10-26T13:00:00+01:00'};
  assert.deepEqual(onsiteDays(slot, 'Europe/Berlin', hours), [
    {date:'2026-10-24', start:'09:00', end:'17:00', fraction:1},
    {date:'2026-10-25', start:'09:00', end:'17:00', fraction:1},
    {date:'2026-10-26', start:'09:00', end:'13:00', fraction:0.5}
  ]);
  assert.match(durationLabel({duration_days:3, daily_hours:hours}), /2½ days/);
  assert.match(slotLabel(slot, 'Europe/Berlin', hours), /final day 09:00–13:00/);
  assert.deepEqual(onsiteDays(slot, 'Europe/Berlin', {start:'09:00', end:'17:00'}).map(day => day.fraction), [1, 1, 1]);
});

test('calendar days identify Würzburg holidays and JMU breaks across the year boundary without browser timezone shifts', () => {
  assert.deepEqual(calendarDay('2026-11-02'), {weekend:false, holiday:''});
  assert.deepEqual(calendarDay('2026-11-07'), {weekend:true, holiday:''});
  assert.deepEqual(calendarDay('2026-11-01'), {weekend:true, holiday:'All Saints’ Day'});
  assert.match(calendarDay('2026-11-18').holiday, /JMU/);
  assert.equal(calendarDay('2026-12-23').holiday, '');
  for (const date of ['2026-12-24', '2026-12-28', '2026-12-31', '2027-01-04']) {
    assert.equal(calendarDay(date).holiday, 'JMU Christmas break');
  }
  assert.equal(calendarDay('2027-01-01').holiday, 'New Year’s Day');
  assert.equal(calendarDay('2027-01-06').holiday, 'Epiphany');
  assert.equal(calendarDay('2027-01-07').holiday, '');
  assert.equal(calendarDay('2026-04-03').holiday, 'Good Friday');
  assert.equal(calendarDay('2027-03-26').holiday, 'Good Friday');
  assert.equal(calendarDay('2027-03-29').holiday, 'Easter Monday');
  assert.equal(calendarDay('2027-05-06').holiday, 'Ascension Day');
  assert.equal(calendarDay('2027-05-17').holiday, 'Whit Monday');
  assert.equal(calendarDay('2027-05-27').holiday, 'Corpus Christi');
  assert.equal(calendarDay('2027-08-15').holiday, 'Assumption Day');
  assert.equal(calendarDay('2027-08-08').holiday, '');
});

test('saved progress uses current options; continuation wraps and skips completed or closed polls', () => {
  assert.equal(answeredCount(invitation({a:'available', old:'available'})), 1);
  assert.equal(responseComplete(invitation({a:'available', old:'available'})), false);
  assert.equal(responseComplete(invitation({a:'available', b:'unavailable'})), true);
  const entries = [entry('one', {a:'available'}), entry('two', {}, {final_event:{}}), entry('three'), {event:{id:'four'}, error:'Unavailable'}];
  assert.equal(nextEvent(entries, 'one').event.id, 'three');
  assert.equal(nextEvent(entries, 'three').event.id, 'one');
  assert.equal(nextEvent(entries).event.id, 'one');
  entries[0].invitation.answers.b = 'unavailable';
  assert.equal(nextEvent(entries, 'three'), undefined);
});

test('date summaries treat legacy conditional replies and missing responses as awaiting', () => {
  const people = ['available', 'if_needed', 'unavailable', undefined].map(answer => ({answers:answer ? {a:answer} : {}}));
  assert.deepEqual(voteCounts(people, {id:'a'}, 5), {available:1, unavailable:1, unknown:3});
  assert.deepEqual(voteCounts(people, {slot_id:'a'}, 2), {available:1, unavailable:1, unknown:2});
});

test('participant rings retain their minimum size and accommodate added members', () => {
  const people = [{name:'Student', role:'student'}, {name:'Professor', role:'professor'}, {name:'Unassigned'}];
  const [inner, outer] = participantRings(people);
  assert.equal(inner.capacity, 5);
  assert.equal(outer.capacity, 15);
  assert.deepEqual(inner.people.map(p => p.name), ['Professor']);
  assert.deepEqual(outer.people.map(p => p.name), ['Student']);
  assert.ok(inner.radius[1] < outer.radius[0]);
  const expanded = participantRings([...Array.from({length:16}, () => ({role:'student'})), {role:'member'}]);
  assert.equal(expanded[1].people.length, 17);
  assert.equal(expanded[1].capacity, 17);
});

test('five broad professor arcs and fifteen student arcs fill their rings with gaps; shared options divide each band', () => {
  const [inner, outer] = participantRings([]);
  const professor = ringArc(inner, 0), student = ringArc(outer, 0);
  assert.ok(professor.sweep > student.sweep);
  for (const ring of [inner, outer]) {
    const arc = ringArc(ring, 0);
    assert.ok(arc.sweep * ring.capacity < 2 * Math.PI);
    assert.ok(arc.sweep * ring.capacity > 0.8 * 2 * Math.PI);
    assert.equal(arc.radius - arc.width / 2, ring.radius[0]);
    assert.equal(arc.radius + arc.width / 2, ring.radius[1]);
    const first = ringArc(ring, 0, 0, 2), second = ringArc(ring, 0, 1, 2);
    assert.equal(first.radius + first.width / 2, second.radius - second.width / 2);
    assert.equal(first.width + second.width, arc.width);
    assert.equal(first.sweep, second.sweep);
    assert.notEqual(ringArc(ring, 0).path, ringArc(ring, 1).path);
  }
});
