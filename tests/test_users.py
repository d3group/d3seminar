import csv, json, sqlite3, sys, tempfile, unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import users


class UsersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.csv = self.root / 'attendees.csv'
        self.database = self.root / 'responses.sqlite3'
        self.invite = dict(id='a'*24, name='Ada', email='ada@example.org', role='student', token='A'*43)
        (self.root / 'invitations.json').write_text(json.dumps([self.invite]))
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE invitations (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')
            db.execute('INSERT INTO invitations VALUES (?, ?, ?)', (self.invite['id'], 'b'*64, json.dumps(dict(self.invite, answers={'one':'available'}))))
        self.write([
            dict(name='Ada', email='ada@example.org', role='PhD', role_status='confirmed'),
            dict(name='Bea', email='bea@example.org', role='Professor', role_status='confirmed'),
            dict(name='Unconfirmed address', email='', role='Research assistant', role_status='inferred'),
        ])

    def write(self, rows):
        with self.csv.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=users.FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def read(self):
        with self.csv.open(newline='') as stream:
            return list(csv.DictReader(stream))

    def records(self, table='users'):
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]

    def test_staging_matches_csv_preserves_existing_access_and_is_idempotent(self):
        before = self.records('invitations')
        original_invites = (self.root / 'invitations.json').read_bytes()
        self.assertEqual(users.stage(self.csv, self.database), 3)
        rows = self.read()
        self.assertEqual(rows[0]['id'], self.invite['id'])
        self.assertEqual(rows[0]['token'], self.invite['token'])
        self.assertEqual(len({row['token'] for row in rows}), 3)
        for row in rows:
            self.assertRegex(row['id'], r'^[a-f0-9]{24}$')
            self.assertRegex(row['token'], r'^[A-Za-z0-9_-]{43}$')
        expected = sorted([dict(row, email=row['email'] or None, is_organizer=0, is_archived=0) for row in rows], key=lambda row:row['id'])
        self.assertEqual(self.records(), expected)
        self.assertEqual(self.records('invitations'), before)
        self.assertEqual((self.root / 'invitations.json').read_bytes(), original_invites)
        users.stage(self.csv, self.database)
        self.assertEqual(self.read(), rows)
        self.assertEqual(self.records(), expected)
        self.assertEqual(self.csv.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)

    def test_finishing_an_email_and_editing_a_name_preserve_identity(self):
        users.stage(self.csv, self.database)
        rows = self.read()
        identity = (rows[2]['id'], rows[2]['token'])
        rows[2].update(email='cora@example.org', name='Cora')
        self.write(rows)
        users.stage(self.csv, self.database)
        updated = self.read()[2]
        self.assertEqual((updated['id'], updated['token']), identity)
        self.assertEqual(updated['email'], 'cora@example.org')

    def test_organizer_handover_survives_roster_import_and_rejects_unknown_users(self):
        users.stage(self.csv, self.database)
        original = self.records()
        users.set_organizers(self.database, ['ada@example.org'])
        users.stage(self.csv, self.database)
        current = self.records()
        self.assertEqual([row['email'] for row in current if row['is_organizer']], ['ada@example.org'])
        self.assertEqual([{key:value for key,value in row.items() if key != 'is_organizer'} for row in current],
                         [{key:value for key,value in row.items() if key != 'is_organizer'} for row in original])
        for addresses in ([], ['unknown@example.org']):
            with self.assertRaises(ValueError):
                users.set_organizers(self.database, addresses)
            self.assertEqual(self.records(), current)
        users.set_organizers(self.database, ['bea@example.org'])
        self.assertEqual([row['email'] for row in self.records() if row['is_organizer']], ['bea@example.org'])

    def test_invalid_roles_duplicate_emails_and_token_rotation_roll_back(self):
        users.stage(self.csv, self.database)
        original = self.read()
        records = self.records()
        for change in (dict(role='admin'), dict(token='B'*43), dict(id='not-an-id'),
                       dict(email='bea@example.org'), dict(role_status='probably')):
            with self.subTest(change=change):
                rows = [dict(row) for row in original]
                rows[0].update(change)
                self.write(rows)
                before = self.csv.read_bytes()
                with self.assertRaises(ValueError):
                    users.stage(self.csv, self.database)
                self.assertEqual(self.records(), records)
                self.assertEqual(self.csv.read_bytes(), before)

    def test_failed_csv_export_can_be_retried_without_losing_generated_tokens(self):
        before = self.csv.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('Simulated export failure')):
            with self.assertRaises(OSError):
                users.stage(self.csv, self.database)
        saved = self.records()
        self.assertEqual(self.csv.read_bytes(), before)
        users.stage(self.csv, self.database)
        self.assertEqual(self.records(), saved)
        self.assertEqual({row['token'] for row in self.read()}, {row['token'] for row in saved})

    def test_conflicting_legacy_event_tokens_are_rejected(self):
        event = self.root / 'events/onsite'
        event.mkdir(parents=True)
        (event / 'invitations.json').write_text(json.dumps([dict(self.invite, token='B'*43)]))
        before = self.csv.read_bytes()
        with self.assertRaisesRegex(ValueError, 'conflicting personal tokens'):
            users.stage(self.csv, self.database)
        self.assertEqual(self.csv.read_bytes(), before)

    def test_missing_email_names_must_be_unambiguous_for_retry(self):
        rows = self.read()
        self.write([*rows, dict(rows[-1])])
        with self.assertRaisesRegex(ValueError, 'duplicate name'):
            users.stage(self.csv, self.database)

    def test_public_site_and_symlinks_cannot_receive_tokens(self):
        site = self.root / 'site'
        site.mkdir()
        public = site / 'attendees.csv'
        public.write_bytes(self.csv.read_bytes())
        with patch.object(users.seminar, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'outside the public'):
            users.stage(public, self.database)
        link = self.root / 'linked.csv'
        link.symlink_to(self.csv)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            users.stage(link, self.database)


if __name__ == '__main__':
    unittest.main()
