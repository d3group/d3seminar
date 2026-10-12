"""Exercise the two containers with disposable data. Build the images first."""

import json, os, sqlite3, subprocess, tempfile, time, tarfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
project = f'd3seminar-test-{os.getpid()}'
port = os.environ.get('D3_SMOKE_PORT', '18080')
base = f'http://localhost:{port}'
(root / 'test-results').mkdir(exist_ok=True)
data_directory = tempfile.TemporaryDirectory(prefix='docker-data-', dir=root / 'test-results')
backup_directory = tempfile.TemporaryDirectory(prefix='docker-backups-', dir=root / 'test-results')
restore_directory = tempfile.TemporaryDirectory(prefix='docker-restore-', dir=root / 'test-results')
env = dict(os.environ, D3_FRONTEND_PORT=port, D3_DATA_PATH=data_directory.name,
           D3_BACKUP_PATH=backup_directory.name, D3_UID=str(os.getuid()), D3_GID=str(os.getgid()))


def compose(*args):
    result = subprocess.run(['docker', 'compose', '-p', project, *args], cwd=root, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def cli(*args):
    return compose('exec', '-T', 'backend', 'python', 'scripts/seminar.py', *args)


def request(path, data=None, headers=None):
    request_headers = {'Origin':base}
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        request_headers['Content-Type'] = 'application/json'
    request_headers.update(headers or {})
    with urlopen(Request(base + path, data=data, headers=request_headers), timeout=10) as response:
        body = response.read()
        return json.loads(body) if response.headers.get_content_type() == 'application/json' else body


def ready():
    for _ in range(30):
        try:
            if request('/health')['ok']:
                return
        except (URLError, TimeoutError):
            pass
        time.sleep(1)
    raise RuntimeError('The frontend could not reach the backend.')


try:
    compose('run', '--rm', '--no-deps', 'backend', 'python', 'scripts/seminar.py', 'init',
            '--email', 'docker-test@example.com', '--name', 'Docker Test', '--role', 'student')
    compose('run', '--rm', '--no-deps', 'backend', 'python', 'scripts/seminar.py', '--event', 'all', 'prepare')
    compose('up', '-d', '--wait', '--wait-timeout', '60', '--no-build')
    cli('--event', 'all', 'sync')
    ready()
    assert len(list(Path(data_directory.name).rglob('responses.sqlite3'))) == 4, 'Databases must be visible in the host bind directory.'
    print('Both containers are healthy; all four events are initialized.', flush=True)

    config = request('/config.json')
    assert config['site_url'] == base + '/'
    assert config['backend_url'] == base + '/api/respond'
    for path in ['/', '/agenda.html', '/admin', '/home.js', '/styles.css', '/assets/agenda-paper.svg']:
        assert request(path)
    for path in ['/.private/settings.json', '/vault/README.md', '/scripts/server.py']:
        try:
            request(path)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError('Private path was served: ' + path)

    token = parse_qs(urlparse(cli('home', '--print-url')).fragment)['token'][0]
    secret = parse_qs(urlparse(cli('admin', '--print-url')).fragment)['key'][0]
    invitation = request('/api/invitation', dict(token=token))
    slot = invitation['poll']['slots'][0]['id']
    data = dict(token=token, revision=invitation['revision'], response_version=invitation['response_version'], **{'answer:' + slot:'available'})
    request('/api/respond', urlencode(data).encode(), {'Content-Type':'application/x-www-form-urlencoded', 'Accept':'application/json'})
    with sqlite3.connect(f'file:{data_directory.name}/responses.sqlite3?mode=ro', uri=True) as connection:
        record = json.loads(connection.execute('SELECT data FROM invitations').fetchone()[0])
        assert record['answers'] == {slot:'available'}
    talk = dict(title='Container test talk', abstract='Private abstract.', visibility='public', presentation_minutes=20, questions_minutes=10)
    saved = request('/api/presentation', dict(token=token, presentation=talk))['presentation']
    pdf = b'%PDF-1.4\n' + b'0' * (1024 * 1024 + 1) + b'\n%%EOF'
    uploaded = request('/api/presentation-upload', pdf, {'Content-Type':'application/pdf', 'X-Invitation-Token':token,
        'X-Event-Id':'seminar-1', 'X-Material-Kind':'slides', 'X-File-Name':'test.pdf', 'X-Presentation-Version':saved['version']})['presentation']
    assert request('/api/public-presentations', {})['presentations'] == [{'name':'Docker Test'}]

    def admin(action, **data):
        return request('/api/admin', dict(action=action, event_id='seminar-1', secret=secret, **data))

    proposal = dict(deadline='2026-10-24T17:00', options=[dict(id=s['id'], start=s['start'][:16]) for s in invitation['poll']['slots']])
    preview = admin('preview_schedule', proposal=proposal)
    admin('save_schedule', proposal=proposal, preview=preview['preview'], confirm_reset=False)
    assert request('/config.json')['events'][0]['deadline'].startswith('2026-10-24')
    cli('status')
    assert admin('preview_mail', kind='invite')['messages']
    print('Responses, talk uploads, private downloads, email previews and live settings work through the frontend.', flush=True)

    compose('up', '-d', '--wait', '--wait-timeout', '90', '--no-build', 'backups')
    status = json.loads(compose('exec', '-T', 'backups', 'python', 'scripts/backup.py', 'status'))
    assert not status['error'] and status['databases'] == 4
    archive = Path(backup_directory.name) / status['archive']
    assert archive.is_file() and archive.stat().st_mode & 0o777 == 0o600
    compose('exec', '-T', 'backups', 'python', '-c', "import os; assert not os.access('/data/settings.json', os.W_OK)")
    holder = subprocess.Popen(['docker', 'compose', '-p', project, 'exec', '-T', 'backend', 'python', '-c',
        "import fcntl; f=open('/data/command.lock', 'r'); fcntl.flock(f, fcntl.LOCK_EX); print('locked', flush=True); input()"],
        cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'locked'
        try:
            compose('exec', '-T', 'backups', 'python', 'scripts/backup.py', 'create')
        except RuntimeError as error:
            assert 'BlockingIOError' in str(error)
        else:
            raise AssertionError('The backup ignored an organiser edit lock in the backend container.')
    finally:
        holder.communicate('\n', timeout=10)
    compose('restart', 'backups')
    compose('exec', '-T', 'backups', 'python', 'scripts/backup.py', 'status')
    assert len(list(Path(backup_directory.name).glob('d3seminar-*.tar.gz'))) == 1
    compose('exec', '-T', 'backups', 'python', 'scripts/backup.py', 'verify', '/backups/' + archive.name)
    print('Scheduled backup created and verified; source is read-only, organiser locks are respected, and restart does not duplicate a completed run.', flush=True)

    compose('down', '--remove-orphans')
    compose('up', '-d', '--wait', '--wait-timeout', '60', '--no-build')
    ready()
    current = request('/api/invitation', dict(token=token))
    assert current['answers'] == {slot:'available'}
    assert current['presentation']['title'] == talk['title']
    assert request('/config.json')['events'][0]['deadline'].startswith('2026-10-24')
    material = urlencode(dict(token=token, file_id=uploaded['files']['slides']['id'])).encode()
    assert request('/api/material', material, {'Content-Type':'application/x-www-form-urlencoded'}) == pdf
    try:
        request('/api/invitation', dict(token=token), {'Origin':'https://untrusted.example'})
    except HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError('Untrusted origin was accepted.')
    print('Container recreation preserved replies, uploads, personal links and edited dates. No email was sent.', flush=True)
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            if not member.name.startswith('data/'):
                continue
            target = Path(restore_directory.name) / member.name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(bundle.extractfile(member).read())
            target.chmod(0o600)
    compose('down', '--remove-orphans')
    env['D3_DATA_PATH'] = str(Path(restore_directory.name) / 'data')
    compose('up', '-d', '--wait', '--wait-timeout', '60', '--no-build')
    ready()
    restored = request('/api/invitation', dict(token=token))
    assert restored['answers'] == {slot:'available'}
    assert restored['presentation']['title'] == talk['title']
    assert request('/api/material', material, {'Content-Type':'application/x-www-form-urlencoded'}) == pdf
    assert request('/config.json')['events'][0]['deadline'].startswith('2026-10-24')
    assert admin('dashboard')['ok']
    print('Backup restored into a separate data directory: responses, agenda, original links, organiser key, uploads and dates work.', flush=True)
except Exception:
    print(compose('logs', '--tail=30'), flush=True)
    raise
finally:
    try:
        compose('down', '--volumes', '--remove-orphans')
    finally:
        data_directory.cleanup()
        backup_directory.cleanup()
        restore_directory.cleanup()
