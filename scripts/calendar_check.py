#!/usr/bin/env python3
"""Find shared free slots in university Exchange calendars, locally and read-only."""

import argparse
import csv
import getpass
import json
import logging
import os
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / '.private/calendar'
ENDPOINT = 'https://mail.uni-wuerzburg.de/EWS/Exchange.asmx'
INTERVAL = 15
UTC = timezone.utc


def save_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)


def validate_people(people):
    if not isinstance(people, list) or not 1 <= len(people) <= 30:
        raise ValueError('Choose between 1 and 30 calendars.')
    result, seen = [], set()
    for person in people:
        name, email = person.get('name', '').strip(), person.get('email', '').strip().lower()
        if not name or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise ValueError('Each calendar needs a name and valid email address.')
        if email in seen:
            raise ValueError('The calendar list contains duplicate email addresses.')
        seen.add(email)
        result.append(dict(name=name, email=email))
    return result


def setup(roster, username):
    path = PRIVATE / 'settings.json'
    if path.exists():
        raise ValueError('Calendar settings already exist. Edit .private/calendar/settings.json to change them.')
    with roster.open(encoding='utf-8-sig', newline='') as stream:
        people = validate_people([row for row in csv.DictReader(stream) if row.get('role', '').lower() == 'professor'])
    if not username:
        mail_settings = ROOT / '.mailer/settings.json'
        if mail_settings.exists():
            username = json.loads(mail_settings.read_text()).get('smtp_username')
    if not username:
        raise ValueError('Supply --username with your university login name (not your password).')
    settings = dict(username=username, timezone='Europe/Berlin', people=people)
    save_private(path, json.dumps(settings, ensure_ascii=False, indent=2) + '\n')
    print(f'Prepared {len(people)} professor calendars in {path}. No passwords or invitation tokens copied.')


def window(start, end, zone, work_start, work_end, duration):
    if not 0 <= (end - start).days <= 30:
        raise ValueError('Choose an inclusive date range of at most 31 days, with the end on or after the start.')
    for clock in (work_start, work_end):
        if clock.tzinfo or clock.second or clock.microsecond or clock.minute % INTERVAL:
            raise ValueError('Working hours must use whole 15-minute intervals, for example 09:00.')
    minutes = (work_end.hour - work_start.hour) * 60 + work_end.minute - work_start.minute
    if not INTERVAL <= duration <= minutes or duration % INTERVAL:
        raise ValueError('Duration must fit inside the working day and be a positive multiple of 15 minutes.')
    return (datetime.combine(start, time.min, zone).astimezone(UTC),
            datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(UTC))


def fetch_availability(settings, start, end, password):
    try:
        from exchangelib import Configuration, Credentials, EWSDateTime, NTLM
        from exchangelib.protocol import Protocol
    except ImportError:
        raise ValueError('Install the optional dependency: .venv/bin/python -m pip install -r requirements-calendar.txt') from None
    # Exchange debug logs may contain authentication or mailbox details.
    logging.getLogger('exchangelib').disabled = True
    logging.getLogger('exchangelib').setLevel(logging.CRITICAL)
    Protocol.TIMEOUT = 30
    protocol = Protocol(config=Configuration(service_endpoint=ENDPOINT, auth_type=NTLM,
                        credentials=Credentials(settings['username'], password), max_connections=1))
    try:
        views = list(protocol.get_free_busy_info(
            accounts=[(person['email'], 'Required', False) for person in settings['people']],
            start=EWSDateTime.from_datetime(start.astimezone(UTC)),
            end=EWSDateTime.from_datetime(end.astimezone(UTC)),
            merged_free_busy_interval=INTERVAL, requested_view='MergedOnly'))
        return decode_views(settings['people'], views, int((end - start).total_seconds() // (60 * INTERVAL)))
    except Exception as error:
        # Do not print server response bodies or authentication headers.
        raise ValueError(f'Exchange lookup failed ({type(error).__name__}). Check your university login, network access and calendar permissions.') from None
    finally:
        protocol.close()
        protocol.config._credentials = None
        Protocol.clear_cache()


def decode_views(people, views, expected):
    if len(views) != len(people):
        raise ValueError('Exchange returned an unexpected number of calendars; results cannot be matched safely.')
    calendars, warnings = {}, []
    for person, view in zip(people, views):
        merged = getattr(view, 'merged', None)
        if isinstance(view, Exception) or not isinstance(merged, str) or len(merged) != expected:
            merged = '?' * expected
            warnings.append(f'{person["name"]}: calendar unavailable or incomplete; availability is unknown.')
        elif any(value not in '0123' for value in merged):
            warnings.append(f'{person["name"]}: some calendar intervals are unknown.')
        calendars[person['email']] = merged
    return calendars, warnings


def slot_status(merged, offset, size):
    values = merged[offset:offset + size] if offset >= 0 else ''
    if any(value in '123' for value in values):
        return 'busy'
    if len(values) != size or any(value != '0' for value in values):
        return 'unknown'
    return 'free'


def find_slots(people, calendars, origin, start, end, zone, work_start, work_end, duration, excluded=()):
    slots, day = [], start
    while day <= end:
        if day.weekday() < 5 and day not in excluded:
            cursor = datetime.combine(day, work_start, zone).astimezone(UTC)
            stop = datetime.combine(day, work_end, zone).astimezone(UTC)
            while cursor + timedelta(minutes=duration) <= stop:
                offset = int((cursor - origin).total_seconds() // (60 * INTERVAL))
                statuses = {person['email']: slot_status(calendars.get(person['email'], ''), offset, duration // INTERVAL)
                            for person in people}
                slots.append(dict(start=cursor.astimezone(zone).isoformat(),
                                  end=(cursor + timedelta(minutes=duration)).astimezone(zone).isoformat(),
                                  free=sum(value == 'free' for value in statuses.values()),
                                  busy=sum(value == 'busy' for value in statuses.values()),
                                  unknown=sum(value == 'unknown' for value in statuses.values()),
                                  people=statuses))
                cursor += timedelta(minutes=INTERVAL)
        day += timedelta(days=1)
    return sorted(slots, key=lambda row: (-row['free'], row['unknown'], row['start']))


def write_report(settings, slots, warnings, args):
    import io
    report = dict(checked_at=datetime.now(UTC).isoformat(), timezone=settings['timezone'],
                  start=args.start.isoformat(), end=args.end.isoformat(), duration_minutes=args.duration,
                  working_hours=[args.work_start.isoformat(), args.work_end.isoformat()],
                  excluded_dates=[day.isoformat() for day in args.exclude_date],
                  calendars=settings['people'], warnings=warnings, slots=slots)
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    path = PRIVATE / f'slots-{stamp}'
    save_private(path.with_suffix('.json'), json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['Start', 'End', 'Free', 'Busy', 'Unknown', *[p['email'] for p in settings['people']]])
    for row in slots:
        writer.writerow([row[key] for key in ('start', 'end', 'free', 'busy', 'unknown')] +
                        [row['people'][p['email']] for p in settings['people']])
    save_private(path.with_suffix('.csv'), stream.getvalue())
    for warning in warnings:
        print(warning)
    total = len(settings['people'])
    common = sum(row['free'] == total for row in slots)
    print(f'\n{common} candidate start time(s) with all {total} calendars free. Times: {settings["timezone"]}.')
    print('Tentative and out-of-office intervals count as busy. Unknown intervals never count as free.')
    print('Calendar gaps are suggestions, not confirmed attendance. Weekends and excluded dates are omitted; holidays are not automatic.\n')
    for row in slots[:20]:
        start, end = datetime.fromisoformat(row['start']), datetime.fromisoformat(row['end'])
        conflicts = ', '.join(f'{p["name"]}: {row["people"][p["email"]]}' for p in settings['people'] if row['people'][p['email']] != 'free')
        print(f'{start:%d.%m.%Y %H:%M}–{end:%H:%M}  {row["free"]}/{total} free' + (f'  ({conflicts})' if conflicts else ''))
    print(f'\nFull private results: {path.with_suffix(".csv")}\nNothing sent or changed in Outlook or the seminar app.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('setup', help='Copy professor names and emails into private local settings')
    init.add_argument('--roster', type=Path, default=ROOT / 'data/participants.csv')
    init.add_argument('--username', help='University login name; defaults to the local email-service username')
    check = commands.add_parser('check', help='Prompt locally for a password and check free/busy availability')
    check.add_argument('--start', required=True, type=date.fromisoformat, help='First date, YYYY-MM-DD')
    check.add_argument('--end', required=True, type=date.fromisoformat, help='Last date, inclusive, YYYY-MM-DD')
    check.add_argument('--duration', type=int, default=120, help='Minutes, default 120')
    check.add_argument('--work-start', type=time.fromisoformat, default=time(9))
    check.add_argument('--work-end', type=time.fromisoformat, default=time(17))
    check.add_argument('--exclude-date', type=date.fromisoformat, action='append', default=[], help='Omit a holiday or other date; repeat as needed')
    args = parser.parse_args()
    try:
        if args.command == 'setup':
            setup(args.roster, args.username)
            return
        settings = json.loads((PRIVATE / 'settings.json').read_text())
        settings['people'] = validate_people(settings['people'])
        if not settings.get('username'):
            raise ValueError('Set your university username in .private/calendar/settings.json.')
        zone = ZoneInfo(settings['timezone'])
        start, end = window(args.start, args.end, zone, args.work_start, args.work_end, args.duration)
        if not sys.stdin.isatty():
            raise ValueError('Run this command in a local Terminal to enter your password privately.')
        print(f'Checking {len(settings["people"])} calendars at {ENDPOINT}')
        print('Calendars: ' + ', '.join(p['name'] for p in settings['people']))
        password = getpass.getpass('University password (not stored): ')
        if not password:
            raise ValueError('No password entered.')
        try:
            calendars, warnings = fetch_availability(settings, start, end, password)
        finally:
            password = None
        slots = find_slots(settings['people'], calendars, start, args.start, args.end, zone,
                           args.work_start, args.work_end, args.duration, args.exclude_date)
        write_report(settings, slots, warnings, args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'Calendar check stopped: {error}\n')
    except (KeyboardInterrupt, EOFError):
        parser.exit(1, '\nCalendar check cancelled.\n')


if __name__ == '__main__':
    main()
