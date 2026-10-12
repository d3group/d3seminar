import copy, json, sqlite3, sys, tempfile, unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import database
from models import Base, PollState
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

LEGACY_SCHEMA = '''
CREATE TABLE invitations (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
CREATE TABLE poll_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
CREATE TABLE history (saved_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE presentations (participant_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE presentation_files (id TEXT PRIMARY KEY, participant_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL, content BLOB NOT NULL, UNIQUE(participant_id, kind));
'''
USERS_SCHEMA = '''CREATE TABLE users (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
email TEXT UNIQUE COLLATE NOCASE, role TEXT NOT NULL, token TEXT NOT NULL UNIQUE, role_status TEXT NOT NULL)'''


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'responses.sqlite3'

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='alembic_version'")]
            return {name:db.execute('SELECT * FROM ' + name).fetchall() for name in names}

    def assert_current(self, engine):
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            self.assertEqual(context.get_current_revision(), '0005_mail_drafts')
            self.assertEqual(compare_metadata(context, Base.metadata), [])

    def test_adopts_legacy_databases_without_rewriting_any_rows(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(LEGACY_SCHEMA + USERS_SCHEMA)
            db.execute('INSERT INTO invitations VALUES (?, ?, ?)', ('a'*24, 'b'*64, '{"name":"Änne", "answers":{"one":"available"}}'))
            db.execute('INSERT INTO poll_state VALUES (1, ?)', ('{"programme":{"topic":"Keep this"}}',))
            db.execute('INSERT INTO history VALUES (?, ?)', ('old', '{"note":"Keep old responses"}'))
            db.execute('INSERT INTO presentations VALUES (?, ?)', ('a'*24, '{"title":"Provisional", "visibility":"internal"}'))
            db.execute('INSERT INTO presentation_files VALUES (?, ?, ?, ?, ?, ?)', ('c'*48, 'a'*24, 'slides', 'talk.pdf', 'application/pdf', b'%PDF-1.4\x00\xff'))
            db.execute('INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)', ('a'*24, 'Änne', 'anne@example.org', 'PhD', 'A'*43, 'confirmed'))
        before = self.snapshot()
        before['users'] = [(*row, 0, 0) for row in before['users']]
        before['mail_batches'] = []
        before['mail_drafts'] = []
        engine = database.make_engine(self.path)
        self.addCleanup(engine.dispose)
        database.upgrade(engine)
        database.upgrade(engine)
        self.assertEqual(self.snapshot(), before)
        self.assert_current(engine)

    def test_initializes_fresh_and_partial_legacy_schemas(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                path = self.path.with_name(f'{legacy}.sqlite3')
                if legacy:
                    with closing(sqlite3.connect(path)) as db:
                        db.executescript(LEGACY_SCHEMA)
                engine = database.make_engine(path)
                self.addCleanup(engine.dispose)
                database.upgrade(engine)
                self.assert_current(engine)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_unknown_legacy_schema_rolls_back_without_stamping_or_losing_data(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE users (id TEXT PRIMARY KEY, unexpected TEXT)')
            db.execute('INSERT INTO users VALUES (?, ?)', ('keep', 'private'))
        before = self.snapshot()
        engine = database.make_engine(self.path)
        self.addCleanup(engine.dispose)
        with self.assertRaisesRegex(ValueError, 'schema'):
            database.upgrade(engine)
        self.assertEqual(self.snapshot(), before)
        with engine.connect() as connection:
            self.assertIsNone(MigrationContext.configure(connection).get_current_revision())

    def test_concurrent_startup_and_updates_preserve_transactions(self):
        def initialize(_):
            engine = database.make_engine(self.path)
            try:
                database.upgrade(engine)
            finally:
                engine.dispose()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(initialize, range(8)))
        engine = database.make_engine(self.path)
        self.addCleanup(engine.dispose)
        with database.transaction(engine, write=True) as session:
            session.add(PollState(id=1, data={'count':0}))
        def increment(_):
            with database.transaction(engine, write=True) as session:
                state = session.get(PollState, 1)
                state.data = dict(count=state.data['count'] + 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(increment, range(20)))
        with self.assertRaises(RuntimeError):
            with database.transaction(engine, write=True) as session:
                session.get(PollState, 1).data = {'count':99}
                session.flush()
                raise RuntimeError('Rollback this update')
        with database.transaction(engine) as session:
            self.assertEqual(session.get(PollState, 1).data, {'count':20})

    def test_json_columns_save_nested_replacements_and_rollback_without_schema_changes(self):
        engine = database.make_engine(self.path)
        self.addCleanup(engine.dispose)
        database.upgrade(engine)
        original = {'programme':{'topic':'Änne’s research'}, 'answers':{'one':'available'}}
        with database.transaction(engine, write=True) as session:
            session.add(PollState(id=1, data=original))
        with database.transaction(engine, write=True) as session:
            row = session.get(PollState, 1)
            updated = copy.deepcopy(row.data)
            updated['answers']['two'] = 'unavailable'
            row.data = updated
        with self.assertRaises(RuntimeError), database.transaction(engine, write=True) as session:
            row = session.get(PollState, 1)
            row.data = dict(row.data, programme={'topic':'Discard this'})
            session.flush()
            raise RuntimeError('rollback')
        with database.transaction(engine) as session:
            self.assertEqual(session.get(PollState, 1).data, updated)
        with closing(sqlite3.connect(self.path)) as db:
            raw = db.execute('SELECT data FROM poll_state').fetchone()[0]
            self.assertEqual(json.loads(raw), updated)
        self.assert_current(engine)
