import fcntl
import json
import hashlib
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.destination = self.root / 'data', self.root / 'backups'
        self.source.mkdir()
        self.now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        (self.source / 'command.lock').touch()
        (self.source / 'settings.json').write_text('{"admin_secret":"test-only"}')
        (self.source / 'participants.csv').write_text('name,email,role\nTest,test@example.com,student\n')
        (self.source / 'config.json').write_text(json.dumps(dict(events=[dict(id='seminar-1'), dict(id='onsite')])))
        self.writers = []
        for relative in ('', 'events/onsite'):
            directory = self.source / relative
            directory.mkdir(parents=True, exist_ok=True)
            invite = dict(id='a'*24, token='T'*43, revision='r')
            (directory / 'invitations.json').write_text(json.dumps([invite]))
            connection = sqlite3.connect(directory / 'responses.sqlite3')
            self.writers.append(connection)
            self.addCleanup(connection.close)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE invitations (data TEXT)')
            connection.execute('CREATE TABLE materials (content BLOB)')
            connection.execute('INSERT INTO invitations VALUES (?)', (json.dumps(dict(invite, active=True, token_hash=hashlib.sha256(invite['token'].encode()).hexdigest())),))
            connection.execute('INSERT INTO materials VALUES (?)', (b'%PDF-test-talk',))
            connection.commit()

    def make(self, day=0, keep=30):
        return backup.create_backup(self.source, self.destination, keep=keep, now=self.now + timedelta(days=day))

    def test_live_sqlite_snapshot_archive_and_private_metadata_are_complete(self):
        self.writers[0].execute('INSERT INTO materials VALUES (?)', (b'uncommitted',))
        archive = self.make()
        manifest = backup.verify_archive(archive)
        self.assertEqual(manifest['databases'], ['data/events/onsite/responses.sqlite3', 'data/responses.sqlite3'])
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        with tarfile.open(archive) as bundle:
            names = bundle.getnames()
            self.assertFalse(any(name.endswith(('-wal', '-shm', '.lock')) for name in names))
            self.assertEqual(json.load(bundle.extractfile('data/settings.json'))['admin_secret'], 'test-only')
            restored = self.root / 'restored.sqlite3'
            restored.write_bytes(bundle.extractfile('data/responses.sqlite3').read())
        with closing(sqlite3.connect(restored)) as connection:
            self.assertEqual(connection.execute('SELECT content FROM materials').fetchall(), [(b'%PDF-test-talk',)])
        self.writers[0].rollback()
        self.assertEqual(backup.read_status(self.destination)['archive'], archive.name)
        self.assertTrue(backup.healthy(self.destination, self.now + timedelta(hours=29)))
        self.assertFalse(backup.healthy(self.destination, self.now + timedelta(hours=31)))

    def test_retention_only_after_success_and_only_owned_archives(self):
        first, second = self.make(), self.make(1)
        unrelated = self.destination / 'manual-backup.tar.gz'
        unrelated.write_bytes(b'Leave this alone')
        third = self.make(2, keep=2)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists() and third.exists() and unrelated.exists())
        (self.source / 'invitations.json').write_text('[]')
        with self.assertRaisesRegex(ValueError, 'synchronized'):
            self.make(3, keep=1)
        self.assertTrue(second.exists() and third.exists())
        self.assertEqual(backup.read_status(self.destination)['archive'], third.name)
        self.assertTrue(backup.read_status(self.destination)['error'])
        self.assertFalse(backup.healthy(self.destination, self.now + timedelta(days=3)))

    def test_busy_edits_incomplete_sources_and_unsafe_paths_leave_backups_intact(self):
        archive = self.make()
        with (self.source / 'command.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.make(1)
        (self.source / 'pending-edit.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'pending'):
            self.make(1)
        (self.source / 'pending-edit.json').unlink()
        (self.source / 'outside').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'link'):
            self.make(1)
        with self.assertRaises(ValueError):
            backup.create_backup(self.source, self.source / 'backups')
        with self.assertRaises(ValueError):
            backup.create_backup(self.source, self.destination, keep=0)
        self.assertTrue(archive.exists())

    def test_corrupt_archive_is_rejected(self):
        archive = self.make()
        contents = archive.read_bytes()
        archive.write_bytes(contents[:len(contents)//2])
        with self.assertRaises((ValueError, EOFError, tarfile.TarError)):
            backup.verify_archive(archive)

    def test_daily_schedule_catches_up_and_tracks_berlin_dst(self):
        self.assertTrue(backup.due({}, datetime(2026, 10, 6, 0, tzinfo=timezone.utc)))
        state = dict(last_success='2026-10-05T12:00:00+00:00')
        self.assertFalse(backup.due(state, datetime(2026, 10, 6, 0, 59, tzinfo=timezone.utc)))
        self.assertTrue(backup.due(state, datetime(2026, 10, 6, 1, tzinfo=timezone.utc)))
        self.assertTrue(backup.due(state, datetime(2026, 10, 8, 15, tzinfo=timezone.utc)))
        state = dict(last_success='2026-10-24T01:00:00+00:00')
        self.assertFalse(backup.due(state, datetime(2026, 10, 25, 1, 59, tzinfo=timezone.utc)))
        self.assertTrue(backup.due(state, datetime(2026, 10, 25, 2, tzinfo=timezone.utc)))


if __name__ == '__main__':
    unittest.main()
