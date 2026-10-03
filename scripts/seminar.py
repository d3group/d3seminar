#!/usr/bin/env python3
"""Manage a small seminar without publishing participant data or mail credentials."""

import argparse
import csv
import fcntl
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / '.private'
CONFIG = ROOT / 'site' / 'config.json'
EMAIL_PATTERN = r'[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+'


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
    return dict(campaign_id=config['campaign_id'], end=slot['end'], location=config['location'],
                slot_id=slot['id'], start=slot['start'], timezone=config['timezone'], title=config['title'])


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def event_revision(event):
    return digest(json.dumps(event, ensure_ascii=False, separators=(',', ':'), sort_keys=True))


def poll_data(config):
    return {key: config[key] for key in ('campaign_id', 'title', 'timezone', 'location', 'slots',
                                        'deadline', 'required_participants')}


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
    if len(durations) != 1:
        raise ValueError('Use the same duration for every candidate time.')


def when(slot, timezone_name):
    start = datetime.fromisoformat(slot['start']).astimezone(ZoneInfo(timezone_name))
    end = datetime.fromisoformat(slot['end']).astimezone(ZoneInfo(timezone_name))
    return f'{start:%A, %d %B %Y}, {start:%H:%M}–{end:%H:%M} ({timezone_name})'


def calendar_bytes(event):
    def stamp(value):
        return value.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    def escape(value):
        return value.replace('\\', '\\\\').replace('\r', '').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//D3//Seminar//EN', 'BEGIN:VEVENT',
             f'UID:{event["campaign_id"]}@d3seminar', f'DTSTAMP:{stamp(datetime.now(timezone.utc))}',
             f'DTSTART:{stamp(datetime.fromisoformat(event["start"]))}', f'DTEND:{stamp(datetime.fromisoformat(event["end"]))}',
             f'SUMMARY:{escape(event["title"])}', f'LOCATION:{escape(event["location"])}',
             'STATUS:CONFIRMED', 'END:VEVENT', 'END:VCALENDAR', '']
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


def load_config():
    config = read_json(CONFIG)
    local_file = PRIVATE / 'settings.json'
    if local_file.exists():
        base = read_json(local_file).get('local_base_url')
        if base:
            config['site_url'] = base
            config['backend_url'] = base + 'api/respond'
    if not isinstance(config.get('test_mode'), bool):
        raise ValueError('Set test_mode explicitly to true or false.')
    validate_poll(poll_data(config))
    url = urlparse(config['site_url'])
    local = url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost')
    if (url.scheme != 'https' and not local) or not url.netloc or url.query or url.fragment or not url.path.endswith('/'):
        raise ValueError('site_url must be HTTPS, or HTTP on localhost, and end with /.')
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
        writer = csv.DictWriter(stream, fieldnames=['name', 'email'])
        writer.writeheader()
        writer.writerow(dict(name=args.name, email=address))
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
    source = Path(args.csv) if args.csv else PRIVATE / 'participants.csv'
    with source.open(newline='') as stream:
        participants = list(csv.DictReader(stream))
    if not 1 <= len(participants) <= 100:
        raise ValueError('The participant list must contain 1–100 people.')
    state_file = PRIVATE / 'invitations.json'
    previous = read_json(state_file) if state_file.exists() else []
    by_email = {record['email']: record for record in previous}
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
        records.append(dict(id=old['id'] if old else secrets.token_hex(12), name=name, email=address,
                            token=old['token'] if old else secrets.token_urlsafe(32),
                            revision=revision, poll=poll,
                            deliveries=old.get('deliveries', {}) if old else {}))
    if previous and previous != records:
        save_private(PRIVATE / 'history' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-invitations.json'), previous)
    save_private(state_file, records)
    print(f'Prepared {len(records)} personal invitation(s). Nothing sent.')


def load_invitations(config):
    records = read_json(PRIVATE / 'invitations.json')
    for record in records:
        if record['revision'] != event_revision(poll_data(config)):
            raise ValueError('Seminar details changed. Run prepare, then sync before sending.')
    return records


def backend(config, action, **data):
    base = settings().get('local_base_url', '')
    url = urlparse(base)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost') or url.path != '/':
        raise ValueError('Configure the local backend with configure --local first.')
    payload = dict(action=action, secret=settings()['admin_secret'], **data)
    request = Request(base + 'api/admin', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=40) as response:
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
    invites = [dict(id=item['id'], name=item['name'], email=item['email'], poll=item['poll'],
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
    save_private(PRIVATE / 'responses.json', {'fetched_at': now(), 'records': responses})
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
    print('A private snapshot is saved in .private/responses.json.')


def open_page(args):
    config = load_config()
    if args.command == 'admin':
        url = config['site_url'] + 'admin#' + urlencode(dict(key=settings()['admin_secret']))
    else:
        invite = next(item for item in load_invitations(config) if item['email'] == settings()['test_recipient'])
        url = invitation_url(config, invite)
    subprocess.run(['open', url], check=True)


def invitation_url(config, invite):
    return config['site_url'] + '#' + urlencode(dict(token=invite['token']))


def message(config, local, invite, kind, state=None):
    poll = invite['poll']
    state = state or {}
    wording = {
        'invite': ('Find a time for our seminar', 'Please mark your availability for each option. We are looking for one time when everyone can attend.'),
        'remind': ('Reminder: your seminar availability', 'Please finish marking your availability so we can find a time for everyone.'),
        'check': ('Can you make this seminar time?', 'You marked this time as available if necessary. Please open your invitation and change it to Available if you can commit, or Unavailable if you cannot.'),
        'confirm': ('Our seminar time is confirmed', 'Everyone has confirmed their availability. Our seminar will take place at the time below.'),
    }
    subject, introduction = wording[kind]
    if kind == 'confirm':
        event = state.get('final_event')
        if not event:
            raise ValueError('Finalize a time for everyone before sending confirmations.')
        details = when(event, poll['timezone'])
    elif kind == 'check':
        slot = next((item for item in poll['slots'] if item['id'] == state.get('proposed_slot_id')), None)
        if not slot:
            raise ValueError('Choose a time to reconfirm in the organizer dashboard first.')
        details = when(slot, poll['timezone'])
    else:
        details = '\n'.join(when(slot, poll['timezone']) for slot in poll['slots'])
    deadline = datetime.fromisoformat(poll['deadline']).astimezone(ZoneInfo(poll['timezone']))
    due = '' if kind == 'confirm' else f'Please respond by {deadline:%A, %d %B, %H:%M} ({poll["timezone"]}).\n\n'
    test_note = 'This is an MVP test sent only to you. All dates below are test options.\n\n' if config['test_mode'] else ''
    body = (f'Hello {invite["name"]},\n\n{test_note}{introduction}\n\n{poll["title"]}\n{details}\n'
            f'{poll["location"]}\n\n{due}Open your personal invitation:\n'
            f'{invitation_url(config, invite)}\n\nPlease keep this link private; anyone with it can update your response.\n\n'
            f'Best,\n{local["sender_name"]}\n\nPlease send any comments or suggestions to {local["sender_email"]}.\n')
    mail = EmailMessage()
    mail['From'] = formataddr((local['sender_name'], local['sender_email']))
    mail['To'] = invite['email']
    mail['Subject'] = ('[TEST] ' if config['test_mode'] else '') + subject + ' | ' + poll['title']
    mail['Date'] = formatdate(localtime=True)
    mail['Message-ID'] = make_msgid(domain=local['sender_email'].split('@')[1])
    mail.set_content(body)
    safe_body = html.escape(body).replace(html.escape(invitation_url(config, invite)),
        '<a href="' + html.escape(invitation_url(config, invite), quote=True) + '">Open your personal invitation</a>')
    paragraphs = ''.join('<p style="margin:0 0 18px">' + paragraph.replace('\n', '<br>') + '</p>'
                         for paragraph in safe_body.strip().split('\n\n'))
    mail.add_alternative('<html><body><div style="font:15px/1.7 Arial,sans-serif;max-width:600px;color:#152d35">'
                         + paragraphs + '</div></body></html>', subtype='html')
    if kind == 'confirm':
        mail.add_attachment(calendar_bytes(state['final_event']), maintype='text', subtype='calendar', filename='d3-seminar.ics')
    return mail


def eligible(invite, response, kind, state=None):
    deliveries = invite.get('deliveries', {})
    state = state or {}
    answers = response.get('answers', {}) if response else {}
    revision = invite['revision']
    key = kind + ':' + revision
    if kind == 'confirm':
        if not state.get('final_event'):
            return False
        key = 'confirm:' + state['finalized_at']
    else:
        if state.get('final_event'):
            return False
        if kind == 'check':
            if not state.get('proposal_id') or answers.get(state.get('proposed_slot_id')) != 'if_needed':
                return False
            key = 'check:' + state['proposal_id']
        else:
            if len(answers) == len(invite['poll']['slots']):
                return False
            if kind == 'remind' and deliveries.get('invite:' + revision, {}).get('status') != 'sent':
                return False
    return key if key not in deliveries else False


def send(args):
    config, local = load_config(), settings()
    invitations = load_invitations(config)
    recipients = [invite for invite in invitations if args.all or invite['email'] == email_address(args.to)]
    if not recipients:
        raise ValueError('No matching participant. Run prepare after editing the participant list.')
    if config['test_mode'] and any(invite['email'] != local['test_recipient'] for invite in recipients):
        raise ValueError('Test mode blocks delivery to anyone except the test recipient.')
    responses = get_responses(config, invitations) if args.send or args.kind != 'invite' else {}
    state = backend(config, 'dashboard')['state'] if responses else {}
    queue = [(invite, eligible(invite, responses.get(invite['id']), args.kind, state)) for invite in recipients]
    queue = [(invite, key) for invite, key in queue if key]
    if not queue:
        print('No eligible messages. Already sent or attempted messages are not sent again.')
        return
    preview_dir = PRIVATE / 'outbox'
    preview_dir.mkdir(exist_ok=True, mode=0o700)
    messages = []
    for invite, key in queue:
        mail = message(config, local, invite, args.kind, state)
        filename = preview_dir / f'{invite["id"]}-{args.kind}.eml'
        filename.write_bytes(bytes(mail))
        filename.chmod(0o600)
        messages.append((invite, key, mail))
        print(f'{"Send" if args.send else "Preview"}: {args.kind} → {invite["email"]}')
    if not args.send:
        print('Email previews saved in .private/outbox/. Nothing sent. Add --send to deliver.')
        return
    if not local['smtp_username']:
        raise ValueError('Set your JMU account username with configure --smtp-user first.')
    password = os.environ.get('D3_SMTP_PASSWORD') or getpass.getpass('JMU password (not stored): ')
    if not password:
        raise ValueError('A password is required to send.')
    with smtplib.SMTP_SSL(local['smtp_host'], local['smtp_port'], context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(local['smtp_username'], password)
        for invite, key, mail in messages:
            # Recheck after the password prompt in case someone responded meanwhile.
            current = get_responses(config, invitations)
            latest = backend(config, 'dashboard')['state']
            if eligible(invite, current.get(invite['id']), args.kind, latest) != key:
                print(f'Skipped changed response: {invite["email"]}')
                continue
            # Persist before SMTP to prevent automatic duplicates after a crash or ambiguous timeout.
            invite['deliveries'][key] = dict(status='attempted', at=now(), message_id=mail['Message-ID'])
            save_private(PRIVATE / 'invitations.json', invitations)
            smtp.send_message(mail, from_addr=local['sender_email'], to_addrs=[invite['email']])
            invite['deliveries'][key]['status'] = 'sent'
            save_private(PRIVATE / 'invitations.json', invitations)
            print(f'Accepted by the university mail server: {invite["email"]}')


def smtp_check(args):
    local = settings()
    with smtplib.SMTP_SSL(local['smtp_host'], local['smtp_port'], context=ssl.create_default_context(), timeout=20) as smtp:
        code, _ = smtp.ehlo()
        if code != 250:
            raise ValueError('The SMTP server did not accept EHLO.')
        print('JMU SMTP connection and TLS certificate verified. No login attempted and no email sent.')
        print('Authentication methods:', smtp.esmtp_features.get('auth', 'not advertised'))


def test_mail(args):
    address = settings()['test_recipient']
    send(argparse.Namespace(kind=args.kind, to=address, all=False, send=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Create private settings and one test participant')
    init.add_argument('--email', required=True)
    init.add_argument('--name', required=True)
    init.add_argument('--smtp-user')
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
    commands.add_parser('admin', help='Open the private organizer dashboard').set_defaults(run=open_page)
    commands.add_parser('open-test', help='Open your personal test invitation').set_defaults(run=open_page)
    commands.add_parser('smtp-check', help='Test SMTP connectivity without sending').set_defaults(run=smtp_check)
    tester = commands.add_parser('test-mail', help='Send one test message to your configured test address')
    tester.add_argument('kind', choices=['invite', 'remind', 'check', 'confirm'], nargs='?', default='invite')
    tester.set_defaults(run=test_mail)
    sender = commands.add_parser('send', help='Preview or send one invitation, reminder, or confirmation per person')
    sender.add_argument('kind', choices=['invite', 'remind', 'check', 'confirm'])
    target = sender.add_mutually_exclusive_group(required=True)
    target.add_argument('--to')
    target.add_argument('--all', action='store_true')
    sender.add_argument('--send', action='store_true', help='Actually deliver; default is preview only')
    sender.set_defaults(run=send)
    args = parser.parse_args()
    PRIVATE.mkdir(exist_ok=True, mode=0o700)
    with (PRIVATE / 'command.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, 'Another seminar command is running. Please wait.\n')
        try:
            args.run(args)
        except (ValueError, OSError, smtplib.SMTPException, KeyError) as error:
            parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
