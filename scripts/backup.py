#!/usr/bin/env python3
"""Private SQLite snapshots, verified archives and daily retention."""
import argparse, fcntl, hashlib, json, os, re, shutil, sqlite3, tarfile, tempfile, time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = re.compile(r'd3seminar-\d{8}T\d{12}Z\.tar\.gz')


def utc_now():
    return datetime.now(timezone.utc)


def read_status(directory):
    try:
        return json.loads((directory / 'status.json').read_text())
    except FileNotFoundError:
        return {}
    except ValueError:
        return dict(error='Backup status could not be read.')


def write_status(directory, **values):
    status = dict(read_status(directory), **values)
    with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.status-', delete=False) as stream:
        json.dump(status, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    Path(stream.name).replace(directory / 'status.json')


@contextmanager
def lock_file(path, create=False):
    with path.open('a' if create else 'rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def database_copy(source, destination):
    deadline = time.monotonic() + 300
    def progress(*_):
        if time.monotonic() > deadline:
            raise TimeoutError('The database remained busy for too long.')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst, pages=256, progress=progress)
            dst.execute('PRAGMA journal_mode=DELETE')
    destination.chmod(0o600)


def validate_snapshot(directory):
    for name in ('settings.json', 'config.json', 'participants.csv', 'invitations.json'):
        if not (directory / name).is_file():
            raise ValueError('The data directory is not fully initialized: missing ' + name)
    if (directory / 'pending-edit.json').exists():
        raise ValueError('An organiser edit is pending. Restart the backend to recover it first.')
    config = json.loads((directory / 'config.json').read_text())
    for event in config['events']:
        if not re.fullmatch(r'[a-z0-9-]+', event['id']):
            raise ValueError('Invalid event identifier in the configuration.')
        event_dir = directory if event['id'] == 'seminar-1' else directory / 'events' / event['id']
        if not (event_dir / 'responses.sqlite3').is_file() or not (event_dir / 'invitations.json').is_file():
            raise ValueError('All events must be initialized and synchronized before backup.')
        invites = json.loads((event_dir / 'invitations.json').read_text())
        with closing(sqlite3.connect((event_dir / 'responses.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
            records = [json.loads(row[0]) for row in connection.execute('SELECT data FROM invitations')]
        expected = sorted((r['id'], r['revision'], hashlib.sha256(r['token'].encode()).hexdigest()) for r in invites)
        actual = sorted((r['id'], r['revision'], r['token_hash']) for r in records if r['active'])
        if not expected or expected != actual:
            raise ValueError('Invitations and responses must be synchronized before backup.')


def verify_archive(archive):
    with tempfile.TemporaryDirectory(prefix='d3-backup-verify-') as temporary, tarfile.open(archive, 'r:gz') as bundle:
        directory = Path(temporary)
        members = bundle.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or '..' in path.parts or str(path) != member.name:
                raise ValueError('Unsafe archive entry: only regular files are allowed.')
        names = [m.name for m in members]
        if len(names) != len(set(names)) or 'manifest.json' not in names:
            raise ValueError('Invalid backup manifest or duplicate archive entries.')
        manifest = json.load(bundle.extractfile('manifest.json'))
        if manifest.get('version') != 1 or set(names) != {'manifest.json', *manifest['files']}:
            raise ValueError('The backup manifest does not match the archive.')
        databases = []
        for member in members:
            if member.name == 'manifest.json':
                continue
            if not member.name.startswith('data/'):
                raise ValueError('Unexpected file outside the backup data directory.')
            output = directory / member.name
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with output.open('xb') as stream, bundle.extractfile(member) as source:
                shutil.copyfileobj(source, stream)
            output.chmod(0o600)
            if digest(output) != manifest['files'][member.name]:
                raise ValueError('Backup checksum verification failed.')
            if output.suffix == '.json':
                json.loads(output.read_text())
            if output.suffix == '.sqlite3':
                with closing(sqlite3.connect(output.as_uri() + '?mode=ro', uri=True)) as connection:
                    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise ValueError('Backup database integrity check failed.')
                databases.append(member.name)
        if sorted(databases) != manifest['databases']:
            raise ValueError('The backup database list does not match the manifest.')
        validate_snapshot(directory / 'data')
        return manifest


def create_backup(source, destination, keep=30, now=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if keep < 1 or source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Use a separate backup directory and retain at least one backup.')
    if not source.is_dir():
        raise ValueError('The source data directory does not exist.')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    now = now or utc_now()
    with lock_file(destination / 'backup.lock', create=True):
        try:
            with tempfile.TemporaryDirectory(dir=destination, prefix='.working-') as temporary:
                stage = Path(temporary)
                snapshot = stage / 'data'
                snapshot.mkdir(mode=0o700)
                with lock_file(source / 'command.lock'):
                    if (source / 'pending-edit.json').exists():
                        raise ValueError('An organiser edit is pending. Restart the backend first.')
                    for file in sorted(source.rglob('*')):
                        if file.is_symlink():
                            raise ValueError('Symbolic links are not allowed in the backup source.')
                        if file.is_dir():
                            continue
                        if not file.is_file():
                            raise ValueError('The source contains a non-regular file.')
                        if file.name.endswith(('.lock', '.tmp', '-wal', '-shm', '-journal')) or file.name == '.DS_Store':
                            continue
                        output = snapshot / file.relative_to(source)
                        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        if file.suffix == '.sqlite3':
                            database_copy(file, output)
                        else:
                            shutil.copyfile(file, output)
                            output.chmod(0o600)
                    validate_snapshot(snapshot)
                files = {p.relative_to(stage).as_posix():digest(p) for p in sorted(snapshot.rglob('*')) if p.is_file()}
                manifest = dict(version=1, created_at=now.isoformat(), files=files,
                                databases=sorted(p for p in files if p.endswith('.sqlite3')))
                manifest_file = stage / 'manifest.json'
                manifest_file.write_text(json.dumps(manifest, indent=2) + '\n')
                manifest_file.chmod(0o600)
                pending = stage / 'archive.tar.gz'
                with pending.open('xb') as stream:
                    with tarfile.open(fileobj=stream, mode='w:gz') as bundle:
                        for name in ('manifest.json', *files):
                            file = stage / name
                            info = bundle.gettarinfo(file, arcname=name)
                            info.mode, info.uid, info.gid, info.uname, info.gname = 0o600, 0, 0, '', ''
                            with file.open('rb') as contents:
                                bundle.addfile(info, contents)
                    stream.flush()
                    os.fsync(stream.fileno())
                pending.chmod(0o600)
                verify_archive(pending)
                archive = destination / ('d3seminar-' + now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.tar.gz')
                if archive.exists():
                    raise ValueError('A backup already exists with this timestamp.')
                pending.rename(archive)
            older = sorted((p for p in destination.iterdir() if ARCHIVE_NAME.fullmatch(p.name) and p.is_file()
                            and not p.is_symlink() and p != archive), reverse=True)
            for expired in older[keep-1:]:
                expired.unlink()
            write_status(destination, last_attempt=now.isoformat(), last_success=now.isoformat(), archive=archive.name,
                         databases=len(manifest['databases']), files=len(files), bytes=archive.stat().st_size, keep=keep, error=None)
            print(f'Backup verified: {archive.name} ({len(manifest["databases"])} databases; retaining {keep}).', flush=True)
            return archive
        except BlockingIOError:
            raise
        except Exception as error:
            write_status(destination, last_attempt=now.isoformat(), error=str(error))
            raise


def healthy(directory, now=None):
    status = read_status(directory)
    if status.get('error') or not status.get('last_success') or not status.get('archive'):
        return False
    age = (now or utc_now()) - datetime.fromisoformat(status['last_success'])
    return timedelta(0) <= age <= timedelta(hours=30) and (directory / status['archive']).is_file()


def due(status, now, hour=3, zone='Europe/Berlin'):
    local = now.astimezone(ZoneInfo(zone))
    scheduled = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if local < scheduled:
        scheduled -= timedelta(days=1)
    return not status.get('last_success') or datetime.fromisoformat(status['last_success']) < scheduled.astimezone(timezone.utc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(os.environ.get('D3_DATA_DIR', ROOT / 'data')))
    parser.add_argument('--destination', type=Path, default=Path(os.environ.get('D3_BACKUP_DIR', ROOT / 'backups')))
    parser.add_argument('--keep', type=int, default=int(os.environ.get('D3_BACKUP_KEEP', '30')))
    parser.add_argument('--hour', type=int, choices=range(24), default=int(os.environ.get('D3_BACKUP_HOUR', '3')))
    parser.add_argument('--timezone', default=os.environ.get('D3_BACKUP_TIMEZONE', 'Europe/Berlin'))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('create')
    commands.add_parser('schedule')
    commands.add_parser('status')
    commands.add_parser('verify').add_argument('archive', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.keep < 1 or not 0 <= args.hour <= 23:
        parser.error('Retention must be positive and the hour must be between 0 and 23.')
    ZoneInfo(args.timezone)
    if args.command == 'verify':
        manifest = verify_archive(args.archive)
        print(f'Backup verified: {len(manifest["databases"])} databases and {len(manifest["files"])} files.')
    elif args.command == 'status':
        print(json.dumps(read_status(args.destination), indent=2))
        raise SystemExit(0 if healthy(args.destination) else 1)
    elif args.command == 'create':
        create_backup(args.source, args.destination, args.keep)
    else:
        print(f'Daily backups at {args.hour:02}:00 {args.timezone}; keep {args.keep} successful archives.', flush=True)
        retry_at = 0
        while True:
            now = utc_now()
            status = read_status(args.destination)
            missing = not (args.destination / status.get('archive', '.missing')).is_file()
            if time.monotonic() >= retry_at and (missing or status.get('error') or due(status, now, args.hour, args.timezone)):
                try:
                    create_backup(args.source, args.destination, args.keep)
                except Exception as error:
                    print(f'Backup deferred or failed ({type(error).__name__}); retrying in five minutes.', flush=True)
                retry_at = time.monotonic() + 300
            time.sleep(60)


if __name__ == '__main__':
    main()
