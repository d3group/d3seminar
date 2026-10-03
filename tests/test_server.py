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
from server import Store, create_server, receipt

TOKEN = 'A' * 43
POLL = json.loads((seminar.ROOT / 'site/config.json').read_text())
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
                             token_hash=seminar.digest(token), poll=self.poll, revision=self.revision) for i, token in enumerate(self.tokens)]
        self.store.sync(self.invites)

    def answer(self, index, value='available', all_slots=False):
        slots = self.poll['slots'] if all_slots else self.poll['slots'][:1]
        return self.store.respond(dict(token=self.tokens[index], revision=self.revision, **{'answer:' + s['id']:value for s in slots}))

    def choose(self, action='finalize'):
        return self.store.select(action, SLOT, self.store.dashboard()['snapshot'])

    def test_nineteen_of_twenty_cannot_finalize_or_request_conditional_confirmation(self):
        for i in range(19):
            self.answer(i)
        slot = self.store.dashboard()['slots'][0]
        self.assertEqual((slot['available'], slot['unknown'], slot['unavailable']), (19, 1, 0))
        for action in ('finalize', 'propose'):
            with self.assertRaises(ValueError):
                self.choose(action)

    def test_conditional_answer_must_be_explicitly_changed_before_finalizing(self):
        for i in range(20):
            self.answer(i, 'if_needed' if i == 19 else 'available')
        with self.assertRaisesRegex(ValueError, 'Everyone'):
            self.choose()
        state = self.choose('propose')
        self.assertEqual(self.store.invitation(self.tokens[19])['state']['proposed_slot_id'], SLOT)
        self.assertTrue(state['proposal_id'])
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
        self.assertFalse(any(s['ready'] or s['can_propose'] for s in dashboard['slots']))
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

    def test_declining_proposed_time_cancels_reconfirmation(self):
        for i in range(20):
            self.answer(i, 'if_needed' if i == 19 else 'available')
        self.choose('propose')
        self.answer(19, 'unavailable')
        self.assertEqual(self.store.dashboard()['state'], {})


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / 'responses.sqlite3')
        cls.store.sync([INVITE])
        config = json.loads((seminar.ROOT / 'site/config.json').read_text())
        cls.server = create_server(0, store=cls.store, config=config, admin_secret='secret' * 8)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read().decode()
        connection.close()
        return result

    def test_static_server_never_exposes_private_files_or_source(self):
        for path in ['/.private/settings.json', '/../.private/settings.json', '/scripts/server.py', '/.git/config']:
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.request('GET', '/')[0], 200)

    def test_admin_requires_secret_and_cross_site_requests_are_blocked(self):
        status, body = self.request('POST', '/api/admin', json.dumps(dict(action='responses', secret='wrong')), {'Content-Type':'application/json'})
        self.assertEqual(status, 403)
        self.assertNotIn('ada@example.com', body)
        self.assertEqual(self.request('POST', '/api/respond', urlencode(RESPONSE), {'Content-Type':'application/x-www-form-urlencoded', 'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/', headers={'Host':'evil.example'})[0], 403)

    def test_real_form_post_writes_sqlite_before_success_receipt(self):
        status, body = self.request('POST', '/api/respond', urlencode(RESPONSE), {'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(status, 200)
        self.assertIn('Response saved.', body)
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {SLOT:'available'})

    def test_link_scans_and_get_requests_do_not_submit(self):
        self.assertEqual(self.request('GET', '/api/respond?' + urlencode(RESPONSE))[0], 404)

    def test_calendar_is_unavailable_before_finalization_or_without_token(self):
        for token in (TOKEN, 'X' * 43):
            status, body = self.request('POST', '/api/calendar', json.dumps(dict(token=token)), {'Content-Type':'application/json'})
            self.assertEqual(status, 400)
            self.assertNotIn('BEGIN:VCALENDAR', body)


if __name__ == '__main__':
    unittest.main()
