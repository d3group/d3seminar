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
EVENT = dict(campaign_id='demo', end='2026-10-09T14:30:00+02:00', location='Test room',
             slot_id='one', start='2026-10-09T14:00:00+02:00', timezone='Europe/Berlin', title='D3 seminar')
INVITE = dict(id='a' * 24, name='Ada', email='ada@example.com', token_hash=seminar.digest(TOKEN),
              revision=seminar.event_revision(EVENT), event=EVENT, site_url='http://127.0.0.1:8765/')
RESPONSE = dict(token=TOKEN, revision=INVITE['revision'], response='confirmed', comment='')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'responses.sqlite3')
        self.store.sync([INVITE])

    def test_response_persists_across_store_restart_and_updates_one_row(self):
        saved = self.store.respond(RESPONSE)
        self.store = Store(self.store.database)
        self.assertEqual(self.store.invitation(TOKEN)['response'], 'confirmed')
        self.assertEqual(self.store.respond(RESPONSE)['updated_at'], saved['updated_at'])
        self.store.respond(dict(RESPONSE, response='change_requested', comment='Monday morning.'))
        self.assertEqual(len(self.store.responses()), 1)
        self.assertEqual(self.store.invitation(TOKEN)['comment'], 'Monday morning.')

    def test_bad_tokens_statuses_notes_and_stale_revisions_are_rejected(self):
        for bad in [dict(token='B' * 43), dict(token=''), dict(response='admin'), dict(comment='x'*1001),
                    dict(response='change_requested', comment='  '), dict(revision='0'*64)]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.respond(dict(RESPONSE, **bad))
        self.assertEqual(self.store.invitation(TOKEN)['response'], 'pending')

    def test_sync_preserves_unchanged_response_and_revokes_removed_participant(self):
        self.store.respond(RESPONSE)
        self.store.sync([INVITE])
        self.assertEqual(self.store.invitation(TOKEN)['response'], 'confirmed')
        self.store.sync([dict(INVITE, id='b'*24, email='other@example.com', token_hash=seminar.digest('B'*43))])
        with self.assertRaises(ValueError):
            self.store.invitation(TOKEN)
        self.assertEqual(len(self.store.responses()), 1)

    def test_new_token_invalidates_old_link_and_resets_reply(self):
        self.store.respond(RESPONSE)
        self.store.sync([dict(INVITE, token_hash=seminar.digest('B'*43))])
        with self.assertRaises(ValueError):
            self.store.respond(RESPONSE)
        self.assertEqual(self.store.invitation('B'*43)['response'], 'pending')

    def test_invalid_sync_is_atomic(self):
        for records in [[INVITE, INVITE], [dict(INVITE, revision='0'*64)], [], [None]]:
            with self.assertRaises(ValueError):
                self.store.sync(records)
        self.assertEqual(len(self.store.responses()), 1)

    def test_private_lookup_does_not_return_email_or_token_hash(self):
        response = self.store.invitation(TOKEN)
        self.assertNotIn('email', response)
        self.assertNotIn('token_hash', response)

    def test_receipt_escapes_untrusted_names(self):
        self.store.sync([dict(INVITE, name='<img src=x onerror=alert(1)>')])
        body = receipt(self.store.respond(RESPONSE), TOKEN)
        self.assertIn('&lt;img', body)
        self.assertNotIn('<img src=x', body)


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
        self.assertEqual(self.store.invitation(TOKEN)['response'], 'confirmed')

    def test_link_scans_and_get_requests_do_not_submit(self):
        self.assertEqual(self.request('GET', '/api/respond?' + urlencode(RESPONSE))[0], 404)


if __name__ == '__main__':
    unittest.main()
