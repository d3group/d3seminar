#!/usr/bin/env python3
"""Private roster staging, organizer permissions and user administration."""

import argparse, csv, fcntl, json, os, re, secrets, tempfile
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from database import make_engine, transaction, upgrade
from models import User
import seminar

FIELDS = ['name', 'email', 'role', 'id', 'token', 'role_status']
ROLES = {'Professor', 'PhD', 'Postdoc', 'Research assistant', 'Associated member', 'Unconfirmed'}
ROLE_STATUSES = {'confirmed', 'inferred', 'unconfirmed'}


def organizer_access(store, token):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        return False
    with store.session() as session:
        return session.scalar(select(User.id).where(User.token == token, User.is_organizer.is_(True), User.is_archived.is_(False))) is not None


def set_organizers(database, addresses):
    addresses = {seminar.email_address(address) for address in addresses}
    if not addresses:
        raise ValueError('Choose at least one organizer.')
    engine = make_engine(database, create=False)
    try:
        upgrade(engine)
        with transaction(engine, write=True) as session:
            found = set(session.scalars(select(User.email).where(User.email.in_(addresses), User.is_archived.is_(False))))
            if found != addresses:
                raise ValueError('Every organizer must already be in the private users table.')
            session.execute(update(User).values(is_organizer=False))
            session.execute(update(User).where(User.email.in_(addresses)).values(is_organizer=True))
    finally:
        engine.dispose()


def attached_events(session, stores):
    connection = session.connection()
    schemas = {}
    for index, (event_id, store) in enumerate(stores.items()):
        schema = 'main' if index == 0 else f'event{index}'
        if index:
            connection.exec_driver_sql(f'ATTACH DATABASE ? AS {schema}', (str(store.database.resolve()),))
        schemas[event_id] = schema
    return schemas


def roster(session, schemas):
    memberships = {}
    for event_id, schema in schemas.items():
        for value in session.connection().exec_driver_sql(f'SELECT data FROM {schema}.invitations').scalars():
            person = json.loads(value)
            if person['active']:
                memberships.setdefault(person['id'], []).append(event_id)
    fields = ('id', 'name', 'email', 'role', 'role_status', 'is_organizer', 'is_archived')
    people = [dict({key:getattr(user, key) for key in fields}, events=memberships.get(user.id, []))
              for user in session.scalars(select(User).order_by(User.name, User.id))]
    return dict(users=people, version=seminar.event_revision(people))


def listing(organizer):
    with organizer.stores['seminar-1'].session() as session:
        return roster(session, attached_events(session, organizer.stores))


def save(organizer, data, version, actor_token=None):
    fields = {'id', 'name', 'email', 'role', 'is_organizer', 'is_archived', 'events'}
    if not isinstance(data, dict) or data.keys() - fields:
        raise ValueError('Provide only the user profile and permissions.')
    name, address, role = data.get('name'), data.get('email'), data.get('role')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(ord(c)<32 for c in name):
        raise ValueError('Enter a name of 1–120 characters.')
    if address is not None and not isinstance(address, str):
        raise ValueError('Enter a valid email address.')
    address = seminar.email_address(address) if address else None
    if role not in ROLES or any(type(data.get(key)) is not bool for key in ('is_organizer', 'is_archived')):
        raise ValueError('Choose an academic role and organizer permissions.')
    events = data.get('events')
    if not isinstance(events, list) or any(not isinstance(e, str) or e not in organizer.stores for e in events) or len(set(events)) != len(events):
        raise ValueError('Choose valid events for this participant.')
    archived, appointed = data['is_archived'], data['is_organizer']
    if archived:
        events, appointed = [], False
    if appointed and not events:
        raise ValueError('An organizer needs an active event invitation.')
    if events and not address:
        raise ValueError('An email address is required for event invitations.')
    if events and organizer.config['test_mode']:
        if address != seminar.read_json(organizer.private / 'settings.json')['test_recipient']:
            raise ValueError('Test mode allows only the test address to join events. Save other users as drafts.')
    root = organizer.stores['seminar-1']
    with organizer.lock(), root.session(write=True) as session:
        # Recheck after obtaining the write lock so a revoked organizer cannot change users.
        if actor_token and not session.scalar(select(User.id).where(User.token==actor_token, User.is_organizer.is_(True), User.is_archived.is_(False))):
            raise ValueError('Organizer access has been removed. Reload the page.')
        schemas = attached_events(session, organizer.stores)
        before = roster(session, schemas)
        if before['version'] != version:
            raise ValueError('The user list changed. Refresh it before saving.')
        user = session.get(User, data.get('id')) if data.get('id') else None
        if data.get('id') and not user:
            raise ValueError('Unknown user. Refresh the list.')
        if address and session.scalar(select(User.id).where(User.email==address, User.id != (user.id if user else ''))):
            raise ValueError('That email address already belongs to a user.')
        if not user:
            if len(before['users']) >= 100:
                raise ValueError('The user list supports up to 100 people.')
            user = User(id=secrets.token_hex(12), token=secrets.token_urlsafe(32), role_status='confirmed')
            session.add(user)
        elif user.is_organizer and not appointed and not any(p['id']!=user.id and p['is_organizer'] and not p['is_archived'] and p['events'] for p in before['users']):
            raise ValueError('Keep at least one active organizer. Appoint someone else first.')
        user.name, user.email, user.role = name.strip(), address, role
        user.is_organizer, user.is_archived = appointed, archived
        user.role_status = 'confirmed'
        session.flush()
        for event_id, schema in schemas.items():
            connection = session.connection()
            records = [json.loads(value) for value in connection.exec_driver_sql(f'SELECT data FROM {schema}.invitations').scalars()]
            if address and any(r['email']==address and r['id']!=user.id for r in records):
                raise ValueError('This address already has an event invitation under another user. Import its existing identity before editing it here.')
            old = next((r for r in records if r['id']==user.id), None)
            active = event_id in events
            if not old and not active:
                continue
            state = connection.exec_driver_sql(f'SELECT data FROM {schema}.poll_state WHERE id=1').scalar()
            if state and json.loads(state).get('final_event') and active != bool(old and old['active']) and not archived:
                raise ValueError('Event membership cannot change after its date is finalized.')
            config = seminar.event_config(organizer.config, event_id)
            poll = next((r['poll'] for r in records if r['active']), seminar.poll_data(config))
            record = old or dict(id=user.id, poll=poll, revision=seminar.event_revision(poll), site_url=config['site_url'],
                                 event_id=event_id, answers={}, comment='', updated_at='')
            if old and active and not old['active'] and old['revision'] != seminar.event_revision(poll):
                connection.exec_driver_sql(f'INSERT INTO {schema}.history (saved_at, data) VALUES (?, ?)',
                    (seminar.now(), json.dumps(dict(records=[old], state=json.loads(state) if state else {}))))
                record = dict(old, poll=poll, revision=seminar.event_revision(poll), answers={}, comment='', updated_at='')
            record.update(name=user.name, email=user.email, role={'Professor':'professor', 'PhD':'student'}.get(role, 'member'),
                          token_hash=seminar.digest(user.token), active=active)
            if active:
                organizer.stores[event_id].validate(record)
            connection.exec_driver_sql(f'INSERT INTO {schema}.invitations (id, token_hash, data) VALUES (?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET token_hash=excluded.token_hash, data=excluded.data',
                (user.id, record['token_hash'], json.dumps(record)))
    return user.id


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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--csv', type=Path, help='Private CSV to enrich with stable ids and tokens')
    source.add_argument('--organizers', nargs='+', metavar='EMAIL', help='Replace the organizer list with these existing users')
    parser.add_argument('--database', type=Path, required=True, help='Existing root responses.sqlite3 database')
    args = parser.parse_args()
    try:
        if args.organizers:
            set_organizers(args.database, args.organizers)
            print('Organizer permissions updated. Academic roles, invitations and tokens unchanged. Nothing sent.')
            return
        count = stage(args.csv, args.database)
    except (ValueError, OSError, SQLAlchemyError) as error:
        parser.exit(1, f'Could not stage users: {error}\n')
    print(f'Staged {count} users with matching CSV tokens. Event invitations unchanged. Nothing sent.')


if __name__ == '__main__':
    main()
