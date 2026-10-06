#!/usr/bin/env python3
"""Manage a small seminar without publishing participant data or mail credentials."""

import argparse
import csv
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path(os.environ.get('D3_DATA_DIR', ROOT / '.private'))
CONFIG = Path(os.environ.get('D3_CONFIG', ROOT / 'site' / 'config.json'))
EVENT_ID = None
EMAIL_PATTERN = r'[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+'
GROUP_SIZES = {'professor':5, 'student':15}


def validate_roles(records):
    if any(record.get('role', '') not in {'', *GROUP_SIZES} for record in records):
        raise ValueError('Set each participant role to professor or student, or leave it blank until assigned.')
    for role, count in GROUP_SIZES.items():
        if sum(record.get('role') == role for record in records) > count:
            raise ValueError(f'The roster has more than {count} participants with the role {role}.')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_json(path):
    with path.open() as stream:
        return json.load(stream)


def save_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    temporary.replace(path)
    path.chmod(0o600)


def email_address(value):
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(EMAIL_PATTERN, value):
        raise ValueError('Enter a valid email address.')
    return value


def event_data(config, slot):
    event = dict(campaign_id=config['campaign_id'], end=slot['end'], location=config['location'],
                slot_id=slot['id'], start=slot['start'], timezone=config['timezone'], title=config['title'])
    if config.get('daily_hours'):
        event.update(daily_hours=config['daily_hours'], duration_days=config['duration_days'])
    return event


def programme_state(state):
    if not state.get('final_event') or not state.get('programme'):
        return state
    programme = state['programme']
    event = dict(state['final_event'], programme=programme)
    if programme.get('room'):
        event['location'] = programme['room']
    return dict(state, final_event=event)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def event_revision(event):
    return digest(json.dumps(event, ensure_ascii=False, separators=(',', ':'), sort_keys=True))


def poll_data(config):
    poll = {key: config[key] for key in ('campaign_id', 'title', 'timezone', 'location', 'slots',
                                        'deadline', 'required_participants')}
    if config.get('duration_days'):
        poll['duration_days'] = config['duration_days']
    if config.get('daily_hours'):
        poll['daily_hours'] = config['daily_hours']
    return poll


def event_config(config, event_id=None):
    events = config.get('events', [{'id':'seminar-1', 'name':'Seminar 1'}])
    ids = [event['id'] for event in events]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'[a-z0-9-]{1,40}', key) for key in ids):
        raise ValueError('Give each event a unique id using lowercase letters, numbers, and hyphens.')
    event = next((event for event in events if event['id'] == (event_id or ids[0])), None)
    if not event:
        raise ValueError('Unknown event.')
    return dict(config, **{key:value for key,value in event.items() if key != 'id'}, event_id=event['id'])


def event_private(config):
    key = config.get('event_id', 'seminar-1')
    return PRIVATE if key == 'seminar-1' else PRIVATE / 'events' / key


def validate_poll(poll):
    if not isinstance(poll, dict):
        raise ValueError('Invalid poll details.')
    for key in ('campaign_id', 'title', 'timezone', 'location', 'deadline'):
        if not isinstance(poll.get(key), str) or not 1 <= len(poll[key]) <= 300 or '\r' in poll[key] or '\n' in poll[key]:
            raise ValueError('Invalid poll details.')
    ZoneInfo(poll['timezone'])
    deadline = datetime.fromisoformat(poll['deadline'])
    if deadline.tzinfo is None:
        raise ValueError('The response deadline needs a UTC offset.')
    count = poll.get('required_participants')
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError('Set required_participants to the full group size (1–100).')
    slots = poll.get('slots')
    if not isinstance(slots, list) or not 2 <= len(slots) <= 12:
        raise ValueError('Provide 2–12 candidate times.')
    seen, times, durations = set(), set(), set()
    for slot in slots:
        if not isinstance(slot, dict) or not isinstance(slot.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', slot['id']) or slot['id'] in seen:
            raise ValueError('Give each candidate time a unique id.')
        seen.add(slot['id'])
        start, end = (datetime.fromisoformat(slot[key]) for key in ('start', 'end'))
        if start.tzinfo is None or end.tzinfo is None or end <= start or start <= deadline or start in times:
            raise ValueError('Candidate times must be distinct, after the deadline, and include UTC offsets.')
        times.add(start)
        durations.add(end - start)
    if len(durations) != 1 and not poll.get('duration_days'):
        raise ValueError('Use the same duration for every candidate time.')
    if poll.get('duration_days'):
        if type(poll['duration_days']) is not int or not 1 <= poll['duration_days'] <= 14:
            raise ValueError('Use a whole number of days for the onsite meeting.')
        zone = ZoneInfo(poll['timezone'])
        for slot in slots:
            start, end = (datetime.fromisoformat(slot[key]).astimezone(zone) for key in ('start', 'end'))
            if (end.date() - start.date()).days + 1 != poll['duration_days']:
                raise ValueError('Each onsite option must cover the configured number of calendar days.')
    if 'daily_hours' in poll:
        hours = poll['daily_hours']
        if not poll.get('duration_days') or not isinstance(hours, dict) or set(hours) not in ({'start', 'end'}, {'start', 'end', 'last_day_end'}):
            raise ValueError('Daily hours require a multi-day event and a start and end time.')
        if any(not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value) for value in hours.values()):
            raise ValueError('Write daily hours as HH:MM.')
        day_start, day_end = (time.fromisoformat(hours[key]) for key in ('start', 'end'))
        if day_start >= day_end:
            raise ValueError('Daily sessions must end after they start.')
        last_end = time.fromisoformat(hours.get('last_day_end', hours['end']))
        if 'last_day_end' in hours and (poll['duration_days'] < 2 or not day_start < last_end < day_end):
            raise ValueError('The final day must be shorter than a full day and end after it starts.')
        for slot in slots:
            start, end = (datetime.fromisoformat(slot[key]).astimezone(ZoneInfo(poll['timezone'])) for key in ('start', 'end'))
            if start.time() != day_start or end.time() != last_end:
                raise ValueError('Each onsite option must match the configured daily hours.')


def daily_hours_label(hours):
    full = f'{hours["start"]}–{hours["end"]}'
    if hours.get('last_day_end'):
        return f'{full}; final day {hours["start"]}–{hours["last_day_end"]}'
    return full + ' each day'


def when(slot, timezone_name, daily_hours=None):
    start = datetime.fromisoformat(slot['start']).astimezone(ZoneInfo(timezone_name))
    end = datetime.fromisoformat(slot['end']).astimezone(ZoneInfo(timezone_name))
    daily_hours = daily_hours or slot.get('daily_hours')
    if daily_hours:
        return f'{start:%A, %d %B %Y} to {end:%A, %d %B %Y}, {daily_hours_label(daily_hours)} ({timezone_name})'
    if start.date() != end.date():
        return f'{start:%A, %d %B %Y, %H:%M} to {end:%A, %d %B %Y, %H:%M} ({timezone_name})'
    return f'{start:%A, %d %B %Y}, {start:%H:%M}–{end:%H:%M} ({timezone_name})'


def calendar_bytes(event, created_at=None):
    def stamp(value):
        return value.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    def escape(value):
        return value.replace('\\', '\\\\').replace('\r', '').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')
    sessions = [(event['campaign_id'], datetime.fromisoformat(event['start']), datetime.fromisoformat(event['end']), event['title'])]
    if event.get('daily_hours'):
        zone = ZoneInfo(event['timezone'])
        first_day = datetime.fromisoformat(event['start']).astimezone(zone).date()
        sessions = []
        for index in range(event['duration_days']):
            day = first_day + timedelta(days=index)
            hours = event['daily_hours']
            finish = hours.get('last_day_end', hours['end']) if index == event['duration_days'] - 1 else hours['end']
            start, end = (datetime.combine(day, time.fromisoformat(value), tzinfo=zone) for value in (hours['start'], finish))
            sessions.append((f'{event["campaign_id"]}-day-{index + 1}', start, end, f'{event["title"]} (day {index + 1})'))
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//D3//Seminar//EN']
    for uid, start, end, title in sessions:
        created = datetime.fromisoformat(created_at) if created_at else datetime.now(timezone.utc)
        lines.extend(['BEGIN:VEVENT', f'UID:{uid}@d3seminar', f'DTSTAMP:{stamp(created)}',
                      f'DTSTART:{stamp(start)}', f'DTEND:{stamp(end)}', f'SUMMARY:{escape(title)}',
                      f'LOCATION:{escape(event["location"])}'])
        programme = event.get('programme', {})
        description = '\n'.join(programme[key] for key in ('topic', 'presenters', 'agenda') if programme.get(key))
        if description:
            lines.append('DESCRIPTION:' + escape(description))
        lines.extend(['STATUS:CONFIRMED', 'END:VEVENT'])
    lines.extend(['END:VCALENDAR', ''])
    # Fold by bytes so calendar applications also accept long Unicode titles.
    folded = []
    for line in lines:
        part = ''
        for char in line:
            if len((part + char).encode()) > 75:
                folded.append(part)
                part = ' '
            part += char
        folded.append(part)
    return '\r\n'.join(folded).encode()


def validate_site_url(base):
    if not isinstance(base, str) or any(c.isspace() for c in base) or '\\' in base:
        raise ValueError('Use a valid website URL.')
    url = urlparse(base)
    local = url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost')
    if (url.scheme != 'https' and not local) or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment or not url.path.endswith('/'):
        raise ValueError('site_url must be HTTPS, or HTTP on localhost, and end with /.')
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError('Invalid URL port.')
    return base


def validate_api_url(base):
    validate_site_url(base)
    if urlparse(base).path != '/':
        raise ValueError('api_url must use the backend root and end with /.')
    return base


def load_config():
    programme = read_json(CONFIG)
    for event in programme.get('events', []):
        validate_poll(poll_data(event_config(programme, event['id'])))
    config = event_config(programme, EVENT_ID)
    local_file = PRIVATE / 'settings.json'
    site = os.environ.get('D3_SITE_URL')
    if not site and local_file.exists():
        site = read_json(local_file).get('local_base_url')
    config['site_url'] = validate_site_url(site or config['site_url'])
    api = os.environ.get('D3_API_URL') or (site if site else config.get('api_url'))
    if not api and config.get('backend_url'):
        api = config['backend_url'].removesuffix('api/respond')
    if api:
        config['api_url'] = validate_api_url(api)
        config['backend_url'] = api + 'api/respond'
    if not isinstance(config.get('test_mode'), bool):
        raise ValueError('Set test_mode explicitly to true or false.')
    validate_poll(poll_data(config))
    return config


def settings():
    filename = PRIVATE / 'settings.json'
    if not filename.exists():
        raise ValueError('Run init first.')
    return read_json(filename)


def initialize(args):
    if (PRIVATE / 'settings.json').exists():
        raise ValueError('Already initialized; use configure to change settings.')
    address = email_address(args.email)
    config = load_config()
    save_private(PRIVATE / 'settings.json', {
        'sender_email': address, 'sender_name': args.name, 'smtp_username': args.smtp_user or '',
        'smtp_host': 'mailmaster.uni-wuerzburg.de', 'smtp_port': 465,
        'test_recipient': address, 'admin_secret': secrets.token_urlsafe(32),
        'local_base_url': 'http://127.0.0.1:8765/',
    })
    with (PRIVATE / 'participants.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['name', 'email', 'role'])
        writer.writeheader()
        writer.writerow(dict(name=args.name, email=address, role=getattr(args, 'role', '')))
    (PRIVATE / 'participants.csv').chmod(0o600)
    prepare(argparse.Namespace(csv=None))
    print('Created private settings and a participant list containing only your test address.')


def configure(args):
    local = settings()
    if args.smtp_user:
        local['smtp_username'] = args.smtp_user
    if args.local:
        local['local_base_url'] = 'http://127.0.0.1:8765/'
    save_private(PRIVATE / 'settings.json', local)
    print('Settings updated. No password is stored.')


def prepare(args):
    config, local = load_config(), settings()
    directory = event_private(config)
    source = Path(args.csv) if args.csv else PRIVATE / 'participants.csv'
    with source.open(newline='') as stream:
        participants = list(csv.DictReader(stream))
    if not 1 <= len(participants) <= 100:
        raise ValueError('The participant list must contain 1–100 people.')
    state_file = directory / 'invitations.json'
    previous = read_json(state_file) if state_file.exists() else []
    by_email = {record['email']: record for record in previous}
    shared_file = PRIVATE / 'invitations.json'
    shared = {r['email']:r for r in read_json(shared_file)} if shared_file.exists() else {}
    records, seen = [], set()
    for participant in participants:
        address = email_address(participant['email'])
        if address in seen:
            raise ValueError('Duplicate participant email.')
        seen.add(address)
        name = participant['name'].strip()
        if not name or len(name) > 120 or any(char in name for char in '\r\n'):
            raise ValueError('Each participant needs a name of 1–120 characters.')
        if config['test_mode'] and address != local['test_recipient']:
            raise ValueError('Test mode allows only the test recipient. No invitations were changed.')
        poll = poll_data(config)
        revision = event_revision(poll)
        old = by_email.get(address)
        identity = old or shared.get(address)
        role = participant.get('role', (identity or {}).get('role', '')).strip().lower()
        records.append(dict(id=identity['id'] if identity else secrets.token_hex(12), name=name, email=address, role=role,
                            token=identity['token'] if identity else secrets.token_urlsafe(32),
                            revision=revision, poll=poll,
                            deliveries=old.get('deliveries', {}) if old else {}))
    validate_roles(records)
    if previous and previous != records:
        save_private(directory / 'history' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-invitations.json'), previous)
    save_private(state_file, records)
    print(f'{config.get("name", config["title"])}: prepared {len(records)} personal invitation(s). Nothing sent.')


def load_invitations(config):
    records = read_json(event_private(config) / 'invitations.json')
    for record in records:
        if record['revision'] != event_revision(poll_data(config)):
            raise ValueError('Seminar details changed. Run prepare, then sync before sending.')
    return records


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('The API redirected. Set D3_API_URL to its final HTTPS address.')


def backend(config, action, **data):
    base = os.environ.get('D3_API_URL')
    if not base:
        base = settings().get('local_base_url', '')
        url = urlparse(base)
        if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost'):
            raise ValueError('Set D3_API_URL explicitly for a hosted backend.')
    validate_api_url(base)
    payload = dict(action=action, event_id=config.get('event_id', 'seminar-1'), secret=settings()['admin_secret'], **data)
    request = Request(base + 'api/admin', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with build_opener(NoRedirect).open(request, timeout=40) as response:
        try:
            result = json.load(response)
        except ValueError as error:
            raise ValueError('The backend did not return JSON. Check the local server is running.') from error
    if not result.get('ok'):
        raise ValueError(result.get('error', 'Backend request failed.'))
    return result


def synchronize(args):
    config = load_config()
    records = load_invitations(config)
    invites = [dict(id=item['id'], name=item['name'], email=item['email'], role=item.get('role', ''), poll=item['poll'], event_id=config.get('event_id', 'seminar-1'),
                    revision=item['revision'], token_hash=digest(item['token']), site_url=config['site_url']) for item in records]
    result = backend(config, 'sync', invites=invites)
    print(f'Synchronized {result["count"]} invitation(s) to local SQLite. Existing responses to unchanged invitations are preserved.')


def get_responses(config, invitations):
    responses = backend(config, 'responses')['records']
    by_id = {record['id']: record for record in responses}
    if set(by_id) != {invite['id'] for invite in invitations}:
        raise ValueError('Local invitations and response storage differ. Run sync first.')
    for invite in invitations:
        response = by_id.get(invite['id'])
        if not response or response['revision'] != invite['revision'] or response['token_hash'] != digest(invite['token']):
            raise ValueError('Local invitations and response storage differ. Run sync first.')
    save_private(event_private(config) / 'responses.json', {'fetched_at': now(), 'records': responses})
    return by_id


def status(args):
    config = load_config()
    invitations = load_invitations(config)
    responses = get_responses(config, invitations)
    for invite in invitations:
        response = responses[invite['id']]
        count = len(response['answers'])
        print(f'{invite["name"]}: {count}/{len(config["slots"])} options answered')
        if response['comment']:
            print('  ' + response['comment'].replace('\n', '\n  '))
    print('A private snapshot is saved in ' + str(event_private(config) / 'responses.json') + '.')


def open_page(args):
    config = load_config()
    if args.command == 'admin':
        url = config['site_url'] + 'admin.html?' + urlencode(dict(event=config.get('event_id', 'seminar-1'))) + '#' + urlencode(dict(key=settings()['admin_secret']))
    else:
        invite = next(item for item in load_invitations(config) if item['email'] == settings()['test_recipient'])
        url = config['site_url'] + '#' + urlencode(dict(token=invite['token'])) if args.command == 'home' else invitation_url(config, invite)
    if getattr(args, 'print_url', False):
        print(url)
    else:
        subprocess.run(['open', url], check=True)


def invitation_url(config, invite):
    return config['site_url'] + 'event.html?' + urlencode(dict(event=config.get('event_id', 'seminar-1'))) + '#' + urlencode(dict(token=invite['token']))


def message(config, local, invite, kind, state=None, later=None):
    poll = invite['poll']
    state = state or {}
    wording = {
        'invite': ('Find a time for our seminar', 'Please mark your availability for each option. We are looking for one time when everyone can attend.'),
        'remind': ('Reminder: your seminar availability', 'Please finish marking your availability so we can find a time for everyone.'),
        'confirm': ('Our seminar time is confirmed', 'Everyone has confirmed their availability. Our seminar will take place at the time below.'),
        'present': ('Bring your research to our next seminar', 'Please add a talk for our next seminar. Work in progress, an early result, or a question you would like feedback on is welcome.'),
    }
    if kind not in wording:
        raise ValueError('Choose invitations, reminders, presentation nudges or final confirmations.')
    subject, introduction = wording[kind]
    series = config.get('name', poll['title']) + (' · ' + config['semester'] if config.get('semester') else '')
    if config.get('kind') == 'onsite':
        subject = subject.replace('seminar', 'onsite meeting')
        introduction = introduction.replace('seminar', 'onsite meeting')
        if kind != 'confirm':
            hours = poll.get('daily_hours')
            timing = f', {daily_hours_label(hours)}' if hours else ''
            introduction += f' Each option covers {poll["duration_days"]} consecutive dates{timing}. Please respond for the whole meeting.'
    if kind == 'confirm' or kind == 'present' and state.get('final_event'):
        event = state.get('final_event')
        if not event:
            raise ValueError('Finalize a time for everyone before sending confirmations.')
        details = when(event, poll['timezone'])
        programme = event.get('programme', {})
        details += ''.join('\n' + programme[key] for key in ('topic', 'presenters', 'agenda') if programme.get(key))
    else:
        details = '\n'.join(when(slot, poll['timezone'], poll.get('daily_hours')) for slot in poll['slots'])
    deadline = datetime.fromisoformat(poll['deadline']).astimezone(ZoneInfo(poll['timezone']))
    due = '' if kind in {'confirm', 'present'} else f'Please respond by {deadline:%A, %d %B, %H:%M} ({poll["timezone"]}).\n\n'
    test_note = 'This is an MVP test sent only to you. All dates below are test options.\n\n' if config['test_mode'] else ''
    presentation_url = config['site_url'] + 'agenda.html?' + urlencode(dict(present=config.get('event_id', 'seminar-1'))) + '#' + urlencode(dict(token=invite['token']))
    presentation_note = ''
    if kind == 'present':
        if invite.get('role') != 'student':
            raise ValueError('Presentation nudges are for students only.')
        presentation_note = ('Start with a title; your abstract, explainer and slides can follow later.\nAdd your presentation:\n' + presentation_url + '\n\n')
        if later:
            presentation_note += 'Not ready for the next seminar? Reserve a later seminar with a provisional title. You can develop it into a full talk later.\n'
            for event in later:
                link = config['site_url'] + 'agenda.html?' + urlencode(dict(present=event['id'], placeholder='1')) + '#' + urlencode(dict(token=invite['token']))
                presentation_note += event['name'] + ': ' + link + '\n'
            presentation_note += '\n'
        else:
            presentation_note += 'If you cannot present at this seminar, please contact me to plan a contribution.\n\n'
        presentation_note += 'Plan to contribute at least once across the three seminars and at the onsite meeting.\n\n'
    body = (f'Hello {invite["name"]},\n\n{test_note}{introduction}\n\n{series}\n{details}\n'
            f'{state["final_event"]["location"] if kind == "confirm" else poll["location"]}\n\n{due}'
            + (presentation_note if kind == 'present' else f'Open your personal invitation:\n{invitation_url(config, invite)}\n\n')
            + 'Please keep your personal links private; anyone with them can update your responses and presentations.\n\n'
            f'Best,\n{local["sender_name"]}\n\nPlease send any comments or suggestions to {local["sender_email"]}.\n')
    mail = EmailMessage()
    mail['From'] = formataddr((local['sender_name'], local['sender_email']))
    mail['To'] = invite['email']
    mail['Subject'] = ('[TEST] ' if config['test_mode'] else '') + subject + ' | ' + series
    mail['Date'] = formatdate(localtime=True)
    mail['Message-ID'] = make_msgid(domain=local['sender_email'].split('@')[1])
    mail.set_content(body)
    safe_body = html.escape(body).replace(html.escape(invitation_url(config, invite)),
        '<a href="' + html.escape(invitation_url(config, invite), quote=True) + '">Open your personal invitation</a>')
    if presentation_note:
        safe_body = safe_body.replace(html.escape(presentation_url), '<a href="' + html.escape(presentation_url, quote=True) + '">Add your presentation</a>')
        for event in later or []:
            link = config['site_url'] + 'agenda.html?' + urlencode(dict(present=event['id'], placeholder='1')) + '#' + urlencode(dict(token=invite['token']))
            safe_body = safe_body.replace(html.escape(link), '<a href="' + html.escape(link, quote=True) + '">Reserve ' + html.escape(event['name']) + '</a>')
    paragraphs = ''.join('<p style="margin:0 0 18px">' + paragraph.replace('\n', '<br>') + '</p>'
                         for paragraph in safe_body.strip().split('\n\n'))
    mail.add_alternative('<html><body><div style="font:15px/1.7 Arial,sans-serif;max-width:600px;color:#152d35">'
                         + paragraphs + '</div></body></html>', subtype='html')
    if kind == 'confirm':
        mail.add_attachment(calendar_bytes(state['final_event'], state.get('finalized_at')), maintype='text', subtype='calendar', filename='d3-seminar.ics')
    return mail


def eligible(invite, response, kind, state=None):
    if kind not in {'invite', 'remind', 'confirm', 'present'}:
        return False
    deliveries = invite.get('deliveries', {})
    state = state or {}
    answers = {key:value for key,value in (response or {}).get('answers', {}).items() if value in {'available', 'unavailable'}}
    revision = invite['revision']
    key = kind + ':' + revision
    if kind == 'confirm':
        if not state.get('final_event'):
            return False
        key = 'confirm:' + state['finalized_at']
    elif kind == 'present':
        if invite.get('role') != 'student':
            return False
    else:
        if state.get('final_event'):
            return False
        if kind == 'remind' and all(answers.get(slot['id']) for slot in invite['poll']['slots']):
            return False
        if kind == 'remind' and deliveries.get('invite:' + revision, {}).get('status') != 'sent':
            return False
    return key if key not in deliveries else False


def main():
    global EVENT_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', default='seminar-1', help='Event id, or all for prepare, sync, and status')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Create private settings and one test participant')
    init.add_argument('--email', required=True)
    init.add_argument('--name', required=True)
    init.add_argument('--smtp-user')
    init.add_argument('--role', choices=['professor', 'student'], default='')
    init.set_defaults(run=initialize)
    config = commands.add_parser('configure')
    config.add_argument('--local', action='store_true', help='Use the backend at http://127.0.0.1:8765')
    config.add_argument('--smtp-user')
    config.set_defaults(run=configure)
    prepare_cmd = commands.add_parser('prepare', help='Create or refresh invitation links from a CSV')
    prepare_cmd.add_argument('--csv')
    prepare_cmd.set_defaults(run=prepare)
    commands.add_parser('sync', help='Register invitation hashes with the local SQLite backend').set_defaults(run=synchronize)
    commands.add_parser('status', help='Fetch current participant responses').set_defaults(run=status)
    for command, help_text in [('admin', 'Open the private organizer dashboard'), ('open-test', 'Open your personal test invitation'), ('home', 'Open your personal programme homepage')]:
        page = commands.add_parser(command, help=help_text)
        page.add_argument('--print-url', action='store_true', help='Print the private link instead of opening a browser')
        page.set_defaults(run=open_page)
    args = parser.parse_args()
    PRIVATE.mkdir(exist_ok=True, mode=0o700)
    with (PRIVATE / 'command.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, 'Another seminar command is running. Please wait.\n')
        try:
            if args.event == 'all':
                if args.command not in {'prepare', 'sync', 'status'}:
                    raise ValueError('Use --event all only with prepare, sync, or status.')
                for event in read_json(CONFIG)['events']:
                    EVENT_ID = event['id']
                    args.run(args)
            else:
                EVENT_ID = args.event
                args.run(args)
        except (ValueError, OSError, KeyError) as error:
            parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
