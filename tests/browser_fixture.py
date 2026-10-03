"""Isolated local browser-test backend; never uses the real participant database."""
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import seminar
from server import Store, create_server

with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory) / 'qa.sqlite3')
    config = seminar.read_json(seminar.ROOT / 'site/config.json')
    app = create_server(0, store=store, config=config, admin_secret='browser-test-only-' * 3)
    config['site_url'] = f'http://127.0.0.1:{app.server_address[1]}/'
    config['backend_url'] = config['site_url'] + 'api/respond'
    poll = seminar.poll_data(config)
    revision = seminar.event_revision(poll)
    token = 'T' * 43
    store.sync([dict(id='d'*24, name='Browser Test', email='browser-test@example.com',
                     token_hash=seminar.digest(token), revision=revision, poll=poll, site_url=config['site_url'])])
    print(json.dumps(dict(base=config['site_url'], invitation=config['site_url'] + '#' + urlencode(dict(token=token, slot='test-session', revision='legacy')), admin=config['site_url'] + 'admin#' + urlencode(dict(key=app.admin_secret)))), flush=True)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.server_close()
