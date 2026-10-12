import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import check_publication

ROOT = Path(__file__).parents[1]


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / 'site', self.root / 'site')
        shutil.copy(ROOT / '.gitignore', self.root / '.gitignore')
        self.git('init', '-q')

    def git(self, *args, **kwargs):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, **kwargs)

    def test_unexpected_artifacts_and_symlinks_fail_even_when_ignored(self):
        self.assertEqual(check_publication.check_site(self.root / 'site'), [])
        for name in ('responses.sqlite3', 'notes.txt', '.env', 'assets/export.json'):
            with self.subTest(name=name):
                path = self.root / 'site' / name
                path.write_text('private export')
                self.assertTrue(check_publication.check_site(self.root / 'site'))
                path.unlink()
        asset = self.root / 'site/assets/d3.svg'
        asset.unlink()
        self.assertTrue(check_publication.check_site(self.root / 'site'))
        asset.symlink_to(ROOT / 'site/assets/d3.svg')
        self.assertTrue(check_publication.check_site(self.root / 'site'))

    def test_renamed_binary_and_private_config_fields_are_rejected(self):
        path = self.root / 'site/home.js'
        path.write_bytes(b'SQLite format 3\x00')
        self.assertTrue(check_publication.check_site(self.root / 'site'))
        shutil.copy(ROOT / 'site/home.js', path)
        path = self.root / 'site/config.json'
        config = json.loads(path.read_text())
        for where in (config, config['events'][0], config['slots'][0]):
            where['participants'] = [{'email':'person@example.org'}]
            path.write_text(json.dumps(config))
            self.assertTrue(check_publication.check_site(self.root / 'site'))
            del where['participants']
        path.write_text(json.dumps(config))
        self.assertEqual(check_publication.check_site(self.root / 'site'), [])

    def test_private_exports_are_ignored_and_force_added_files_block_publication(self):
        names = ['responses.sqlite3', 'cache.db-wal', 'export.sqlite-journal', 'export.sql',
                 'private.pem', 'signing.key', 'id_ed25519', 'identity.p12',
                 '.env', 'data/settings.json', 'participants.csv']
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('test data')
        ignored = self.git('check-ignore', '--stdin', input='\n'.join(names).encode()).stdout.decode().splitlines()
        self.assertEqual(set(ignored), set(names))
        self.assertEqual(check_publication.check_repository(self.root), [])
        self.git('add', '--force', '--', *names)
        errors = check_publication.check_repository(self.root)
        self.assertEqual(len(errors), len(names))


if __name__ == '__main__':
    unittest.main()
