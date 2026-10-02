const $ = id => document.getElementById(id);
let config, slot, demo = false;

export function eventData(config, slot) {
  return {campaign_id: config.campaign_id, end: slot.end, location: config.location,
    slot_id: slot.id, start: slot.start, timezone: config.timezone, title: config.title};
}

export async function eventRevision(config, slot) {
  const bytes = new TextEncoder().encode(JSON.stringify(eventData(config, slot)));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
}

function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  $('notice').hidden = !message;
}

function showDemo() {
  demo = true;
  $('status-pill').textContent = 'DEMO';
  $('greeting').textContent = 'Try the form. This preview does not send or save anything.';
  $('response-form').hidden = false;
  $('preview-actions').hidden = true;
  $('demo-receipt').hidden = true;
  $('submit-button').textContent = 'Preview my response ↗';
  notice('Demo mode · No email will be sent and no response will be stored.');
}

function calendar() {
  const stamp = value => new Date(value).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  const escape = value => value.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/[,;]/g, '\\$&');
  const content = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//D3//Seminar//EN','BEGIN:VEVENT',
    `UID:${config.campaign_id}-${slot.id}@d3seminar`, `DTSTAMP:${stamp(Date.now())}`,
    `DTSTART:${stamp(slot.start)}`, `DTEND:${stamp(slot.end)}`,
    `SUMMARY:${escape(config.title)} (proposed)`, `LOCATION:${escape(config.location)}`,
    'STATUS:TENTATIVE','END:VEVENT','END:VCALENDAR',''].join('\r\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([content], {type:'text/calendar;charset=utf-8'}));
  link.download = 'd3-seminar-proposed.ics';
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function start() {
  const response = await fetch('config.json', {cache:'no-store'});
  if (!response.ok) throw new Error('The seminar details could not be loaded. Please try again.');
  config = await response.json();
  document.title = config.title;
  $('subtitle').textContent = config.subtitle;
  $('pilot-banner').hidden = !config.test_mode;
  $('edition').textContent = config.test_mode ? 'MVP / PILOT' : 'SEMINAR';
  const params = new URLSearchParams(location.hash.slice(1));
  const token = params.get('token');
  slot = config.slots.find(item => item.id === params.get('slot')) || config.slots[0];
  const fmt = options => new Intl.DateTimeFormat('en-GB', {timeZone:config.timezone, ...options});
  $('month').textContent = fmt({month:'short'}).format(new Date(slot.start)).toUpperCase();
  $('day').textContent = fmt({day:'2-digit'}).format(new Date(slot.start));
  $('session-date').textContent = fmt({weekday:'long',day:'numeric',month:'long'}).format(new Date(slot.start));
  $('session-time').textContent = `${fmt({hour:'2-digit',minute:'2-digit'}).format(new Date(slot.start))}–${fmt({hour:'2-digit',minute:'2-digit',timeZoneName:'short'}).format(new Date(slot.end))} · ${config.timezone}`;
  $('session-location').textContent = config.location;
  $('calendar').hidden = false;
  $('calendar').addEventListener('click', calendar);
  $('demo-button').addEventListener('click', showDemo);
  $('demo-reset').addEventListener('click', showDemo);
  $('response-form').addEventListener('change', () => {
    const change = new FormData($('response-form')).get('response') === 'change_requested';
    $('comment').required = change;
    $('comment-hint').textContent = change ? '(please suggest an alternative)' : '(optional)';
  });
  $('response-form').addEventListener('submit', event => {
    const change = new FormData($('response-form')).get('response') === 'change_requested';
    if (change && !$('comment').value.trim()) {
      event.preventDefault();
      $('comment').setCustomValidity('Please suggest an alternative time.');
      $('comment').reportValidity();
      return;
    }
    if (demo) {
      event.preventDefault();
      $('response-form').hidden = true;
      $('demo-receipt').hidden = false;
      notice('Preview complete · Nothing was saved.');
    } else {
      $('submit-button').disabled = true;
      $('submit-button').textContent = 'Saving your response…';
    }
  });
  $('comment').addEventListener('input', () => $('comment').setCustomValidity(''));
  window.addEventListener('pageshow', () => { $('submit-button').disabled = false; });
  if (!token) {
    notice('Open the personal link in your invitation email to send a response.');
    $('preview-actions').hidden = false;
    if (params.get('demo') === '1') showDemo();
    return;
  }
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('This invitation link is incomplete. Please open the full link from your email.');
  const revision = await eventRevision(config, slot);
  if (params.get('revision') !== revision) throw new Error('The seminar details have changed. Please ask the organizer for an updated invitation.');
  const backend = config.backend_url ? new URL(config.backend_url) : null;
  if (!backend || backend.origin !== location.origin || backend.pathname !== '/api/respond') {
    throw new Error('Responses are not open yet. Please reply to your invitation email, or try again once the organizer has finished setup.');
  }
  const invitationResponse = await fetch(new URL('invitation', backend), {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token})
  });
  const invitation = await invitationResponse.json();
  if (!invitationResponse.ok || !invitation.ok) throw new Error(invitation.error || 'Your invitation could not be loaded.');
  if (invitation.revision !== revision) throw new Error('The seminar details have changed. Please ask the organizer for an updated invitation.');
  $('response-form').action = config.backend_url;
  $('token').value = token;
  $('revision').value = revision;
  $('greeting').textContent = `Hello ${invitation.name}. Please check your proposed time below.`;
  $('status-pill').textContent = 'INVITATION';
  $('response-form').hidden = false;
  const previous = $('response-form').querySelector(`input[value="${invitation.response}"]`);
  if (previous) previous.checked = true;
  $('comment').value = invitation.comment;
  $('comment').required = invitation.response === 'change_requested';
  $('comment-hint').textContent = invitation.response === 'change_requested' ? '(please suggest an alternative)' : '(optional)';
  notice(invitation.response === 'pending' ? 'After you submit, you’ll see a receipt once your response has been saved.' : 'Your previous response is saved. You can update it below.');
}

start().catch(error => { notice(error.message, true); $('response-form').hidden = true; });
window.addEventListener('hashchange', () => location.reload());
