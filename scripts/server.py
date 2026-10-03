#!/usr/bin/env python3
"""Serve the seminar and its SQLite response store on this Mac only."""

import hmac
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse

import seminar

ANSWERS = {'available', 'if_needed', 'unavailable'}


class Store:
    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(exist_ok=True, parents=True, mode=0o700)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS invitations (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS poll_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS history (saved_at TEXT NOT NULL, data TEXT NOT NULL)')
        self.database.chmod(0o600)

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
        base = urlparse(invite['site_url'])
        if base.scheme != 'http' or base.hostname not in ('127.0.0.1', 'localhost') or base.path != '/' or base.query or base.fragment:
            raise ValueError('Use the local seminar website for this backend.')

    def sync(self, invites):
        if not isinstance(invites, list) or not 1 <= len(invites) <= 100:
            raise ValueError('Provide 1–100 invitations.')
        for invite in invites:
            self.validate(invite)
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
                self.write_state(connection, {})
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

    def invitation(self, token):
        with self.connect() as connection:
            connection.execute('BEGIN')
            record = self.lookup(connection, token)
            result = {key: record[key] for key in ('name', 'poll', 'revision', 'answers', 'comment', 'updated_at')}
            result['state'] = self.state(connection)
            return result

    def respond(self, params):
        comment = params.get('comment', '')
        if not isinstance(comment, str) or len(comment) > 1000:
            raise ValueError('Please keep your note under 1,000 characters.')
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            record = self.lookup(connection, params.get('token'))
            if params.get('revision') != record['revision']:
                raise ValueError('The candidate times have changed. Reopen your invitation and check all options again.')
            state = self.state(connection)
            if state.get('final_event'):
                raise ValueError('The seminar time is finalized. Please contact Aabhash if your availability has changed.')
            ids = {slot['id'] for slot in record['poll']['slots']}
            answers = {}
            for key, value in params.items():
                if key.startswith('answer:'):
                    slot_id = key[7:]
                    if slot_id not in ids or value not in ANSWERS | {''}:
                        raise ValueError('Invalid availability option.')
                    if value:
                        answers[slot_id] = value
            if record['answers'] != answers or record['comment'] != comment.strip() or not record['updated_at']:
                record.update(answers=answers, comment=comment.strip(), updated_at=datetime.now(timezone.utc).isoformat())
                connection.execute('UPDATE invitations SET data=? WHERE id=?', (json.dumps(record), record['id']))
                if state.get('proposed_slot_id') and answers.get(state['proposed_slot_id']) not in {'available', 'if_needed'}:
                    self.write_state(connection, {})
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
        state = self.state(connection)
        poll = records[0]['poll'] if records else None
        slots = []
        required = poll['required_participants'] if poll else 0
        for slot in poll['slots'] if poll else []:
            counts = {value: sum(r['answers'].get(slot['id']) == value for r in records) for value in ANSWERS}
            counts['unknown'] = max(required, len(records)) - sum(counts.values())
            ready = len(records) == required and counts['available'] == required
            can_propose = len(records) == required and not counts['unknown'] and not counts['unavailable'] and counts['if_needed'] > 0
            slots.append(dict(slot, **counts, ready=ready, can_propose=can_propose))
        people = [dict(id=r['id'], name=r['name'], email=r['email'], answers=r['answers'], comment=r['comment'], updated_at=r['updated_at'],
                       complete=len(r['answers']) == len(poll['slots'])) for r in records]
        return dict(poll=poll, state=state, participants=people, slots=slots, registered=len(records), required=required,
                    completed=sum(p['complete'] for p in people), snapshot=seminar.event_revision(dict(records=records, state=state)))

    def select(self, action, slot_id, snapshot):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            dashboard = self.dashboard(connection)
            if dashboard['snapshot'] != snapshot:
                raise ValueError('Responses changed. Refresh the dashboard and check the latest availability.')
            state = dashboard['state']
            if state.get('final_event'):
                raise ValueError('This seminar is already finalized.')
            slot = next((s for s in dashboard['slots'] if s['id'] == slot_id), None)
            if not slot:
                raise ValueError('Choose a candidate time.')
            if action == 'finalize':
                if not slot['ready']:
                    raise ValueError('Everyone in the required group must mark this time Available before it can be finalized.')
                state.update(final_event=seminar.event_data(dashboard['poll'], slot), finalized_at=datetime.now(timezone.utc).isoformat())
            elif action == 'propose':
                if not slot['can_propose']:
                    raise ValueError('A reconfirmation needs responses from everyone and no Unavailable responses for this time.')
                if state.get('proposed_slot_id') != slot_id:
                    state.update(proposed_slot_id=slot_id, proposal_id=datetime.now(timezone.utc).isoformat())
            else:
                raise ValueError('Unknown selection action.')
            self.write_state(connection, state)
        return state


def page(title, body):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="referrer" content="same-origin"><title>' + html.escape(title) + ' · D3 Seminar</title>'
            '<link rel="stylesheet" href="/styles.css"></head><body><main class="receipt-page">'
            '<span class="eyebrow">D3 / RESEARCH SEMINAR</span><h1>' + html.escape(title) + '</h1>' + body + '</main></body></html>')


def receipt(record, token):
    count, total = len(record['answers']), len(record['poll']['slots'])
    message = f'Your availability is saved for {count} of {total} options.'
    message += ' Please return to answer the remaining options.' if count < total else ' The organizer will email the final time once everyone can attend.'
    link = record['site_url'] + '#' + urlencode(dict(token=token))
    return page('Response saved.', '<p>Thank you, ' + html.escape(record['name']) + '.</p><p>' + message
                + '</p><p>You can use your personal link to update your availability until the time is finalized.</p>'
                + '<a class="primary" href="' + html.escape(link, quote=True) + '">Return to the seminar ↗</a>')


class Handler(BaseHTTPRequestHandler):
    server_version = 'D3Seminar/1.0'

    def log_message(self, *_):
        # Invitation tokens and participant responses must never appear in access logs.
        pass

    def reply(self, status, data, content_type='application/json'):
        body = (json.dumps(data) if content_type == 'application/json' else data)
        body = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def trusted_request(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            self.reply(403, {'ok':False, 'error':'Open this service through localhost.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + host for host in hosts}:
            self.reply(403, {'ok':False, 'error':'Cross-site requests are not allowed.'})
            return False
        return True

    def do_GET(self):
        if not self.trusted_request():
            return
        route = urlparse(self.path).path
        if route == '/health':
            self.reply(200, {'ok':True, 'storage':'sqlite'})
        elif route == '/config.json':
            self.reply(200, self.server.config)
        else:
            files = {'/':'index.html','/index.html':'index.html','/styles.css':'styles.css','/app.js':'app.js','/admin':'admin.html','/admin.js':'admin.js'}
            filename = files.get(route)
            if filename:
                types = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8'}
                self.reply(200, (seminar.ROOT / 'site' / filename).read_bytes(), types[Path(filename).suffix])
            else:
                self.reply(404, {'ok':False, 'error':'Not found.'})

    def do_POST(self):
        if not self.trusted_request():
            return
        route = urlparse(self.path).path
        is_form = route == '/api/respond'
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 150000:
                raise ValueError('Request is empty or too large.')
            data = self.rfile.read(size).decode('utf-8')
            if is_form:
                if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                    raise ValueError('Use the seminar response form.')
                params = {key: values[0] for key, values in parse_qs(data, keep_blank_values=True).items()}
                record = self.server.store.respond(params)
                self.reply(200, receipt(record, params['token']), 'text/html; charset=utf-8')
                return
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('JSON is required.')
            request = json.loads(data)
            if not isinstance(request, dict):
                raise ValueError('Invalid request.')
            if route == '/api/invitation':
                self.reply(200, {'ok':True, **self.server.store.invitation(request.get('token'))})
            elif route == '/api/calendar':
                invitation = self.server.store.invitation(request.get('token'))
                event = invitation['state'].get('final_event')
                if not event:
                    raise ValueError('The seminar time is not finalized yet.')
                self.reply(200, seminar.calendar_bytes(event), 'text/calendar; charset=utf-8')
            elif route == '/api/admin':
                secret = request.get('secret')
                if not isinstance(secret, str) or not hmac.compare_digest(secret.encode(), self.server.admin_secret.encode()):
                    self.reply(403, {'ok':False, 'error':'Unauthorized.'})
                elif request.get('action') == 'sync':
                    self.reply(200, {'ok':True, 'count':self.server.store.sync(request.get('invites'))})
                elif request.get('action') == 'responses':
                    self.reply(200, {'ok':True, 'records':self.server.store.responses()})
                elif request.get('action') == 'dashboard':
                    self.reply(200, {'ok':True, **self.server.store.dashboard()})
                elif request.get('action') in ('propose', 'finalize'):
                    state = self.server.store.select(request['action'], request.get('slot_id'), request.get('snapshot'))
                    self.reply(200, {'ok':True, 'state':state})
                else:
                    raise ValueError('Unknown admin action.')
            else:
                self.reply(404, {'ok':False, 'error':'Not found.'})
        except (ValueError, KeyError, TypeError) as error:
            message = str(error) if isinstance(error, ValueError) else 'Invalid request.'
            if is_form:
                self.reply(400, page('Response not saved.', '<p>' + html.escape(message) + '</p><p>Return to your email invitation and try again.</p>'), 'text/html; charset=utf-8')
            else:
                self.reply(400, {'ok':False, 'error':message})
        except sqlite3.Error:
            self.reply(503, {'ok':False, 'error':'Response storage is unavailable. Please try again.'})


def create_server(port=8765, store=None, config=None, admin_secret=None):
    config = config or seminar.load_config()
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.store = store or Store(seminar.PRIVATE / 'responses.sqlite3')
    server.config = config
    server.admin_secret = admin_secret or seminar.settings()['admin_secret']
    return server


if __name__ == '__main__':
    server = create_server()
    print('D3 seminar is running at http://127.0.0.1:8765/', flush=True)
    print('Private responses are stored in .private/responses.sqlite3. Press Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
