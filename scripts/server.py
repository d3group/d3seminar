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
from zoneinfo import ZoneInfo

import seminar

RESPONSES = {'confirmed', 'change_requested', 'declined'}


class Store:
    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(exist_ok=True, parents=True, mode=0o700)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS invitations (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')
        self.database.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def validate(self, invite):
        if not isinstance(invite, dict):
            raise ValueError('Invalid invitation.')
        for field, pattern in [('id', r'[a-f0-9]{24}'), ('token_hash', r'[a-f0-9]{64}'), ('revision', r'[a-f0-9]{64}')]:
            if not isinstance(invite.get(field), str) or not re.fullmatch(pattern, invite[field]):
                raise ValueError('Invalid invitation.')
        if not isinstance(invite.get('name'), str) or not 1 <= len(invite['name']) <= 120 or any(c in invite['name'] for c in '\r\n'):
            raise ValueError('Invalid participant name.')
        seminar.email_address(invite['email'])
        event = invite['event']
        expected = {'campaign_id', 'end', 'location', 'slot_id', 'start', 'timezone', 'title'}
        if set(event) != expected or any(not isinstance(value, str) or not 1 <= len(value) <= 300 for value in event.values()):
            raise ValueError('Invalid seminar details.')
        ZoneInfo(event['timezone'])
        start, end = datetime.fromisoformat(event['start']), datetime.fromisoformat(event['end'])
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError('Invalid seminar time.')
        if seminar.event_revision(event) != invite['revision']:
            raise ValueError('Seminar revision does not match.')
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
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = {row[0]: json.loads(row[1]) for row in connection.execute('SELECT id, data FROM invitations')}
            current = set()
            for invite in invites:
                current.add(invite['id'])
                old = existing.get(invite['id'])
                preserve = old and old['active'] and old['token_hash'] == invite['token_hash'] and old['revision'] == invite['revision']
                record = dict(invite, active=True, response=old['response'] if preserve else 'pending',
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
        return record

    def invitation(self, token):
        with self.connect() as connection:
            record = self.lookup(connection, token)
        return {key: record[key] for key in ('name', 'event', 'revision', 'response', 'comment', 'updated_at')}

    def respond(self, params):
        response = params.get('response')
        comment = params.get('comment', '')
        if response not in RESPONSES:
            raise ValueError('Please choose a response.')
        if not isinstance(comment, str) or len(comment) > 1000:
            raise ValueError('Please keep your note under 1,000 characters.')
        comment = comment.strip()
        if response == 'change_requested' and not comment:
            raise ValueError('Please suggest an alternative time.')
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            record = self.lookup(connection, params.get('token'))
            if params.get('revision') != record['revision']:
                raise ValueError('The seminar details have changed. Please use your latest invitation.')
            if record['response'] != response or record['comment'] != comment:
                record.update(response=response, comment=comment, updated_at=datetime.now(timezone.utc).isoformat())
                connection.execute('UPDATE invitations SET data=? WHERE id=?', (json.dumps(record), record['id']))
        return record

    def responses(self):
        with self.connect() as connection:
            records = [json.loads(row[0]) for row in connection.execute('SELECT data FROM invitations')]
        keys = ('id', 'email', 'revision', 'response', 'comment', 'updated_at', 'token_hash')
        return [{key: record[key] for key in keys} for record in records if record['active']]


def page(title, body):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="referrer" content="same-origin"><title>' + html.escape(title) + ' · D3 Seminar</title>'
            '<link rel="stylesheet" href="/styles.css"></head><body><main class="receipt-page">'
            '<span class="eyebrow">D3 / RESEARCH SEMINAR</span><h1>' + html.escape(title) + '</h1>' + body + '</main></body></html>')


def receipt(record, token):
    messages = {'confirmed':'Your proposed time is confirmed.', 'change_requested':'Your request for another time has been saved.',
                'declined':'The organizer will know that you cannot take part.'}
    when = datetime.fromisoformat(record['event']['start']).astimezone(ZoneInfo(record['event']['timezone']))
    link = record['site_url'] + '#' + urlencode(dict(token=token, slot=record['event']['slot_id'], name=record['name'], revision=record['revision']))
    return page('Response saved.', '<p>Thank you, ' + html.escape(record['name']) + '.</p><p>' + messages[record['response']]
                + '</p><div class="notice">' + html.escape(record['event']['title']) + '<br>'
                + html.escape(when.strftime('%A, %d %B %Y, %H:%M') + ' (' + record['event']['timezone'] + ')') + '<br>'
                + html.escape(record['event']['location']) + '</div><p>You can return to your invitation to update your response. The organizer will email the final details separately.</p>'
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
            files = {'/':'index.html','/index.html':'index.html','/styles.css':'styles.css','/app.js':'app.js'}
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
            elif route == '/api/admin':
                secret = request.get('secret')
                if not isinstance(secret, str) or not hmac.compare_digest(secret.encode(), self.server.admin_secret.encode()):
                    self.reply(403, {'ok':False, 'error':'Unauthorized.'})
                elif request.get('action') == 'sync':
                    self.reply(200, {'ok':True, 'count':self.server.store.sync(request.get('invites'))})
                elif request.get('action') == 'responses':
                    self.reply(200, {'ok':True, 'records':self.server.store.responses()})
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
