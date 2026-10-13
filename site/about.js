import {loadConfig} from './api.js';
import {preserveInvitationLinks, participantRings, orderedRingResponses, ringArc, loadProgramme} from './shared.js';

const token = new URLSearchParams(location.hash.slice(1)).get('token');
preserveInvitationLinks(token);
if (token) loadConfig().then(config => {
  if (config.service_status === 'ready') return loadProgramme(config, token);
}).catch(() => {}); // Keep the guide readable if organizer access cannot be checked.

const people = [
  {name:'Person A', role:'professor'}, {name:'Person B', role:'student'},
  {name:'Person C', role:'student'}, {name:'Person D', role:'student'}
];
const examples = {4:['available', 'available', 'unavailable', 'unknown'], 5:['unavailable', 'unknown', 'available', 'available']};
const labels = {available:'Available', unavailable:'Unavailable', unknown:'Awaiting reply'};
const ns = 'http://www.w3.org/2000/svg';
function mark(ring, index, answer, part = 0, parts = 1) {
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', ringArc(ring, index, part, parts).path);
  path.setAttribute('class', `vote-mark vote-${answer} vote-${ring.role}`);
  return path;
}
for (const [day, answers] of Object.entries(examples)) {
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 60 60'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('vote-ring');
  const replies = people.map((person, index) => ({...person, answers:{example:answers[index]}}));
  for (const ring of participantRings(replies)) {
    orderedRingResponses(ring, [{id:'example'}]).forEach(({answers, index}) => svg.append(mark(ring, index, answers[0])));
  }
  document.querySelector(`[data-guide-ring="${day}"]`).append(svg);
}
function showExample(day) {
  document.querySelectorAll('[data-example-date]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.exampleDate === day)));
  document.querySelector('.guide-response-title').textContent = `${day} November · sample responses`;
  const rows = people.map((person, index) => {
    const answer = examples[day][index];
    const row = document.createElement('li'); row.className = 'vote-' + person.role;
    const name = document.createElement('span'); name.textContent = person.name;
    const status = document.createElement('span'); status.className = 'guide-answer guide-answer-' + answer; status.textContent = labels[answer];
    row.append(name, status); return row;
  });
  document.querySelector('#guide-people ul').replaceChildren(...rows);
}
document.querySelectorAll('[data-example-date]').forEach(button => button.addEventListener('click', () => showExample(button.dataset.exampleDate)));
showExample('4');
const splitRing = participantRings([])[1];
const split = document.getElementById('guide-split-mark');
split.append(mark(splitRing, 0, 'available', 0, 2), mark(splitRing, 0, 'unavailable', 1, 2));
