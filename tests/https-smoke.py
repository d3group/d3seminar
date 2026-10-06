"""Check the production proxy using a disposable Caddy localhost certificate."""
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler

root = Path(__file__).resolve().parents[1]
(root / 'test-results').mkdir(exist_ok=True)
project = f'd3seminar-https-test-{os.getpid()}'
port = int(os.environ.get('D3_HTTPS_TEST_PORT', '18443'))
http_port = int(os.environ.get('D3_HTTP_TEST_PORT', '18081'))


def command(args, **kwargs):
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout


with tempfile.TemporaryDirectory(prefix='https-', dir=root / 'test-results') as directory:
    directory = Path(directory)
    data = directory / 'data'
    data.mkdir(mode=0o700)
    env = dict(os.environ, D3_API_HOST='localhost', D3_SITE_URL='https://d3group.github.io/d3seminar/',
               D3_DATA_PATH=str(data), D3_UID=str(os.getuid()), D3_GID=str(os.getgid()))
    config = json.loads(command(['docker', 'compose', '-f', 'compose.production.yaml', 'config', '--format', 'json'], env=env))
    config['name'] = project
    for section in ('volumes', 'networks'):
        for entry in config.get(section, {}).values():
            entry.pop('name', None)
    config['services']['backend'].pop('build')
    config['services']['backend']['image'] = 'd3seminar-backend:local'
    config['services']['https']['ports'] = [dict(target=80, published=str(http_port), host_ip='127.0.0.1'),
                                          dict(target=443, published=str(port), host_ip='127.0.0.1')]
    compose_file = directory / 'compose.json'
    compose_file.write_text(json.dumps(config))

    def compose(*args):
        return command(['docker', 'compose', '-p', project, '-f', str(compose_file), *args])

    try:
        compose('run', '--rm', '--no-deps', 'backend', 'python', 'scripts/seminar.py', 'init',
                '--email', 'https-test@example.com', '--name', 'HTTPS Test', '--role', 'student')
        compose('up', '-d', '--wait', '--wait-timeout', '60')
        root_cert = directory / 'root.crt'
        for attempt in range(30):
            try:
                root_cert.write_text(compose('exec', '-T', 'https', 'cat', '/data/caddy/pki/authorities/local/root.crt'))
                break
            except RuntimeError:
                time.sleep(1)
        context = ssl.create_default_context(cafile=str(root_cert))

        def request(path, data=None, headers=None, method=None):
            headers = {'Host':'localhost', 'Origin':'https://d3group.github.io', **(headers or {})}
            if isinstance(data, dict):
                data = json.dumps(data).encode()
                headers['Content-Type'] = 'application/json'
            return urlopen(Request(f'https://localhost:{port}' + path, data=data, headers=headers, method=method), context=context, timeout=5)

        with request('/health') as response:
            assert json.load(response)['ok']
            assert response.headers['Strict-Transport-Security'] == 'max-age=31536000'
            assert response.headers['Access-Control-Allow-Origin'] == 'https://d3group.github.io'
        with request('/config.json') as response:
            public = json.load(response)
            assert public['site_url'] == env['D3_SITE_URL']
            assert public['api_url'] == 'https://localhost/'
        with request('/api/presentation-upload', method='OPTIONS', headers={
                'Access-Control-Request-Method':'POST', 'Access-Control-Request-Headers':'content-type,x-invitation-token,x-event-id,x-file-name,x-material-kind,x-presentation-version'}) as response:
            assert response.status == 204
        with request('/api/public-event', {}) as response:
            assert json.load(response)['ok']
        for headers, path, payload in [({'Origin':'https://untrusted.example'}, '/api/public-event', {}),
                                       ({}, '/api/admin', dict(action='responses', secret='wrong'))]:
            try:
                request(path, payload, headers)
            except HTTPError as error:
                assert error.code == 403
                error.close()
            else:
                raise AssertionError('An unauthorized request succeeded.')

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None

        try:
            build_opener(NoRedirect).open(Request(f'http://127.0.0.1:{http_port}/health', headers={'Host':'localhost'}))
        except HTTPError as error:
            assert error.code == 308
            assert error.headers['Location'] == 'https://localhost/health'
            error.close()
        else:
            raise AssertionError('HTTP did not redirect to HTTPS.')
        print('Production Compose passed: trusted local TLS, HTTP redirect, API proxy, HSTS, Pages CORS and authentication. No public certificate requested.')
    finally:
        compose('down', '--volumes')
