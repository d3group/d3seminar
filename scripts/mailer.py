#!/usr/bin/env python3
"""Preview and send seminar emails locally; the university password stays on this machine."""

import argparse, fcntl, getpass, os, smtplib, ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

import seminar
from api_client import post_json

PRIVATE = Path(os.environ.get('D3_MAIL_DIR', seminar.ROOT / '.mailer'))


def api(settings, action, **data):
    base = seminar.validate_api_url(os.environ.get('D3_MAIL_API_URL') or settings['api_url'])
    return post_json(base + 'api/admin', dict(action=action, secret=settings['admin_secret'], **data))


def message(plan, row):
    mail = EmailMessage()
    mail['From'] = formataddr((plan['sender_name'], plan['sender']))
    mail['To'] = row['to']
    mail['Subject'] = row['subject']
    mail['Date'] = formatdate(localtime=True)
    mail['Message-ID'] = make_msgid(domain=plan['sender'].split('@')[1])
    mail.set_content(row['body'])
    mail.add_alternative(row['html'], subtype='html')
    for part in row['attachments']:
        mail.add_attachment(part['content'].encode(), maintype='text', subtype='calendar', filename=part['name'])
    return mail


def run(settings, directory, kind, send=False, event='next'):
    plans = api(settings, 'preview_queued')['plans'] if kind == 'queued' else [api(settings, 'preview_mail', mail_event=event, kind=kind)]
    if any(plan['sender'] != settings['sender_email'] for plan in plans):
        raise ValueError('The local sender address and organiser address differ. Update the local email settings first.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    outbox = directory / 'outbox'
    outbox.mkdir(exist_ok=True, mode=0o700)
    rows = [(plan, row, message(plan, row)) for plan in plans for row in plan['messages']]
    print(f'{len(rows)} {kind} email(s) from {settings["sender_email"]}')
    for plan, row, mail in rows:
        path = outbox / f'{plan["event_id"]}-{row["id"]}-{plan["kind"]}.eml'
        path.write_bytes(bytes(mail)); path.chmod(0o600)
        print(f'  {row["name"]} <{row["to"]}> — {row["subject"]}')
    print(f'Private message previews: {outbox}')
    if not rows or not send:
        print('Nothing sent.' + (' Add --send to send these emails.' if rows else ''))
        return
    if not settings.get('smtp_username'):
        raise ValueError('Configure the local university SMTP username first.')
    password = getpass.getpass('University password (used locally, not stored): ')
    if not password:
        raise ValueError('A password is required to send.')
    try:
        with smtplib.SMTP_SSL(settings['smtp_host'], settings['smtp_port'], context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(settings['smtp_username'], password)
            sent, skipped = 0, 0
            for plan, row, mail in rows:
                claim = api(settings, 'claim_mail', event_id=plan['event_id'], kind=plan['kind'], participant_id=row['id'],
                            preview=row['preview'], message_id=mail['Message-ID'], **({'batch_id':plan['batch_id']} if 'batch_id' in plan else {}))
                if not claim['claimed']:
                    skipped += 1
                    continue
                smtp.send_message(mail, from_addr=plan['sender'], to_addrs=[row['to']])
                api(settings, 'finish_mail', event_id=plan['event_id'], participant_id=row['id'], key=row['key'], message_id=mail['Message-ID'])
                sent += 1
            print(f'{sent} accepted by university SMTP; {skipped} skipped because eligibility changed.')
    finally:
        password = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['setup', 'invite', 'remind', 'present', 'confirm', 'queued'])
    parser.add_argument('--event', default='next', help='Defaults to the next upcoming seminar; optionally choose an event id')
    parser.add_argument('--send', action='store_true', help='Send after the local password prompt; default is preview only')
    parser.add_argument('--from-settings', type=Path, default=seminar.ROOT/'data/settings.json', help='Existing private settings to import once')
    parser.add_argument('--api-url', default='http://localhost:8080/', help='Backend root URL for setup; use HTTPS for a hosted backend')
    args = parser.parse_args()
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if args.kind == 'setup':
            path = PRIVATE/'settings.json'
            if path.exists():
                raise ValueError('Local email settings already exist. Edit .mailer/settings.json to change them.')
            old = seminar.read_json(args.from_settings)
            settings = {key:old[key] for key in ('sender_name','sender_email','smtp_username','smtp_host','smtp_port','admin_secret')}
            seminar.save_private(path, dict(settings, api_url=seminar.validate_api_url(args.api_url)))
            print('Created private local email settings. No password copied or stored.')
            return
        with (PRIVATE/'command.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another local email command is running.') from None
            run(seminar.read_json(PRIVATE/'settings.json'), PRIVATE, args.kind, args.send, args.event)
    except (ValueError, OSError, KeyError, smtplib.SMTPException) as error:
        parser.exit(1, f'Email service stopped: {error}\nAny attempted messages are excluded from automatic retries. Check delivery records before retrying.\n')


if __name__ == '__main__':
    main()
