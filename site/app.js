const $ = id => document.getElementById(id);
let config, poll, token, demo = false;
const choices = [['available', 'Available'], ['if_needed', 'If necessary'], ['unavailable', 'Unavailable']];
const fmt = (value, options) => new Intl.DateTimeFormat('en-GB', {timeZone:poll.timezone, ...options}).format(new Date(value));
const date = value => fmt(value, {weekday:'short', day:'numeric', month:'short', year:'numeric'});
const time = value => fmt(value, {hour:'2-digit', minute:'2-digit'});

function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  $('notice').hidden = !message;
}

function progress() {
  const answered = $('poll-options').querySelectorAll('input:checked').length;
  $('answer-progress').textContent = `${answered} of ${poll.slots.length} options answered. ${answered < poll.slots.length ? 'You can save now and finish later.' : 'Ready to save.'}`;
}

function renderPoll(answers = {}) {
  $('poll-meta').textContent = `${(new Date(poll.slots[0].end) - new Date(poll.slots[0].start)) / 60000} minutes · All times in ${poll.timezone}`;
  $('poll-deadline').textContent = `Please reply by ${date(poll.deadline)}, ${time(poll.deadline)}.`;
  $('poll-options').replaceChildren();
  for (const slot of poll.slots) {
    const fieldset = document.createElement('fieldset');
    fieldset.className = 'poll-option';
    fieldset.dataset.slot = slot.id;
    const legend = document.createElement('legend');
    legend.textContent = `${date(slot.start)} · ${time(slot.start)}–${time(slot.end)}`;
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
    clear.addEventListener('click', () => { row.querySelectorAll('input').forEach(input => input.checked = false); progress(); });
    fieldset.append(row, clear); $('poll-options').append(fieldset);
  }
  progress();
}

function showDemo() {
  demo = true;
  $('status-pill').textContent = 'DEMO';
  $('greeting').textContent = 'Try the poll. This preview does not save anything.';
  $('response-form').hidden = false;
  $('preview-actions').hidden = true;
  $('demo-receipt').hidden = true;
  $('submit-button').textContent = 'Preview my availability ↗';
  notice('Demo mode · No response will be stored.');
}

async function calendar() {
  try {
    const response = await fetch('/api/calendar', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token})});
    if (!response.ok) throw new Error('The calendar could not be downloaded. Please reload and try again.');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(await response.blob()); link.download = 'd3-seminar.ics'; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (error) { notice(error.message, true); }
}

async function start() {
  const response = await fetch('config.json', {cache:'no-store'});
  if (!response.ok) throw new Error('The seminar details could not be loaded. Please try again.');
  config = await response.json(); poll = config;
  document.title = config.title;
  $('subtitle').textContent = config.subtitle;
  $('pilot-banner').hidden = !config.test_mode;
  $('edition').textContent = config.test_mode ? 'MVP / PILOT' : 'SEMINAR';
  const params = new URLSearchParams(location.hash.slice(1)); token = params.get('token');
  $('calendar').addEventListener('click', calendar);
  $('demo-button').addEventListener('click', showDemo);
  $('demo-reset').addEventListener('click', showDemo);
  $('response-form').addEventListener('change', progress);
  for (const answer of ['available', 'unavailable']) {
    $(`all-${answer}`).addEventListener('click', () => {
      $('poll-options').querySelectorAll(`input[value="${answer}"]`).forEach(input => input.checked = true); progress();
    });
  }
  $('response-form').addEventListener('submit', event => {
    if (demo) {
      event.preventDefault(); $('response-form').hidden = true; $('demo-receipt').hidden = false;
      notice('Preview complete · Nothing was saved.');
    } else { $('submit-button').disabled = true; $('submit-button').textContent = 'Saving your availability…'; }
  });
  window.addEventListener('pageshow', () => {
    $('submit-button').disabled = false;
    $('submit-button').textContent = demo ? 'Preview my availability ↗' : 'Save my availability ↗';
  });
  if (!token) {
    renderPoll(); notice('Open the personal link in your invitation email to save your availability.');
    $('preview-actions').hidden = false;
    if (params.get('demo') === '1') showDemo();
    return;
  }
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('This invitation link is incomplete. Please open the full link from your email.');
  const backend = config.backend_url ? new URL(config.backend_url) : null;
  if (!backend || backend.origin !== location.origin || backend.pathname !== '/api/respond') {
    throw new Error('Responses are not open on this site yet. Please reply to your invitation email.');
  }
  const invitationResponse = await fetch('/api/invitation', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token})});
  const invitation = await invitationResponse.json();
  if (!invitationResponse.ok || !invitation.ok) throw new Error(invitation.error || 'Your invitation could not be loaded.');
  // Read current options from the backend, so existing email links survive a new round.
  poll = invitation.poll; renderPoll(invitation.answers);
  $('greeting').textContent = `Hello ${invitation.name}. Please mark all the times that work for you.`;
  $('response-form').action = config.backend_url;
  $('token').value = token; $('revision').value = invitation.revision;
  $('comment').value = invitation.comment;
  const state = invitation.state;
  if (state.final_event) {
    $('status-pill').textContent = 'CONFIRMED';
    $('card-title').textContent = 'See you there.';
    $('greeting').textContent = `Hello ${invitation.name}. We found a time everyone can attend.`;
    const event = state.final_event;
    $('final-session').hidden = false;
    $('final-time').textContent = `${date(event.start)} · ${time(event.start)}–${time(event.end)} · ${poll.timezone}`;
    $('session-location').textContent = event.location;
    $('calendar').hidden = false;
    $('poll-deadline').hidden = true;
    notice('The time is finalized. Please contact Aabhash if your availability changes.');
    return;
  }
  $('status-pill').textContent = invitation.updated_at ? 'SAVED' : 'INVITATION';
  $('response-form').hidden = false;
  const count = Object.keys(invitation.answers).length;
  notice(invitation.updated_at ? `Your saved answers are shown below (${count}/${poll.slots.length}). You can update them here.` : 'Nothing is preselected. After saving, you’ll see a receipt.');
  const proposed = poll.slots.find(slot => slot.id === state.proposed_slot_id);
  if (proposed && invitation.answers[proposed.id] === 'if_needed') {
    const fieldset = [...$('poll-options').children].find(row => row.dataset.slot === proposed.id);
    fieldset.classList.add('needs-confirmation');
    notice(`Can you commit to ${date(proposed.start)}, ${time(proposed.start)}? Change “If necessary” to “Available” or “Unavailable”, then save.`);
  }
}

start().catch(error => { notice(error.message, true); $('response-form').hidden = true; });
window.addEventListener('hashchange', () => location.reload());
