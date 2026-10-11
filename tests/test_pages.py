import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import configure_site


class PagesConfigTests(unittest.TestCase):
    def test_static_launch_clears_any_local_api_address(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config = dict(events=[dict(id='seminar-1')], api_url='http://localhost:8080/',
                          backend_url='http://localhost:8080/api/respond', test_mode=True)
            path.write_text(json.dumps(config))
            with patch.dict(os.environ, {'D3_API_URL':'', 'D3_SITE_URL':''}), contextlib.redirect_stdout(io.StringIO()):
                configure_site.configure(path)
            published = json.loads(path.read_text())
            self.assertEqual(published['api_url'], '')
            self.assertEqual(published['backend_url'], '')
            self.assertEqual(published['site_url'], 'https://d3group.github.io/d3seminar/')
            self.assertEqual(published['events'], config['events'])
            self.assertTrue(published['test_mode'])

    def test_hosted_api_configuration_and_invalid_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{}')
            with patch.dict(os.environ, {'D3_API_URL':'https://seminar-api.example.org/',
                                        'D3_SITE_URL':'https://d3group.github.io/d3seminar/'}), contextlib.redirect_stdout(io.StringIO()):
                configure_site.configure(path)
            self.assertEqual(json.loads(path.read_text())['backend_url'], 'https://seminar-api.example.org/api/respond')
            before = path.read_bytes()
            for key, value in [('D3_API_URL', 'http://localhost:8765/'), ('D3_API_URL', 'https://user:secret@example.org/'),
                               ('D3_API_URL', 'https://example.org/?token=private'), ('D3_API_URL', 'https://example.org/api/'),
                               ('D3_SITE_URL', 'http://localhost:8080/')]:
                with self.subTest(key=key, value=value), patch.dict(os.environ, {'D3_API_URL':'', 'D3_SITE_URL':'', key:value}):
                    with self.assertRaises(ValueError):
                        configure_site.configure(path)
                    self.assertEqual(path.read_bytes(), before)
