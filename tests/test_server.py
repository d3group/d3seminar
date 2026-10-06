import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import seminar
import presentations
from waitress.wasyncore import close_all
from server import Store, create_server, receipt

TOKEN = 'A' * 43
POLL = json.loads((Path(__file__).parent / 'fixtures/programme.json').read_text())
POLL = seminar.poll_data(POLL)
SLOT = POLL['slots'][0]['id']
INVITE = dict(id='a' * 24, name='Ada', email='ada@example.com', token_hash=seminar.digest(TOKEN),
              revision=seminar.event_revision(POLL), poll=POLL, site_url='http://127.0.0.1:8765/')
RESPONSE = dict(token=TOKEN, revision=INVITE['revision'], comment='', **{'answer:' + SLOT:'available'})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'responses.sqlite3')
        self.store.sync([INVITE])

    def test_response_persists_across_store_restart_and_updates_one_row(self):
        saved = self.store.respond(RESPONSE)
        self.store = Store(self.store.database)
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {SLOT:'available'})
        self.assertEqual(self.store.respond(RESPONSE)['updated_at'], saved['updated_at'])
        self.store.respond(dict(RESPONSE, comment='Monday morning.', **{'answer:' + SLOT:'unavailable'}))
        self.assertEqual(len(self.store.responses()), 1)
        self.assertEqual(self.store.invitation(TOKEN)['comment'], 'Monday morning.')

    def test_bad_tokens_statuses_notes_and_stale_revisions_are_rejected(self):
        for bad in [dict(token='B' * 43), dict(token=''), {'answer:' + SLOT:'admin'}, dict(comment='x'*1001),
                    {'answer:nonexistent':'available'}, dict(revision='0'*64)]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.respond(dict(RESPONSE, **bad))
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {})

    def test_https_pages_invitation_preserves_existing_answers_and_rejects_unsafe_urls(self):
        self.store.respond(RESPONSE)
        self.store.sync([dict(INVITE, site_url='https://d3group.github.io/d3seminar/')])
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {SLOT:'available'})
        for url in ('http://outside.example/', 'https://user:password@example.com/', 'https://example.com/#token=x', 'https://example.com/?next=x', 'https://example.com/no-slash', 'javascript:alert(1)'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.store.sync([dict(INVITE, site_url=url)])

    def test_calendar_edits_merge_choices_without_overwriting_other_answers_or_notes(self):
        other = POLL['slots'][1]['id']
        self.store.respond(dict(RESPONSE, comment='Keep my note.'))
        edit = dict(token=TOKEN, revision=INVITE['revision'], partial='true', **{'answer:' + other:'unavailable'})
        saved = self.store.respond(edit)
        self.assertEqual(saved['answers'], {SLOT:'available', other:'unavailable'})
        self.assertEqual(saved['comment'], 'Keep my note.')
        saved = self.store.respond(dict(edit, **{'answer:' + other:''}))
        self.assertEqual(saved['answers'], {SLOT:'available'})
        for bad in [dict(revision='0'*64), {'answer:missing':'available'}, {'answer:' + other:'if_needed'}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.respond(dict(edit, **bad))
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {SLOT:'available'})
        self.store.select('finalize', SLOT, self.store.dashboard()['snapshot'])
        with self.assertRaisesRegex(ValueError, 'finalized'):
            self.store.respond(edit)

    def test_sync_preserves_unchanged_response_and_revokes_removed_participant(self):
        self.store.respond(RESPONSE)
        self.store.sync([INVITE])
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {SLOT:'available'})
        self.store.sync([dict(INVITE, id='b'*24, email='other@example.com', token_hash=seminar.digest('B'*43))])
        with self.assertRaises(ValueError):
            self.store.invitation(TOKEN)
        self.assertEqual(len(self.store.responses()), 1)

    def test_new_token_invalidates_old_link_and_resets_reply(self):
        self.store.respond(RESPONSE)
        self.store.sync([dict(INVITE, token_hash=seminar.digest('B'*43))])
        with self.assertRaises(ValueError):
            self.store.respond(RESPONSE)
        self.assertEqual(self.store.invitation('B'*43)['answers'], {})

    def test_invalid_sync_is_atomic(self):
        for records in [[INVITE, INVITE], [dict(INVITE, revision='0'*64)], [], [None]]:
            with self.assertRaises(ValueError):
                self.store.sync(records)
        self.assertEqual(len(self.store.responses()), 1)

    def test_private_lookup_does_not_return_email_or_token_hash(self):
        response = self.store.invitation(TOKEN)
        self.assertNotIn('email', response)
        self.assertNotIn('token_hash', response)
        self.assertNotIn('participants', response)

    def test_public_event_shares_totals_and_public_names_only(self):
        poll = dict(POLL, required_participants=3)
        first = dict(INVITE, role='student', poll=poll, revision=seminar.event_revision(poll))
        second = dict(first, id='b'*24, name='Bea', email='bea@example.com', token_hash=seminar.digest('B'*43))
        self.store.sync([first, second])
        self.store.respond(dict(RESPONSE, revision=first['revision'], comment='Private note'))
        self.store.respond(dict(RESPONSE, token='B'*43, revision=first['revision'], **{'answer:' + SLOT:'unavailable'}))
        talk = dict(title='Private title', abstract='Private abstract', explainer_url='https://example.org/private', slides_url='')
        presentations.save(self.store, TOKEN, dict(talk, visibility='public'))
        presentations.save(self.store, 'B'*43, dict(talk, visibility='internal'))
        public = self.store.public_event(POLL)
        self.assertEqual(set(public), {'poll', 'final_event', 'totals', 'presentations'})
        self.assertEqual(public['totals'][SLOT], dict(available=1, unavailable=1, unknown=1))
        self.assertEqual(public['totals'][poll['slots'][1]['id']], dict(available=0, unavailable=0, unknown=3))
        self.assertEqual(public['presentations'], [dict(name='Ada')])
        self.assertIsNone(public['final_event'])
        for private in ('Bea', 'email', 'token_hash', 'Private', 'example.org', 'participant_id', 'updated_at', 'revision'):
            self.assertNotIn(private, json.dumps(public))
        self.store.sync([first])
        public = self.store.public_event(POLL)
        self.assertEqual(public['totals'][SLOT], dict(available=1, unavailable=0, unknown=2))
        with self.store.connect() as connection:
            self.store.write_state(connection, dict(final_event=dict(seminar.event_data(poll, poll['slots'][0]), programme=talk), programme=talk))
        public = self.store.public_event(POLL)
        self.assertEqual(public['final_event'], {key:seminar.event_data(poll, poll['slots'][0])[key] for key in ('slot_id', 'start', 'end')})
        self.assertNotIn('Private', json.dumps(public))

    def test_public_event_before_invitations_has_empty_totals(self):
        store = Store(Path(self.temp.name) / 'empty.sqlite3')
        public = store.public_event(POLL)
        self.assertEqual(public['poll'], POLL)
        self.assertEqual(public['totals'][SLOT], dict(available=0, unavailable=0, unknown=POLL['required_participants']))
        self.assertEqual(public['presentations'], [])

    def test_calendar_votes_require_active_invitation_and_exclude_private_fields(self):
        other = dict(INVITE, id='b'*24, name='Bea', email='bea@example.com', token_hash=seminar.digest('B'*43))
        self.store.sync([INVITE, other])
        self.store.respond(dict(RESPONSE, comment='Private organizer note.'))
        self.store.respond(dict(RESPONSE, token='B'*43, comment='Another private note.', **{'answer:' + SLOT:'unavailable'}))
        people = self.store.invitation(TOKEN, include_votes=True)['participants']
        self.assertEqual(people, [dict(name='Ada', role='', answers={SLOT:'available'}), dict(name='Bea', role='', answers={SLOT:'unavailable'})])
        for token in (None, '', 'X'*43):
            with self.assertRaises(ValueError):
                self.store.invitation(token, include_votes=True)
        self.store.sync([INVITE])
        self.assertEqual(self.store.invitation(TOKEN, include_votes=True)['participants'], people[:1])
        with self.assertRaises(ValueError):
            self.store.invitation('B'*43, include_votes=True)

    def test_receipt_escapes_untrusted_names(self):
        self.store.sync([dict(INVITE, name='<img src=x onerror=alert(1)>')])
        body = receipt(self.store.respond(RESPONSE), TOKEN)
        self.assertIn('&lt;img', body)
        self.assertNotIn('<img src=x', body)

    def test_new_round_resets_answers_preserves_link_and_archives_old_response(self):
        self.store.respond(RESPONSE)
        poll = dict(POLL, location='Different room')
        invite = dict(INVITE, poll=poll, revision=seminar.event_revision(poll))
        self.store.sync([invite])
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {})
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.respond(RESPONSE)
        with self.store.connect() as connection:
            history = json.loads(connection.execute('SELECT data FROM history ORDER BY rowid DESC').fetchone()[0])
        self.assertEqual(history['records'][0]['answers'], {SLOT:'available'})

    def test_legacy_conditional_answers_are_archived_and_become_unanswered(self):
        self.store.respond(dict(RESPONSE, comment='Preserve this note'))
        other_slot = POLL['slots'][1]['id']
        with self.store.connect() as connection:
            record = self.store.records(connection)[0]
            record['answers'][other_slot] = 'if_needed'
            connection.execute('UPDATE invitations SET data=? WHERE id=?', (json.dumps(record), record['id']))
            self.store.write_state(connection, dict(proposed_slot_id=other_slot, proposal_id='old', programme=dict(topic='Keep this')))
        self.store = Store(self.store.database)
        current = self.store.invitation(TOKEN)
        self.assertEqual(current['answers'], {SLOT:'available'})
        self.assertEqual(current['comment'], 'Preserve this note')
        self.assertEqual(current['state'], dict(programme=dict(topic='Keep this')))
        with self.store.connect() as connection:
            before = connection.execute('SELECT COUNT(*) FROM history').fetchone()[0]
            history = json.loads(connection.execute('SELECT data FROM history ORDER BY rowid DESC').fetchone()[0])
        self.assertEqual(history['records'][0]['answers'][other_slot], 'if_needed')
        Store(self.store.database)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM history').fetchone()[0], before)
        with self.assertRaises(ValueError):
            self.store.respond(dict(RESPONSE, **{'answer:' + other_slot:'if_needed'}))

    def test_role_sync_preserves_availability_and_shares_only_role_name_and_answers(self):
        self.store.respond(RESPONSE)
        self.store.sync([dict(INVITE, role='student')])
        people = self.store.invitation(TOKEN, include_votes=True)['participants']
        self.assertEqual(people, [dict(name='Ada', role='student', answers={SLOT:'available'})])
        self.store.sync([dict(INVITE, role='professor')])
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {SLOT:'available'})
        with self.assertRaises(ValueError):
            self.store.sync([dict(INVITE, role='invalid')])

    def test_migration_preserves_old_single_slot_rsvp_in_history(self):
        legacy = dict(INVITE, active=True, response='confirmed', comment='Old RSVP', updated_at='old')
        del legacy['poll']
        legacy['revision'] = 'b' * 64
        with self.store.connect() as connection:
            connection.execute('UPDATE invitations SET data=?', (json.dumps(legacy),))
        self.store.sync([INVITE])
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {})
        with self.store.connect() as connection:
            history = json.loads(connection.execute('SELECT data FROM history ORDER BY rowid DESC').fetchone()[0])
        self.assertEqual(history['records'][0]['response'], 'confirmed')


class GroupSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'group.sqlite3')
        self.poll = dict(POLL, required_participants=20)
        self.revision = seminar.event_revision(self.poll)
        self.tokens = [chr(65 + i) * 43 for i in range(20)]
        self.invites = [dict(INVITE, id=f'{i:024x}', name=f'Person {i}', email=f'person{i}@example.com',
                             token_hash=seminar.digest(token), role='professor' if i < 5 else 'student', poll=self.poll, revision=self.revision) for i, token in enumerate(self.tokens)]
        self.store.sync(self.invites)

    def answer(self, index, value='available', all_slots=False):
        slots = self.poll['slots'] if all_slots else self.poll['slots'][:1]
        return self.store.respond(dict(token=self.tokens[index], revision=self.revision, **{'answer:' + s['id']:value for s in slots}))

    def choose(self, action='finalize'):
        return self.store.select(action, SLOT, self.store.dashboard()['snapshot'])

    def test_nineteen_of_twenty_cannot_finalize_and_proposals_are_retired(self):
        for i in range(19):
            self.answer(i)
        slot = self.store.dashboard()['slots'][0]
        self.assertEqual((slot['available'], slot['unknown'], slot['unavailable']), (19, 1, 0))
        for action in ('finalize', 'propose'):
            with self.assertRaises(ValueError):
                self.choose(action)

    def test_calendar_votes_distinguish_all_states_and_unanswered_people(self):
        self.answer(0, 'available')
        self.answer(2, 'unavailable')
        people = self.store.invitation(self.tokens[0], include_votes=True)['participants']
        self.assertEqual(len(people), 20)
        answers = {person['name']: person['answers'].get(SLOT) for person in people}
        self.assertEqual([answers[f'Person {i}'] for i in range(4)], ['available', None, 'unavailable', None])
        self.assertTrue(all(set(person) == {'name', 'role', 'answers'} for person in people))
        self.assertEqual(sum(person['role'] == 'professor' for person in people), 5)
        self.assertEqual(sum(person['role'] == 'student' for person in people), 15)

    def test_everyone_must_be_available_before_finalizing(self):
        for i in range(19):
            self.answer(i)
        with self.assertRaisesRegex(ValueError, 'Everyone'):
            self.choose()
        self.answer(19)
        state = self.choose()
        self.assertEqual(state['final_event']['slot_id'], SLOT)
        with self.assertRaisesRegex(ValueError, 'finalized'):
            self.answer(19, 'unavailable')
        with self.assertRaisesRegex(ValueError, 'finalized'):
            self.store.sync(self.invites[:19])
        self.store.sync(self.invites)
        self.assertEqual(Store(self.store.database).dashboard()['state'], state)

    def test_missing_roster_member_is_unknown_and_blocks_finalization(self):
        self.store.sync(self.invites[:19])
        for i in range(19):
            self.answer(i)
        dashboard = self.store.dashboard()
        self.assertEqual((dashboard['registered'], dashboard['required']), (19, 20))
        self.assertEqual(dashboard['slots'][0]['unknown'], 1)
        with self.assertRaises(ValueError):
            self.choose()

    def test_unavailable_response_blocks_majority_and_no_common_slot(self):
        for i in range(20):
            self.answer(i, 'unavailable' if i == 19 else 'available', all_slots=True)
        dashboard = self.store.dashboard()
        self.assertEqual(dashboard['completed'], 20)
        self.assertFalse(any(s['ready'] for s in dashboard['slots']))
        with self.assertRaises(ValueError):
            self.choose()

    def test_dashboard_cannot_finalize_using_stale_snapshot(self):
        for i in range(20):
            self.answer(i)
        snapshot = self.store.dashboard()['snapshot']
        self.answer(19, 'unavailable')
        with self.assertRaisesRegex(ValueError, 'Responses changed'):
            self.store.select('finalize', SLOT, snapshot)
        self.assertFalse(self.store.dashboard()['state'].get('final_event'))

    def test_partial_response_remains_incomplete_and_explicit_clear_is_unknown(self):
        self.answer(0)
        dashboard = self.store.dashboard()
        self.assertEqual(dashboard['completed'], 0)
        self.assertEqual(dashboard['slots'][1]['unknown'], 20)
        self.store.respond(dict(token=self.tokens[0], revision=self.revision, **{'answer:' + SLOT:''}))
        self.assertEqual(self.store.invitation(self.tokens[0])['answers'], {})

    def test_role_capacity_is_validated_without_changing_saved_answers(self):
        self.answer(0)
        invites = copy.deepcopy(self.invites)
        invites[5]['role'] = 'professor'
        with self.assertRaisesRegex(ValueError, 'more than 5'):
            self.store.sync(invites)
        self.assertEqual(self.store.invitation(self.tokens[0])['answers'], {SLOT:'available'})


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / 'responses.sqlite3')
        cls.store.sync([INVITE])
        config = json.loads((Path(__file__).parent / 'fixtures/programme.json').read_text())
        cls.server = create_server(0, store=cls.store, config=config, admin_secret='secret' * 8)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        cls.port = cls.server.effective_port

    @classmethod
    def tearDownClass(cls):
        cls.server.task_dispatcher.shutdown()
        cls.server.trigger.pull_trigger(lambda: close_all(cls.server._map))
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read().decode()
        connection.close()
        return result

    def test_event_api_keeps_answers_finalization_and_calendar_separate(self):
        config = seminar.event_config(self.server.d3_app.config, 'seminar-2')
        poll = seminar.poll_data(config)
        revision = seminar.event_revision(poll)
        invite = dict(INVITE, name='Seminar Two Participant', poll=poll, revision=revision, event_id='seminar-2')
        headers = {'Content-Type':'application/json'}
        def admin(**data):
            return self.request('POST', '/api/admin', json.dumps(dict(event_id='seminar-2', secret='secret'*8, **data)), headers)
        self.assertEqual(admin(action='sync', invites=[invite])[0], 200)
        prior = self.store.invitation(TOKEN)
        response = dict(token=TOKEN, revision=revision, event_id='seminar-2', **{'answer:' + poll['slots'][0]['id']:'available'})
        status, _ = self.request('POST', '/api/respond', urlencode(response), {'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(status, 200)
        status, body = self.request('POST', '/api/invitation', json.dumps(dict(token=TOKEN, event_id='seminar-2', include_votes=True)), headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['participants'], [dict(name='Seminar Two Participant', role='', answers={poll['slots'][0]['id']:'available'})])
        status, body = self.request('POST', '/api/invitation', json.dumps(dict(token=TOKEN, include_votes=True)), headers)
        self.assertEqual(status, 200)
        self.assertNotIn('Seminar Two Participant', body)
        status, body = admin(action='dashboard')
        state = json.loads(body)
        self.assertEqual(admin(action='finalize', slot_id=poll['slots'][0]['id'], snapshot=state['snapshot'])[0], 200)
        self.assertEqual(self.store.invitation(TOKEN), prior)
        status, calendar = self.request('POST', '/api/calendar', json.dumps(dict(event_id='seminar-2', token=TOKEN)), headers)
        self.assertEqual(status, 200)
        self.assertIn('UID:d3seminar-mvp-2@d3seminar', calendar)
        status, _ = self.request('POST', '/api/calendar', json.dumps(dict(token=TOKEN)), headers)
        self.assertEqual(status, 400)
        response['event_id'] = 'seminar-3'
        self.assertEqual(self.request('POST', '/api/respond', urlencode(response), {'Content-Type':'application/x-www-form-urlencoded'})[0], 400)
        status, body = self.request('POST', '/api/invitation', json.dumps(dict(event_id='../../outside', token=TOKEN)), headers)
        self.assertEqual(status, 400)
        self.assertIn('Unknown event', body)

    def test_static_server_never_exposes_private_files_or_source(self):
        for path in ['/.private/settings.json', '/../.private/settings.json', '/scripts/server.py', '/.git/config']:
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertEqual(self.request('GET', '/about.html')[0], 200)
        self.assertEqual(self.request('GET', '/about.js')[0], 200)
        self.assertEqual(self.request('GET', '/agenda.html')[0], 200)
        self.assertEqual(self.request('GET', '/agenda.js')[0], 200)

    def test_proxy_accepts_only_configured_frontend_and_local_cli(self):
        headers = {'Host':'localhost:8080', 'Origin':'http://localhost:8080'}
        self.assertEqual(self.request('GET', '/health', headers=headers)[0], 403)
        original = self.server.d3_app.public_url
        self.server.d3_app.public_url = 'http://localhost:8080/'
        try:
            self.assertEqual(self.request('GET', '/health', headers=headers)[0], 200)
            self.assertEqual(self.request('GET', '/health')[0], 200)
            self.assertEqual(self.request('GET', '/health', headers={'Host':'127.0.0.1:8080', 'Origin':'http://127.0.0.1:8080'})[0], 200)
            for override in [{'Host':'evil.example'}, {'Origin':'https://evil.example'}, {'Origin':'http://localhost:8081'}]:
                self.assertEqual(self.request('GET', '/health', headers={**headers, **override})[0], 403)
        finally:
            self.server.d3_app.public_url = original

    def test_admin_requires_secret_and_cross_site_requests_are_blocked(self):
        status, body = self.request('POST', '/api/admin', json.dumps(dict(action='responses', secret='wrong')), {'Content-Type':'application/json'})
        self.assertEqual(status, 403)
        self.assertNotIn('ada@example.com', body)
        self.assertEqual(self.request('POST', '/api/respond', urlencode(RESPONSE), {'Content-Type':'application/x-www-form-urlencoded', 'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/', headers={'Host':'evil.example'})[0], 403)

    def test_pages_cors_preflight_and_public_reads_keep_writes_authenticated(self):
        origin = 'https://d3group.github.io'
        original = self.server.d3_app.config['site_url']
        self.server.d3_app.config['site_url'] = origin + '/d3seminar/'
        try:
            headers = {'Origin':origin, 'Access-Control-Request-Method':'POST',
                       'Access-Control-Request-Headers':'Content-Type, X-Invitation-Token, X-Event-Id, X-Material-Kind, X-File-Name, X-Presentation-Version'}
            connection = http.client.HTTPConnection('127.0.0.1', self.port)
            connection.request('OPTIONS', '/api/presentation-upload', headers=headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 204)
            self.assertEqual(response.getheader('Access-Control-Allow-Origin'), origin)
            self.assertIn('Origin', response.getheader('Vary'))
            self.assertIsNone(response.getheader('Access-Control-Allow-Credentials'))
            response.read(); connection.close()
            for blocked in ('https://evil.example', 'https://d3group.github.io.evil.example', 'null'):
                self.assertEqual(self.request('OPTIONS', '/api/respond', headers={**headers, 'Origin':blocked})[0], 403)
            self.assertEqual(self.request('OPTIONS', '/api/respond', headers={**headers, 'Access-Control-Request-Method':'DELETE'})[0], 403)
            self.assertEqual(self.request('OPTIONS', '/api/respond', headers={**headers, 'Access-Control-Request-Headers':'X-Unknown'})[0], 403)
            self.assertEqual(self.request('POST', '/api/public-event', '{}', {'Origin':origin, 'Content-Type':'application/json'})[0], 200)
            self.assertEqual(self.request('POST', '/api/respond', 'answer:'+SLOT+'=available', {'Origin':origin, 'Content-Type':'application/x-www-form-urlencoded'})[0], 400)
            self.assertEqual(self.request('POST', '/api/admin', '{"action":"responses"}', {'Origin':origin, 'Content-Type':'application/json'})[0], 403)
        finally:
            self.server.d3_app.config['site_url'] = original

    def test_individual_calendar_votes_require_an_invitation(self):
        headers = {'Content-Type':'application/json'}
        for request in (dict(include_votes=True), dict(token='X'*43, include_votes=True),
                        dict(token=TOKEN, event_id='seminar-3', include_votes=True)):
            status, body = self.request('POST', '/api/invitation', json.dumps(request), headers)
            self.assertEqual(status, 400)
            self.assertNotIn('participants', body)
            self.assertNotIn('Ada', body)
        self.assertEqual(self.request('GET', '/api/invitation?include_votes=true')[0], 404)

    def test_public_event_never_grants_write_access_or_individual_responses(self):
        headers = {'Content-Type':'application/json'}
        with self.store.connect() as connection:
            before = list(connection.iterdump())
        for token in (None, 'X'*43, TOKEN):
            status, body = self.request('POST', '/api/public-event', json.dumps(dict(token=token, include_votes=True, include_presentations=True, action='finalize')), headers)
            self.assertEqual(status, 200)
            public = json.loads(body)
            self.assertEqual(set(public), {'ok', 'poll', 'final_event', 'totals', 'presentations'})
            self.assertNotIn('Ada', body)
            self.assertEqual(set(public['totals'][SLOT]), {'available', 'unavailable', 'unknown'})
        for token in ('', 'X'*43):
            status, _ = self.request('POST', '/api/respond', urlencode(dict(RESPONSE, token=token)), {'Content-Type':'application/x-www-form-urlencoded'})
            self.assertEqual(status, 400)
            for remove in (False, True):
                status, _ = self.request('POST', '/api/presentation', json.dumps(dict(token=token, remove=remove, presentation=dict(title='Unauthorized'))), headers)
                self.assertEqual(status, 400)
            status, _ = self.request('POST', '/api/presentation-upload', b'%PDF-1.4', {'X-Event-Id':'seminar-1', 'X-Invitation-Token':token, 'X-Material-Kind':'slides', 'X-File-Name':'talk.pdf'})
            self.assertEqual(status, 400)
            status, _ = self.request('POST', '/api/admin', json.dumps(dict(action='finalize', slot_id=SLOT, secret=token)), headers)
            self.assertEqual(status, 403)
        with self.store.connect() as connection:
            self.assertEqual(list(connection.iterdump()), before)

    def test_real_form_post_writes_sqlite_before_success_receipt(self):
        status, body = self.request('POST', '/api/respond', urlencode(RESPONSE), {'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(status, 200)
        self.assertIn('Response saved.', body)
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {SLOT:'available'})

    def test_fetch_save_returns_committed_answers_and_rejects_stale_forms_as_json(self):
        headers = {'Content-Type':'application/x-www-form-urlencoded', 'Accept':'application/json'}
        status, body = self.request('POST', '/api/respond', urlencode(RESPONSE), headers)
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertTrue(saved['ok'])
        self.assertEqual(saved['answers'], self.store.invitation(TOKEN)['answers'])
        self.assertTrue(saved['updated_at'])
        self.assertNotIn('token_hash', saved)
        status, body = self.request('POST', '/api/respond', urlencode(dict(RESPONSE, revision='0'*64)), headers)
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(body)['ok'])
        self.assertIn('changed', json.loads(body)['error'])
        self.assertEqual(self.store.invitation(TOKEN)['answers'], saved['answers'])

    def test_link_scans_and_get_requests_do_not_submit(self):
        self.assertEqual(self.request('GET', '/api/respond?' + urlencode(RESPONSE))[0], 404)

    def test_calendar_is_unavailable_before_finalization_or_without_token(self):
        for token in (TOKEN, 'X' * 43):
            status, body = self.request('POST', '/api/calendar', json.dumps(dict(token=token)), {'Content-Type':'application/json'})
            self.assertEqual(status, 400)
            self.assertNotIn('BEGIN:VCALENDAR', body)


if __name__ == '__main__':
    unittest.main()
