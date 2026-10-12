#!/usr/bin/env python3
"""Stage a private attendee CSV in the existing SQLite database; do not activate invitations or send mail."""

import argparse, csv, fcntl, os, re, secrets, tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from database import make_engine, transaction, upgrade
from models import User
import seminar

FIELDS = ['name', 'email', 'role', 'id', 'token', 'role_status']
ROLES = {'Professor', 'PhD', 'Postdoc', 'Research assistant', 'Associated member', 'Unconfirmed'}
ROLE_STATUSES = {'confirmed', 'inferred', 'unconfirmed'}


def stage(csv_file, database):
    csv_file, database = Path(csv_file), Path(database)
    if csv_file.is_symlink() or database.is_symlink():
        raise ValueError('Use regular private files, not symlinks.')
    if any(path.resolve().is_relative_to((seminar.ROOT / 'site').resolve()) for path in (csv_file, database)):
        raise ValueError('The roster and database must stay outside the public site directory.')
    with csv_file.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'name', 'email', 'role'} <= set(reader.fieldnames or []) or set(reader.fieldnames) - set(FIELDS):
            raise ValueError('Expected name,email,role and optional id,token,role_status columns.')
        rows = list(reader)
    if not 1 <= len(rows) <= 100:
        raise ValueError('Provide 1–100 attendees.')
    csv_file.chmod(0o600)
    database.chmod(0o600)
    temporary = None
    with (database.parent / 'command.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        legacy = {}
        for path in [database.parent / 'invitations.json', *sorted((database.parent / 'events').glob('*/invitations.json'))]:
            if not path.exists():
                continue
            for invite in seminar.read_json(path):
                address = seminar.email_address(invite['email'])
                identity = {key:invite[key] for key in ('id', 'token')}
                if address in legacy and legacy[address] != identity:
                    raise ValueError('Existing events have conflicting personal tokens. Resolve this before staging users.')
                legacy[address] = identity
        engine = make_engine(database, create=False)
        try:
            upgrade(engine)
            with transaction(engine, write=True) as session:
                current = [{key:getattr(user, key) for key in FIELDS} for user in session.scalars(select(User))]
                seen = {key:set() for key in ('id', 'email', 'token')}
                unnamed_addresses = set()
                for index, row in enumerate(rows, 2):
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f'Row {index}: check the number of CSV fields.')
                    row.update({key:row.get(key, '').strip() for key in FIELDS})
                    if not 1 <= len(row['name']) <= 120 or any(ord(c) < 32 for c in row['name']):
                        raise ValueError(f'Row {index}: enter a valid name.')
                    if row['email']:
                        row['email'] = seminar.email_address(row['email'])
                    elif row['name'] in unnamed_addresses:
                        raise ValueError(f'Row {index}: duplicate name without an email address.')
                    else:
                        unnamed_addresses.add(row['name'])
                    if row['role'] not in ROLES:
                        raise ValueError(f'Row {index}: choose a supported academic role.')
                    row['role_status'] = row['role_status'] or 'unconfirmed'
                    if row['role_status'] not in ROLE_STATUSES:
                        raise ValueError(f'Row {index}: invalid role status.')
                    matches = [user for user in current if
                               (row['id'] and user['id'] == row['id']) or
                               (row['email'] and user['email'] == row['email']) or
                               (not row['id'] and not row['email'] and not user['email'] and user['name'] == row['name'])]
                    if len(matches) > 1:
                        raise ValueError(f'Row {index}: conflicting user identities.')
                    identities = matches + ([legacy[row['email']]] if row['email'] in legacy else [])
                    for key, pattern, generate in (
                        ('id', r'[a-f0-9]{24}', lambda:secrets.token_hex(12)),
                        ('token', r'[A-Za-z0-9_-]{43}', lambda:secrets.token_urlsafe(32)),
                    ):
                        known = {identity[key] for identity in identities}
                        if row[key]:
                            known.add(row[key])
                        if len(known) > 1:
                            raise ValueError(f'Row {index}: existing {key} must be preserved.')
                        row[key] = next(iter(known)) if known else generate()
                        if not re.fullmatch(pattern, row[key]):
                            raise ValueError(f'Row {index}: invalid {key}.')
                    for key in seen:
                        if row[key] and row[key] in seen[key]:
                            raise ValueError(f'Row {index}: duplicate {key}.')
                        seen[key].add(row[key])
                    session.merge(User(**dict(row, email=row['email'] or None)))
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=csv_file.parent,
                                                 prefix='.attendees-', delete=False) as stream:
                    temporary = Path(stream.name)
                    writer = csv.DictWriter(stream, fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)
                    stream.flush()
                    os.fsync(stream.fileno())
            # The database retains generated identities if exporting the CSV fails; rerunning reuses them.
            temporary.replace(csv_file)
        finally:
            engine.dispose()
            if temporary:
                temporary.unlink(missing_ok=True)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, required=True, help='Private CSV to enrich with stable ids and tokens')
    parser.add_argument('--database', type=Path, required=True, help='Existing root responses.sqlite3 database')
    args = parser.parse_args()
    try:
        count = stage(args.csv, args.database)
    except (ValueError, OSError, SQLAlchemyError) as error:
        parser.exit(1, f'Could not stage users: {error}\n')
    print(f'Staged {count} users with matching CSV tokens. Event invitations unchanged. Nothing sent.')


if __name__ == '__main__':
    main()
