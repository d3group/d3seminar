#!/usr/bin/env python3
"""Serve the seminar API and SQLite storage with Waitress."""

import hmac
import html
import os
from flask import Flask, Response, abort, request, send_from_directory
from werkzeug.exceptions import BadRequest, HTTPException
from waitress import create_server as waitress_server
from pathlib import Path
import re
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlencode, urlparse

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from database import make_engine, transaction, upgrade
from models import Invitation, PollState, history

import seminar
import presentations
import users
import email_queue
from organizer import Organizer, write_config

ANSWERS = {'available', 'unavailable'}


class ResponseConflict(ValueError):
    pass


def response_version(record):
    return seminar.event_revision({key:record[key] for key in ('answers', 'comment', 'updated_at')})


class Store:
    def __init__(self, database):
        self.database = Path(database)
        self.engine = make_engine(self.database)
        upgrade(self.engine)

    def session(self, write=False):
        return transaction(self.engine, write=write)

    def state(self, session):
        return session.scalar(select(PollState.data).where(PollState.id == 1)) or {}

    def write_state(self, session, state):
        session.merge(PollState(id=1, data=state))

    def records(self, session):
        return list(session.scalars(select(Invitation.data).order_by(Invitation.id)))

    def archive(self, session, records, state):
        session.execute(history.insert().values(saved_at=seminar.now(), data=dict(records=records, state=state)))

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
        with self.session(write=True) as session:
            existing = {row['id']: row for row in self.records(session)}
            state = self.state(session)
            def identity(records):
                return sorted((r['id'], r['email'], r['token_hash'], r['revision']) for r in records)
            changed = identity(invites) != identity(r for r in existing.values() if r['active'])
            if changed and state.get('final_event'):
                raise ValueError('This seminar is finalized. Archive it and start a new campaign before changing the roster or times.')
            if changed:
                if existing:
                    self.archive(session, list(existing.values()), state)
                self.write_state(session, {key:value for key,value in state.items() if key.startswith('programme')})
            current = set()
            for invite in invites:
                current.add(invite['id'])
                old = existing.get(invite['id'])
                preserve = old and old['active'] and 'poll' in old and old['token_hash'] == invite['token_hash'] and old['revision'] == invite['revision']
                record = dict(invite, active=True, answers=old['answers'] if preserve else {},
                              comment=old['comment'] if preserve else '', updated_at=old['updated_at'] if preserve else '')
                session.merge(Invitation(id=record['id'], token_hash=record['token_hash'], data=record))
            for key, old in existing.items():
                if key not in current:
                    old['active'] = False
                    session.get(Invitation, key).data = old
        return len(invites)

    def lookup(self, session, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise ValueError('This invitation link is invalid.')
        data = session.scalar(select(Invitation.data).where(Invitation.token_hash == seminar.digest(token)))
        record = data
        if not record or not record['active']:
            raise ValueError('This invitation has expired or is invalid. Please ask the organizer for a new link.')
        if 'poll' not in record:
            raise ValueError('The organizer is updating this invitation. Please try again shortly.')
        return record

    def invitation(self, token, include_votes=False, include_presentations=False):
        with self.session() as session:
            record = self.lookup(session, token)
            result = {key: record[key] for key in ('name', 'poll', 'revision', 'answers', 'comment', 'updated_at')}
            result['response_version'] = response_version(record)
            result['state'] = seminar.programme_state(self.state(session))
            result['role'] = record.get('role', '')
            result['presentation'] = presentations.own(session, record['id']) if result['role'] in presentations.PRESENTER_ROLES else {}
            if include_presentations:
                result['presentations'] = presentations.listing(self, session, record['id'])
                result['presentation_order_version'] = presentations.order_version(result['presentations'])
            if include_votes:
                people = sorted(self.records(session), key=lambda person: (person['name'].casefold(), person['id']))
                result['participants'] = [dict(name=person['name'], role=person.get('role', ''), answers=person['answers']) for person in people
                                          if person['active'] and person.get('revision') == record['revision'] and 'poll' in person]
            return result

    def public_event(self, fallback_poll):
        with self.session() as session:
            records = [r for r in self.records(session) if r['active'] and 'poll' in r]
            poll = seminar.poll_data(records[0]['poll'] if records else fallback_poll)
            records = [r for r in records if r['revision'] == seminar.event_revision(poll)]
            totals = {}
            for slot in poll['slots']:
                counts = {value:sum(r['answers'].get(slot['id']) == value for r in records) for value in ('available', 'unavailable')}
                counts['unknown'] = max(poll['required_participants'], len(records)) - sum(counts.values())
                totals[slot['id']] = counts
            final = self.state(session).get('final_event')
            # Public data is an explicit projection: never include the private programme or roster.
            final = {key:final[key] for key in ('slot_id', 'start', 'end')} if final else None
            return dict(poll=poll, final_event=final, totals=totals, presentations=presentations.listing(self, session))

    def respond(self, params):
        partial = params.get('partial', 'false')
        if partial not in ('true', 'false'):
            raise ValueError('Invalid response mode.')
        with self.session(write=True) as session:
            record = self.lookup(session, params.get('token'))
            comment = record['comment'] if partial == 'true' else params.get('comment', '')
            if not isinstance(comment, str) or len(comment) > 1000:
                raise ValueError('Please keep your note under 1,000 characters.')
            if params.get('revision') != record['revision']:
                raise ValueError('The candidate times have changed. Reopen your invitation and check all options again.')
            state = self.state(session)
            if state.get('final_event'):
                raise ValueError('The seminar time is finalized. Please contact Aabhash if your availability has changed.')
            if partial == 'false' and params.get('response_version') != response_version(record):
                raise ResponseConflict('This form is out of date. Reload this page to review your saved availability before saving again.')
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
                session.get(Invitation, record['id']).data = record
        return record

    def responses(self):
        with self.session() as session:
            keys = ('id', 'name', 'email', 'revision', 'answers', 'comment', 'updated_at', 'token_hash')
            return [{key: record[key] for key in keys} for record in self.records(session) if record['active'] and 'poll' in record]

    def dashboard(self, session=None):
        if session is None:
            with self.session() as conn:
                return self.dashboard(conn)
        records = [r for r in self.records(session) if r['active'] and 'poll' in r]
        state = seminar.programme_state(self.state(session))
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
        with self.session(write=True) as session:
            dashboard = self.dashboard(session)
            if dashboard['snapshot'] != snapshot:
                raise ValueError('Responses changed. Refresh the dashboard and check the latest availability.')
            state = self.state(session)
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
            self.write_state(session, state)
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


def create_app(service):
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = presentations.MAX_FILE_SIZE
    app.url_map.merge_slashes = False
    allowed_headers = {'content-type', 'accept', 'x-invitation-token', 'x-event-id',
                       'x-material-kind', 'x-file-name', 'x-presentation-version'}

    @app.before_request
    def check_request():
        if request.headers.get('Host') not in service.hosts():
            return dict(ok=False, error='Open this service through its configured address.'), 403
        origin = request.headers.get('Origin')
        if origin and origin not in service.origins():
            return dict(ok=False, error='Cross-site requests are not allowed.'), 403
        if request.method == 'OPTIONS':
            requested = {h.strip().lower() for h in request.headers.get('Access-Control-Request-Headers', '').split(',') if h.strip()}
            if not origin or request.headers.get('Access-Control-Request-Method') not in ('GET', 'POST') or not requested <= allowed_headers:
                return dict(ok=False, error='This cross-site request is not allowed.'), 403
            return '', 204, {'Access-Control-Allow-Methods':'GET, POST',
                             'Access-Control-Allow-Headers':', '.join(sorted(allowed_headers)), 'Access-Control-Max-Age':'600'}
        if request.method not in ('GET', 'POST'):
            return dict(ok=False, error='Method not allowed.'), 405, {'Allow':'GET, POST, OPTIONS'}
        if request.method == 'POST':
            request.max_content_length = presentations.MAX_FILE_SIZE if request.path == '/api/presentation-upload' else 150000
            if not 0 < (request.content_length or 0) <= request.max_content_length:
                raise ValueError('Request is empty or too large.')

    @app.after_request
    def secure_response(response):
        response.headers.update({'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff', 'X-Frame-Options':'DENY'})
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        response.vary.add('Origin')
        origin = request.headers.get('Origin')
        if origin and origin in service.origins():
            response.headers.update({'Access-Control-Allow-Origin':origin, 'Access-Control-Expose-Headers':'Content-Disposition'})
        return response

    def invalid(error):
        message = str(error) if isinstance(error, ValueError) else 'Invalid request.'
        status = 409 if isinstance(error, ResponseConflict) else 400
        if request.path == '/api/respond' and 'application/json' not in request.headers.get('Accept', ''):
            body = page('Response not saved.', '<p>' + html.escape(message) + '</p><p>Return to your email invitation and try again.</p>')
            return Response(body, status=status, content_type='text/html; charset=utf-8')
        return dict(ok=False, error=message), status

    for kind in (ValueError, KeyError, TypeError, BadRequest):
        app.register_error_handler(kind, invalid)

    @app.errorhandler(SQLAlchemyError)
    def storage_error(error):
        return dict(ok=False, error='Response storage is unavailable. Please try again.'), 503

    @app.errorhandler(OSError)
    def file_error(error):
        return dict(ok=False, error='The event files could not be read or saved. Check the local setup and restart the server before retrying.'), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        response = error.get_response()
        response.set_data(app.json.dumps(dict(ok=False, error=error.name)))
        response.content_type = 'application/json'
        return response

    def parameters(form=False):
        if form:
            if request.mimetype != 'application/x-www-form-urlencoded':
                raise ValueError('Use the seminar response form or open the material from the Agenda page.')
            request.get_data().decode('utf-8')
            return request.form.to_dict()
        if request.mimetype != 'application/json':
            raise ValueError('JSON is required.')
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Invalid request.')
        return data

    def event_store(data):
        event_id = data.get('event_id', 'seminar-1')
        if not isinstance(event_id, str) or event_id not in service.stores:
            raise ValueError('Unknown event.')
        return service.stores[event_id]

    def invitation_result(store, token, **options):
        result = store.invitation(token, **options)
        result['is_organizer'] = users.organizer_access(service.store, token)
        return result

    def presentation_result(store, token):
        current = invitation_result(store, token, include_presentations=True)
        return dict(ok=True, **{key:current[key] for key in ('presentation', 'presentations', 'presentation_order_version', 'is_organizer')})

    @app.get('/health')
    def health():
        return dict(ok=True, storage='sqlite')

    @app.get('/config.json')
    def config():
        return service.config

    @app.get('/')
    @app.get('/<path:filename>')
    def site_file(filename='index.html'):
        filename = 'admin.html' if filename == 'admin' else filename
        allowed = {'index.html', 'event.html', 'home.js', 'api.js', 'shared.js', 'about.html', 'about.js',
                   'agenda.html', 'agenda.js', 'styles.css', 'app.js', 'admin.html', 'admin.js',
                   'assets/d3.svg', 'assets/uni-wuerzburg.svg', 'assets/agenda-paper.svg'}
        if filename not in allowed:
            abort(404)
        return send_from_directory(seminar.ROOT / 'site', filename, conditional=False, etag=False)

    @app.post('/api/respond')
    def respond():
        data = parameters(form=True)
        store = event_store(data)
        record = store.respond(data)
        if 'application/json' not in request.headers.get('Accept', ''):
            return Response(receipt(record, data['token']), content_type='text/html; charset=utf-8')
        if data.get('partial') == 'true':
            result = invitation_result(store, data['token'], include_votes=True)
        else:
            result = {key:record[key] for key in ('answers', 'comment', 'updated_at')}
            result['response_version'] = response_version(record)
        return dict(ok=True, **result)

    @app.post('/api/<path:unknown>')
    def unknown_api(unknown):
        abort(404)

    @app.post('/api/material')
    def material():
        data = parameters(form=True)
        material = presentations.material(event_store(data), data.get('token'), data.get('file_id'))
        disposition = 'inline' if material['mime'].startswith('text/html') else 'attachment'
        return Response(material['content'], content_type=material['mime'], headers={
            'Content-Security-Policy':presentations.MATERIAL_POLICY, 'Referrer-Policy':'no-referrer',
            'Content-Disposition':disposition + "; filename*=UTF-8''" + quote(material['name'], safe='')})

    @app.post('/api/presentation-upload')
    def upload():
        store = event_store(dict(event_id=request.headers.get('X-Event-Id')))
        token = request.headers.get('X-Invitation-Token')
        presentations.upload(store, token, request.headers.get('X-Material-Kind'),
            unquote(request.headers.get('X-File-Name', '')), request.get_data(), request.headers.get('X-Presentation-Version', ''))
        return presentation_result(store, token)

    @app.post('/api/public-event')
    def public_event():
        data = parameters()
        store = event_store(data)
        config = seminar.event_config(service.config, data.get('event_id'))
        return dict(ok=True, **store.public_event(seminar.poll_data(config)))

    @app.post('/api/public-presentations')
    def public_presentations():
        store = event_store(parameters())
        with store.session() as session:
            return dict(ok=True, presentations=presentations.listing(store, session))

    @app.post('/api/invitation')
    def invitation():
        data = parameters()
        return dict(ok=True, **invitation_result(event_store(data), data.get('token'), include_votes=data.get('include_votes') is True,
                                                           include_presentations=data.get('include_presentations') is True))

    @app.post('/api/presentation')
    def presentation():
        data = parameters()
        store, token = event_store(data), data.get('token')
        presentations.save(store, token, data.get('presentation'), data.get('version', ''), remove=data.get('remove') is True)
        return presentation_result(store, token)

    @app.post('/api/calendar')
    def calendar():
        data = parameters()
        event = event_store(data).invitation(data.get('token'))['state'].get('final_event')
        if not event:
            raise ValueError('The seminar time is not finalized yet.')
        return Response(seminar.calendar_bytes(event), content_type='text/calendar; charset=utf-8')

    @app.post('/api/admin')
    def admin():
        data = parameters()
        secret = data.get('secret')
        store, organizer, action = event_store(data), service.organizer, data.get('action')
        operator = isinstance(secret, str) and hmac.compare_digest(secret.encode(), service.admin_secret.encode())
        if not operator:
            token = data.get('token')
            if not users.organizer_access(service.store, token):
                return dict(ok=False, error='Only an organizer can change the event or running order.'), 403
            try:
                store.invitation(token)
            except ValueError:
                return dict(ok=False, error='An active organizer invitation is required.'), 403
            # Email previews contain other people's access links; reserve them for the local sender.
            if action not in {'dashboard', 'finalize', 'preview_schedule', 'save_schedule', 'save_programme', 'reorder_presentations', 'move_presentation',
                              'users', 'save_user', 'email_draft', 'email_example', 'save_email_draft', 'email_preview', 'queue_mail', 'mail_batches', 'cancel_mail'}:
                return dict(ok=False, error='This action requires the local administration credentials.'), 403
        if action == 'users':
            return dict(ok=True, **users.listing(organizer))
        if action == 'save_user':
            user_id = users.save(organizer, data.get('user'), data.get('version'), None if operator else data.get('token'))
            return dict(ok=True, user_id=user_id, is_organizer=operator or users.organizer_access(service.store, data.get('token')))
        if action == 'email_preview':
            return dict(ok=True, **email_queue.preview(organizer, data.get('mail_event', 'next'), data.get('kind'), data.get('draft_version')))
        if action == 'email_draft':
            return dict(ok=True, **email_queue.draft(organizer, data.get('mail_event', 'next'), data.get('kind')))
        if action == 'email_example':
            return dict(ok=True, **email_queue.example(organizer, data.get('mail_event', 'next'), data.get('kind'),
                        dict(subject=data.get('subject'), body=data.get('body'))))
        if action == 'save_email_draft':
            return dict(ok=True, **email_queue.save_draft(organizer, data.get('mail_event', 'next'), data.get('kind'),
                        dict(subject=data.get('subject'), body=data.get('body')), data.get('version')))
        if action == 'queue_mail':
            batch_id = email_queue.enqueue(organizer, data.get('mail_event', 'next'), data.get('kind'), data.get('version'))
            return dict(ok=True, batch_id=batch_id)
        if action == 'mail_batches':
            return dict(ok=True, batches=email_queue.listing(organizer))
        if action == 'cancel_mail':
            email_queue.cancel(organizer, data.get('batch_id'))
            return dict(ok=True)
        if action == 'preview_queued':
            return dict(ok=True, plans=email_queue.sender_preview(organizer))
        if action == 'sync':
            return dict(ok=True, count=store.sync(data.get('invites')))
        if action == 'responses':
            return dict(ok=True, records=store.responses())
        if action == 'dashboard':
            return dict(ok=True, **store.dashboard())
        if action == 'reorder_presentations':
            store.invitation(data.get('token'))
            with organizer.lock():
                presentations.reorder(store, data.get('ordered_ids'), data.get('version'))
            return presentation_result(store, data.get('token'))
        if action == 'move_presentation':
            destination = event_store(dict(event_id=data.get('destination_event_id')))
            store.invitation(data.get('token'))
            destination.invitation(data.get('token'))
            with organizer.lock():
                presentations.move(store, destination, data.get('participant_id'), data.get('version'))
            return dict(ok=True, source=invitation_result(store, data['token'], include_presentations=True),
                        destination=invitation_result(destination, data['token'], include_presentations=True))
        if action == 'finalize':
            with organizer.lock():
                return dict(ok=True, state=store.select(action, data.get('slot_id'), data.get('snapshot')))
        if action == 'preview_schedule':
            return dict(ok=True, **organizer.preview_schedule(data['event_id'], data['proposal']))
        if action == 'save_schedule':
            return dict(ok=True, **organizer.save_schedule(data['event_id'], data['proposal'], data['preview'], data.get('confirm_reset')))
        if action == 'save_programme':
            organizer.save_programme(data['event_id'], data['programme'], data['snapshot'])
            return dict(ok=True)
        if action == 'preview_mail':
            return dict(ok=True, **organizer.preview_mail(data.get('mail_event', data.get('event_id', 'next')), data['kind']))
        if action == 'claim_mail':
            return dict(ok=True, **organizer.claim_mail(data['event_id'], data['kind'], data['participant_id'], data['preview'], data['message_id'], data.get('batch_id')))
        if action == 'finish_mail':
            organizer.finish_mail(data['event_id'], data['participant_id'], data['key'], data['message_id'])
            return dict(ok=True)
        raise ValueError('Unknown admin action.')

    return app


def origins_for(base):
    url = urlparse(base)
    origins = {url.scheme + '://' + url.netloc}
    if url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1'}:
        suffix = ':' + str(url.port) if url.port else ''
        origins.update('http://' + host + suffix for host in ('localhost', '127.0.0.1'))
    return origins


class Application:
    def __init__(self, store=None, config=None, admin_secret=None, config_file=None, public_url=None):
        config = config or seminar.load_config()
        self.public_url = public_url or config.get('api_url')
        if self.public_url:
            seminar.validate_api_url(self.public_url)
        seminar.validate_site_url(config['site_url'])
        self.store = store or Store(seminar.PRIVATE / 'responses.sqlite3')
        self.stores = {'seminar-1':self.store}
        for event in config.get('events', []):
            seminar.event_config(config, event['id'])
            if event['id'] != 'seminar-1':
                self.stores[event['id']] = Store(self.store.database.parent / 'events' / event['id'] / 'responses.sqlite3')
        self.admin_secret = admin_secret or seminar.settings()['admin_secret']
        config_file = config_file or (store.database.parent / 'config.json' if store else seminar.CONFIG)
        if not config_file.exists():
            write_config(config_file, config)
        self.organizer = Organizer(config, self.stores, config_file, self.store.database.parent)

    @property
    def config(self):
        return self.organizer.config

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


def create_server(port=8765, store=None, config=None, admin_secret=None, config_file=None, host='127.0.0.1', public_url=None):
    app = Application(store, config, admin_secret, config_file, public_url)
    server = waitress_server(create_app(app), host=host, port=port, threads=4, max_request_body_size=presentations.MAX_FILE_SIZE,
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
