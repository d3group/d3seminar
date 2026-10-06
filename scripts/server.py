#!/usr/bin/env python3
"""Serve the seminar API and SQLite storage with Waitress."""

import hmac
import html
import json
import os
from email.message import Message
from http import HTTPStatus
from waitress import create_server as waitress_server
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import seminar
import presentations
from organizer import Organizer, write_config

ANSWERS = {'available', 'unavailable'}


class Store:
    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(exist_ok=True, parents=True, mode=0o700)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS invitations (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS poll_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS history (saved_at TEXT NOT NULL, data TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS presentations (participant_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS presentation_files (id TEXT PRIMARY KEY, participant_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL, content BLOB NOT NULL, UNIQUE(participant_id, kind))')
        self.database.chmod(0o600)
        self.retire_conditional_answers()

    def retire_conditional_answers(self):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            records, state = self.records(connection), self.state(connection)
            affected = [record for record in records if 'if_needed' in record.get('answers', {}).values()]
            if not affected and not any(key in state for key in ('proposed_slot_id', 'proposal_id')):
                return
            connection.execute('INSERT INTO history VALUES (?, ?)', (seminar.now(), json.dumps(dict(records=records, state=state))))
            for record in affected:
                record['answers'] = {key:value for key,value in record['answers'].items() if value != 'if_needed'}
                connection.execute('UPDATE invitations SET data=? WHERE id=?', (json.dumps(record), record['id']))
            self.write_state(connection, {key:value for key,value in state.items() if key not in ('proposed_slot_id', 'proposal_id')})

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def state(self, connection):
        row = connection.execute('SELECT data FROM poll_state WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {}

    def write_state(self, connection, state):
        connection.execute('INSERT INTO poll_state VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (json.dumps(state),))

    def records(self, connection):
        return [json.loads(row[0]) for row in connection.execute('SELECT data FROM invitations ORDER BY id')]

    def validate(self, invite):
        if not isinstance(invite, dict):
            raise ValueError('Invalid invitation.')
        for field, pattern in [('id', r'[a-f0-9]{24}'), ('token_hash', r'[a-f0-9]{64}'), ('revision', r'[a-f0-9]{64}')]:
            if not isinstance(invite.get(field), str) or not re.fullmatch(pattern, invite[field]):
                raise ValueError('Invalid invitation.')
        if not isinstance(invite.get('name'), str) or not 1 <= len(invite['name']) <= 120 or any(c in invite['name'] for c in '\r\n'):
            raise ValueError('Invalid participant name.')
        if seminar.email_address(invite['email']) != invite['email']:
            raise ValueError('Normalize participant email addresses first.')
        seminar.validate_poll(invite['poll'])
        if seminar.event_revision(invite['poll']) != invite['revision']:
            raise ValueError('Poll revision does not match.')
        seminar.validate_site_url(invite['site_url'])

    def sync(self, invites):
        if not isinstance(invites, list) or not 1 <= len(invites) <= 100:
            raise ValueError('Provide 1–100 invitations.')
        for invite in invites:
            self.validate(invite)
        seminar.validate_roles(invites)
        for field in ('id', 'email', 'token_hash'):
            if len({item[field] for item in invites}) != len(invites):
                raise ValueError('Duplicate ' + field + '.')
        if len({item['revision'] for item in invites}) != 1:
            raise ValueError('All participants must receive the same candidate times.')
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = {row['id']: row for row in self.records(connection)}
            state = self.state(connection)
            def identity(records):
                return sorted((r['id'], r['email'], r['token_hash'], r['revision']) for r in records)
            changed = identity(invites) != identity(r for r in existing.values() if r['active'])
            if changed and state.get('final_event'):
                raise ValueError('This seminar is finalized. Archive it and start a new campaign before changing the roster or times.')
            if changed:
                if existing:
                    connection.execute('INSERT INTO history VALUES (?, ?)', (seminar.now(), json.dumps(dict(records=list(existing.values()), state=state))))
                self.write_state(connection, {key:value for key,value in state.items() if key.startswith('programme')})
            current = set()
            for invite in invites:
                current.add(invite['id'])
                old = existing.get(invite['id'])
                preserve = old and old['active'] and 'poll' in old and old['token_hash'] == invite['token_hash'] and old['revision'] == invite['revision']
                record = dict(invite, active=True, answers=old['answers'] if preserve else {},
                              comment=old['comment'] if preserve else '', updated_at=old['updated_at'] if preserve else '')
                connection.execute('INSERT INTO invitations VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET token_hash=excluded.token_hash, data=excluded.data',
                                   (record['id'], record['token_hash'], json.dumps(record)))
            for key, old in existing.items():
                if key not in current:
                    old['active'] = False
                    connection.execute('UPDATE invitations SET data=? WHERE id=?', (json.dumps(old), key))
        return len(invites)

    def lookup(self, connection, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise ValueError('This invitation link is invalid.')
        row = connection.execute('SELECT data FROM invitations WHERE token_hash=?', (seminar.digest(token),)).fetchone()
        record = json.loads(row[0]) if row else None
        if not record or not record['active']:
            raise ValueError('This invitation has expired or is invalid. Please ask the organizer for a new link.')
        if 'poll' not in record:
            raise ValueError('The organizer is updating this invitation. Please try again shortly.')
        return record

    def invitation(self, token, include_votes=False, include_presentations=False):
        with self.connect() as connection:
            connection.execute('BEGIN')
            record = self.lookup(connection, token)
            result = {key: record[key] for key in ('name', 'poll', 'revision', 'answers', 'comment', 'updated_at')}
            result['state'] = seminar.programme_state(self.state(connection))
            result['role'] = record.get('role', '')
            result['presentation'] = presentations.own(connection, record['id']) if result['role'] in presentations.PRESENTER_ROLES else {}
            if include_presentations:
                result['presentations'] = presentations.listing(self, connection, record['id'])
            if include_votes:
                people = sorted(self.records(connection), key=lambda person: (person['name'].casefold(), person['id']))
                result['participants'] = [dict(name=person['name'], role=person.get('role', ''), answers=person['answers']) for person in people
                                          if person['active'] and person.get('revision') == record['revision'] and 'poll' in person]
            return result

    def public_event(self, fallback_poll):
        with self.connect() as connection:
            connection.execute('BEGIN')
            records = [r for r in self.records(connection) if r['active'] and 'poll' in r]
            poll = seminar.poll_data(records[0]['poll'] if records else fallback_poll)
            records = [r for r in records if r['revision'] == seminar.event_revision(poll)]
            totals = {}
            for slot in poll['slots']:
                counts = {value:sum(r['answers'].get(slot['id']) == value for r in records) for value in ('available', 'unavailable')}
                counts['unknown'] = max(poll['required_participants'], len(records)) - sum(counts.values())
                totals[slot['id']] = counts
            final = self.state(connection).get('final_event')
            # Public data is an explicit projection: never include the private programme or roster.
            final = {key:final[key] for key in ('slot_id', 'start', 'end')} if final else None
            return dict(poll=poll, final_event=final, totals=totals, presentations=presentations.listing(self, connection))

    def respond(self, params):
        partial = params.get('partial', 'false')
        if partial not in ('true', 'false'):
            raise ValueError('Invalid response mode.')
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            record = self.lookup(connection, params.get('token'))
            comment = record['comment'] if partial == 'true' else params.get('comment', '')
            if not isinstance(comment, str) or len(comment) > 1000:
                raise ValueError('Please keep your note under 1,000 characters.')
            if params.get('revision') != record['revision']:
                raise ValueError('The candidate times have changed. Reopen your invitation and check all options again.')
            state = self.state(connection)
            if state.get('final_event'):
                raise ValueError('The seminar time is finalized. Please contact Aabhash if your availability has changed.')
            ids = {slot['id'] for slot in record['poll']['slots']}
            answers = dict(record['answers']) if partial == 'true' else {}
            for key, value in params.items():
                if key.startswith('answer:'):
                    slot_id = key[7:]
                    if slot_id not in ids or value not in ANSWERS | {''}:
                        raise ValueError('Invalid availability option.')
                    if value:
                        answers[slot_id] = value
                    else:
                        answers.pop(slot_id, None)
            if record['answers'] != answers or record['comment'] != comment.strip() or not record['updated_at']:
                record.update(answers=answers, comment=comment.strip(), updated_at=datetime.now(timezone.utc).isoformat())
                connection.execute('UPDATE invitations SET data=? WHERE id=?', (json.dumps(record), record['id']))
        return record

    def responses(self):
        with self.connect() as connection:
            keys = ('id', 'name', 'email', 'revision', 'answers', 'comment', 'updated_at', 'token_hash')
            return [{key: record[key] for key in keys} for record in self.records(connection) if record['active'] and 'poll' in record]

    def dashboard(self, connection=None):
        if connection is None:
            with self.connect() as conn:
                conn.execute('BEGIN')
                return self.dashboard(conn)
        records = [r for r in self.records(connection) if r['active'] and 'poll' in r]
        state = seminar.programme_state(self.state(connection))
        poll = records[0]['poll'] if records else None
        slots = []
        required = poll['required_participants'] if poll else 0
        for slot in poll['slots'] if poll else []:
            counts = {value: sum(r['answers'].get(slot['id']) == value for r in records) for value in ANSWERS}
            counts['unknown'] = max(required, len(records)) - sum(counts.values())
            ready = len(records) == required and counts['available'] == required
            slots.append(dict(slot, **counts, ready=ready))
        people = [dict(id=r['id'], name=r['name'], role=r.get('role', ''), email=r['email'], answers=r['answers'], comment=r['comment'], updated_at=r['updated_at'],
                       complete=len(r['answers']) == len(poll['slots'])) for r in records]
        return dict(poll=poll, state=state, participants=people, slots=slots, registered=len(records), required=required,
                    completed=sum(p['complete'] for p in people), snapshot=seminar.event_revision(dict(records=records, state=state)))

    def select(self, action, slot_id, snapshot):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            dashboard = self.dashboard(connection)
            if dashboard['snapshot'] != snapshot:
                raise ValueError('Responses changed. Refresh the dashboard and check the latest availability.')
            state = self.state(connection)
            if state.get('final_event'):
                raise ValueError('This seminar is already finalized.')
            slot = next((s for s in dashboard['slots'] if s['id'] == slot_id), None)
            if not slot:
                raise ValueError('Choose a candidate time.')
            if action == 'finalize':
                if not slot['ready']:
                    raise ValueError('Everyone in the required group must mark this time Available before it can be finalized.')
                state.update(final_event=seminar.event_data(dashboard['poll'], slot), finalized_at=datetime.now(timezone.utc).isoformat())
            else:
                raise ValueError('Unknown selection action.')
            self.write_state(connection, state)
        return state


def page(title, body):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="referrer" content="same-origin"><title>' + html.escape(title) + ' · D3 Seminar</title>'
            '<link rel="stylesheet" href="/styles.css"></head><body><header class="masthead">'
            '<img class="university-logo" src="/assets/uni-wuerzburg.svg" width="347" height="152" alt="Julius-Maximilians-Universität Würzburg">'
            '<img class="d3-logo" src="/assets/d3.svg" width="241" height="48" alt="D3 · Data Driven Decisions">'
            '</header><main class="receipt-page"><h1>' + html.escape(title) + '</h1>' + body + '</main></body></html>')


def receipt(record, token):
    count, total = len(record['answers']), len(record['poll']['slots'])
    message = f'Your availability is saved for {count} of {total} options.'
    message += ' Please return to answer the remaining options.' if count < total else ' The organizer will email the final time once everyone can attend.'
    link = record['site_url'] + 'event.html?' + urlencode(dict(event=record.get('event_id', 'seminar-1'))) + '#' + urlencode(dict(token=token))
    return page('Response saved.', '<p>Thank you, ' + html.escape(record['name']) + '.</p><p>' + message
                + '</p><p>You can use your personal link to update your availability until the time is finalized.</p>'
                + '<a class="primary" href="' + html.escape(link, quote=True) + '">Return to the seminar</a>')


class Handler:
    def __init__(self, application, environ):
        self.server = application
        self.path = environ['PATH_INFO']
        self.rfile = environ['wsgi.input']
        self.headers = Message()
        for key, value in environ.items():
            if key.startswith('HTTP_'):
                self.headers[key[5:].replace('_', '-')] = value
        for key in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            if environ.get(key):
                self.headers[key.replace('_', '-')] = environ[key]

    def reply(self, status, data, content_type='application/json', policy=None, disposition=None):
        body = (json.dumps(data) if content_type == 'application/json' else data)
        body = body.encode() if isinstance(body, str) else body
        self.status, self.body = status, body
        self.response_headers = [
            ('Content-Type', content_type), ('Content-Length', str(len(body))),
            ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer' if policy else 'strict-origin-when-cross-origin'),
            ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
            ('Content-Security-Policy', policy or "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
            ('Vary', 'Origin')]
        if disposition:
            self.response_headers.append(('Content-Disposition', disposition))
        origin = self.headers.get('Origin')
        if origin and origin in self.server.origins():
            self.response_headers.extend([('Access-Control-Allow-Origin', origin),
                                          ('Access-Control-Expose-Headers', 'Content-Disposition')])

    def trusted_request(self):
        if self.headers.get('Host') not in self.server.hosts():
            self.reply(403, {'ok':False, 'error':'Open this service through its configured address.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in self.server.origins():
            self.reply(403, {'ok':False, 'error':'Cross-site requests are not allowed.'})
            return False
        return True

    def do_OPTIONS(self):
        if not self.trusted_request():
            return
        allowed_headers = {'content-type', 'accept', 'x-invitation-token', 'x-event-id',
                           'x-material-kind', 'x-file-name', 'x-presentation-version'}
        requested = {h.strip().lower() for h in self.headers.get('Access-Control-Request-Headers', '').split(',') if h.strip()}
        if (not self.headers.get('Origin') or self.headers.get('Access-Control-Request-Method') not in ('GET', 'POST')
                or not requested <= allowed_headers):
            self.reply(403, {'ok':False, 'error':'This cross-site request is not allowed.'})
            return
        self.reply(204, b'', 'text/plain')
        self.response_headers.extend([('Access-Control-Allow-Methods', 'GET, POST'),
                                      ('Access-Control-Allow-Headers', ', '.join(sorted(allowed_headers))),
                                      ('Access-Control-Max-Age', '600')])

    def event_store(self, request):
        event_id = request.get('event_id', 'seminar-1')
        if not isinstance(event_id, str) or event_id not in self.server.stores:
            raise ValueError('Unknown event.')
        return self.server.stores[event_id]

    def do_GET(self):
        if not self.trusted_request():
            return
        route = urlparse(self.path).path
        if route == '/health':
            self.reply(200, {'ok':True, 'storage':'sqlite'})
        elif route == '/config.json':
            self.reply(200, self.server.config)
        else:
            files = {'/':'index.html','/index.html':'index.html','/event.html':'event.html', '/home.js':'home.js', '/api.js':'api.js', '/shared.js':'shared.js',
                     '/about.html':'about.html','/about.js':'about.js',
                     '/agenda.html':'agenda.html','/agenda.js':'agenda.js',
                     '/styles.css':'styles.css','/app.js':'app.js','/admin':'admin.html','/admin.html':'admin.html','/admin.js':'admin.js',
                     '/assets/d3.svg':'assets/d3.svg','/assets/uni-wuerzburg.svg':'assets/uni-wuerzburg.svg',
                     '/assets/agenda-paper.svg':'assets/agenda-paper.svg'}
            filename = files.get(route)
            if filename:
                types = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.svg':'image/svg+xml'}
                self.reply(200, (seminar.ROOT / 'site' / filename).read_bytes(), types[Path(filename).suffix])
            else:
                self.reply(404, {'ok':False, 'error':'Not found.'})

    def do_POST(self):
        if not self.trusted_request():
            return
        route = urlparse(self.path).path
        is_form = route == '/api/respond'
        wants_json = 'application/json' in self.headers.get('Accept', '')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            limit = presentations.MAX_FILE_SIZE if route == '/api/presentation-upload' else 150000
            if not 0 < size <= limit:
                raise ValueError('Request is empty or too large.')
            if route == '/api/presentation-upload':
                store = self.event_store(dict(event_id=self.headers.get('X-Event-Id')))
                result = presentations.upload(store, self.headers.get('X-Invitation-Token'), self.headers.get('X-Material-Kind'),
                    unquote(self.headers.get('X-File-Name', '')), self.rfile.read(size), self.headers.get('X-Presentation-Version', ''))
                self.reply(200, dict(ok=True, presentation=result))
                return
            data = self.rfile.read(size).decode('utf-8')
            if route == '/api/material':
                if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                    raise ValueError('Open the material from the Agenda page.')
                params = {key:values[0] for key,values in parse_qs(data).items()}
                material = presentations.material(self.event_store(params), params.get('token'), params.get('file_id'))
                inline = material['mime'].startswith('text/html')
                self.reply(200, material['content'], material['mime'], policy=presentations.MATERIAL_POLICY,
                           disposition=('inline' if inline else 'attachment') + "; filename*=UTF-8''" + quote(material['name'], safe=''))
                return
            if is_form:
                if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                    raise ValueError('Use the seminar response form.')
                params = {key: values[0] for key, values in parse_qs(data, keep_blank_values=True).items()}
                record = self.event_store(params).respond(params)
                if wants_json:
                    result = self.event_store(params).invitation(params['token'], include_votes=True) if params.get('partial') == 'true' else {key:record[key] for key in ('answers', 'comment', 'updated_at')}
                    self.reply(200, dict(ok=True, **result))
                else:
                    self.reply(200, receipt(record, params['token']), 'text/html; charset=utf-8')
                return
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('JSON is required.')
            request = json.loads(data)
            if not isinstance(request, dict):
                raise ValueError('Invalid request.')
            store = self.event_store(request)
            if route == '/api/public-event':
                config = seminar.event_config(self.server.config, request.get('event_id'))
                self.reply(200, dict(ok=True, **store.public_event(seminar.poll_data(config))))
            elif route == '/api/public-presentations':
                with store.connect() as connection:
                    connection.execute('BEGIN')
                    self.reply(200, dict(ok=True, presentations=presentations.listing(store, connection)))
            elif route == '/api/invitation':
                self.reply(200, {'ok':True, **store.invitation(request.get('token'), include_votes=request.get('include_votes') is True,
                                                            include_presentations=request.get('include_presentations') is True)})
            elif route == '/api/presentation':
                result = presentations.save(store, request.get('token'), request.get('presentation'), request.get('version', ''), remove=request.get('remove') is True)
                self.reply(200, dict(ok=True, presentation=result))
            elif route == '/api/calendar':
                invitation = store.invitation(request.get('token'))
                event = invitation['state'].get('final_event')
                if not event:
                    raise ValueError('The seminar time is not finalized yet.')
                self.reply(200, seminar.calendar_bytes(event), 'text/calendar; charset=utf-8')
            elif route == '/api/admin':
                secret = request.get('secret')
                if not isinstance(secret, str) or not hmac.compare_digest(secret.encode(), self.server.admin_secret.encode()):
                    self.reply(403, {'ok':False, 'error':'Unauthorized.'})
                elif request.get('action') == 'sync':
                    self.reply(200, {'ok':True, 'count':store.sync(request.get('invites'))})
                elif request.get('action') == 'responses':
                    self.reply(200, {'ok':True, 'records':store.responses()})
                elif request.get('action') == 'dashboard':
                    self.reply(200, {'ok':True, **store.dashboard()})
                elif request.get('action') == 'finalize':
                    with self.server.organizer.lock():
                        state = store.select(request['action'], request.get('slot_id'), request.get('snapshot'))
                    self.reply(200, {'ok':True, 'state':state})
                elif request.get('action') == 'preview_schedule':
                    self.reply(200, dict(ok=True, **self.server.organizer.preview_schedule(request['event_id'], request['proposal'])))
                elif request.get('action') == 'save_schedule':
                    result = self.server.organizer.save_schedule(request['event_id'], request['proposal'], request['preview'], request.get('confirm_reset'))
                    self.reply(200, dict(ok=True, **result))
                elif request.get('action') == 'save_programme':
                    self.server.organizer.save_programme(request['event_id'], request['programme'], request['snapshot'])
                    self.reply(200, dict(ok=True))
                elif request.get('action') == 'preview_mail':
                    result = self.server.organizer.preview_mail(request.get('mail_event', request.get('event_id', 'next')), request['kind'])
                    self.reply(200, dict(ok=True, **result))
                elif request.get('action') == 'claim_mail':
                    result = self.server.organizer.claim_mail(request['event_id'], request['kind'], request['participant_id'], request['preview'], request['message_id'])
                    self.reply(200, dict(ok=True, **result))
                elif request.get('action') == 'finish_mail':
                    self.server.organizer.finish_mail(request['event_id'], request['participant_id'], request['key'], request['message_id'])
                    self.reply(200, dict(ok=True))
                else:
                    raise ValueError('Unknown admin action.')
            else:
                self.reply(404, {'ok':False, 'error':'Not found.'})
        except (ValueError, KeyError, TypeError) as error:
            message = str(error) if isinstance(error, ValueError) else 'Invalid request.'
            if is_form and not wants_json:
                self.reply(400, page('Response not saved.', '<p>' + html.escape(message) + '</p><p>Return to your email invitation and try again.</p>'), 'text/html; charset=utf-8')
            else:
                self.reply(400, {'ok':False, 'error':message})
        except sqlite3.Error:
            self.reply(503, {'ok':False, 'error':'Response storage is unavailable. Please try again.'})
        except OSError:
            self.reply(503, {'ok':False, 'error':'The event files could not be read or saved. Check the local setup and restart the server before retrying.'})


def origins_for(base):
    url = urlparse(base)
    origins = {url.scheme + '://' + url.netloc}
    if url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1'}:
        suffix = ':' + str(url.port) if url.port else ''
        origins.update('http://' + host + suffix for host in ('localhost', '127.0.0.1'))
    return origins


class Application:
    def __init__(self, store=None, config=None, admin_secret=None, config_file=None, public_url=None):
        self.config = config or seminar.load_config()
        self.public_url = public_url or self.config.get('api_url')
        if self.public_url:
            seminar.validate_api_url(self.public_url)
        seminar.validate_site_url(self.config['site_url'])
        self.store = store or Store(seminar.PRIVATE / 'responses.sqlite3')
        self.stores = {'seminar-1':self.store}
        for event in self.config.get('events', []):
            seminar.event_config(self.config, event['id'])
            if event['id'] != 'seminar-1':
                self.stores[event['id']] = Store(self.store.database.parent / 'events' / event['id'] / 'responses.sqlite3')
        self.admin_secret = admin_secret or seminar.settings()['admin_secret']
        config_file = config_file or (store.database.parent / 'config.json' if store else seminar.CONFIG)
        if not config_file.exists():
            write_config(config_file, self.config)
        self.organizer = Organizer(self.config, self.stores, config_file, self.store.database.parent)

    def hosts(self):
        hosts = {f'localhost:{self.port}', f'127.0.0.1:{self.port}'}
        if self.public_url:
            hosts.update(urlparse(origin).netloc for origin in origins_for(self.public_url))
        return hosts

    def origins(self):
        origins = origins_for(self.config['site_url'])
        origins.update('http://' + host for host in self.hosts() if host.startswith(('localhost:', '127.0.0.1:')))
        if self.public_url:
            origins.update(origins_for(self.public_url))
        return origins

    def __call__(self, environ, start_response):
        handler = Handler(self, environ)
        method = environ['REQUEST_METHOD']
        if method in ('GET', 'POST', 'OPTIONS'):
            getattr(handler, 'do_' + method)()
        else:
            handler.reply(405, {'ok':False, 'error':'Method not allowed.'})
            handler.response_headers.append(('Allow', 'GET, POST, OPTIONS'))
        start_response(f'{handler.status} {HTTPStatus(handler.status).phrase}', handler.response_headers)
        return [handler.body]


def create_server(port=8765, store=None, config=None, admin_secret=None, config_file=None, host='127.0.0.1', public_url=None):
    app = Application(store, config, admin_secret, config_file, public_url)
    server = waitress_server(app, host=host, port=port, threads=4, max_request_body_size=presentations.MAX_FILE_SIZE,
                             channel_timeout=30, clear_untrusted_proxy_headers=True)
    app.port = int(server.effective_port)
    server.d3_app = app
    return server


if __name__ == '__main__':
    server = create_server(port=int(os.environ.get('D3_PORT', '8765')), host=os.environ.get('D3_BIND_HOST', '127.0.0.1'))
    print('D3 seminar API is running with Waitress.', flush=True)
    try:
        server.run()
    finally:
        server.task_dispatcher.shutdown()
        server.close()
