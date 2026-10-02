import argparse
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

SPEC = importlib.util.spec_from_file_location('seminar', Path(__file__).parents[1] / 'scripts/seminar.py')
seminar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seminar)
CONFIG = json.loads((Path(__file__).parents[1] / 'site/config.json').read_text())
ADDRESS = 'organizer@example.com'


class SeminarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'site').mkdir()
        (root / 'site/config.json').write_text(json.dumps(CONFIG))
        for name, value in [('ROOT', root), ('PRIVATE', root / '.private'), ('CONFIG', root / 'site/config.json')]:
            patcher = patch.object(seminar, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        seminar.initialize(argparse.Namespace(email=ADDRESS, name='Organizer', smtp_user='jmu-user'))

    def invites(self):
        return seminar.read_json(seminar.PRIVATE / 'invitations.json')

    def remote(self, invite, status='pending', updated=''):
        return dict(id=invite['id'], email=invite['email'], revision=invite['revision'],
                    token_hash=seminar.digest(invite['token']), response=status, updated_at=updated, comment='')

    def args(self, kind='invite', send=False):
        return argparse.Namespace(kind=kind, send=send, to=ADDRESS, all=False)

    def test_preparing_again_preserves_tokens_and_delivery_history(self):
        records = self.invites()
        records[0]['deliveries']['invite'] = {'status': 'sent'}
        seminar.save_private(seminar.PRIVATE / 'invitations.json', records)
        seminar.prepare(argparse.Namespace(csv=None))
        self.assertEqual(self.invites(), records)

    def test_changed_time_rotates_token_and_clears_delivery_history(self):
        before = self.invites()[0]
        config = copy.deepcopy(CONFIG)
        config['location'] = 'New room'
        seminar.CONFIG.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'details changed'):
            seminar.load_invitations(config)
        seminar.prepare(argparse.Namespace(csv=None))
        after = self.invites()[0]
        self.assertNotEqual(before['token'], after['token'])
        self.assertNotEqual(before['revision'], after['revision'])
        self.assertEqual(after['deliveries'], {})

    def test_test_mode_rejects_another_recipient_without_changing_state(self):
        before = self.invites()
        (seminar.PRIVATE / 'participants.csv').write_text('name,email,slot_id\nOther,other@example.com,test-session\n')
        with self.assertRaisesRegex(ValueError, 'Test mode'):
            seminar.prepare(argparse.Namespace(csv=None))
        self.assertEqual(self.invites(), before)

    def test_personal_mail_contains_fragment_link_and_no_backend_secret(self):
        local = seminar.settings()
        mail = seminar.message(CONFIG, local, self.invites()[0], 'invite')
        self.assertEqual(mail['To'], ADDRESS)
        self.assertIn(ADDRESS, mail['From'])
        self.assertTrue(mail['Subject'].startswith('[TEST]'))
        plain = mail.get_body(preferencelist=('plain',)).get_content()
        self.assertIn('#token=', plain)
        self.assertIn('Europe/Berlin', plain)
        self.assertNotIn(local['admin_secret'], str(mail))

    def test_confirmation_has_calendar_attachment(self):
        mail = seminar.message(CONFIG, seminar.settings(), self.invites()[0], 'confirm')
        attachments = list(mail.iter_attachments())
        self.assertEqual(attachments[0].get_filename(), 'd3-seminar.ics')
        self.assertIn(b'STATUS:CONFIRMED', attachments[0].get_payload(decode=True))
        self.assertIn(b'DTSTART:20261009T120000Z', attachments[0].get_payload(decode=True))

    def test_dry_run_never_connects_to_smtp(self):
        with patch.object(seminar.smtplib, 'SMTP_SSL') as smtp:
            seminar.send(self.args())
            smtp.assert_not_called()
        self.assertEqual(len(list((seminar.PRIVATE / 'outbox').glob('*.eml'))), 1)
        self.assertEqual(self.invites()[0]['deliveries'], {})

    def test_send_is_individual_and_not_repeated(self):
        invite = self.invites()[0]
        smtp = MagicMock()
        with patch.object(seminar, 'backend', return_value={'records': [self.remote(invite)]}), \
             patch.object(seminar.getpass, 'getpass', return_value='test-password'), \
             patch.object(seminar.smtplib, 'SMTP_SSL', return_value=smtp):
            seminar.send(self.args(send=True))
            seminar.send(self.args(send=True))
        client = smtp.__enter__.return_value
        client.login.assert_called_once_with('jmu-user', 'test-password')
        client.send_message.assert_called_once()
        self.assertEqual(client.send_message.call_args.kwargs['to_addrs'], [ADDRESS])
        self.assertEqual(self.invites()[0]['deliveries']['invite']['status'], 'sent')

    def test_ambiguous_smtp_failure_is_not_retried_automatically(self):
        invite = self.invites()[0]
        smtp = MagicMock()
        smtp.__enter__.return_value.send_message.side_effect = OSError('connection lost')
        with patch.object(seminar, 'backend', return_value={'records': [self.remote(invite)]}), \
             patch.object(seminar.getpass, 'getpass', return_value='test-password'), \
             patch.object(seminar.smtplib, 'SMTP_SSL', return_value=smtp):
            with self.assertRaises(OSError):
                seminar.send(self.args(send=True))
            seminar.send(self.args(send=True))
        smtp.__enter__.return_value.send_message.assert_called_once()
        self.assertEqual(self.invites()[0]['deliveries']['invite']['status'], 'attempted')

    def test_remote_mismatch_blocks_mail_before_smtp(self):
        with patch.object(seminar, 'backend', return_value={'records': []}), \
             patch.object(seminar.smtplib, 'SMTP_SSL') as smtp:
            with self.assertRaisesRegex(ValueError, 'Run sync'):
                seminar.send(self.args(send=True))
            smtp.assert_not_called()

    def test_reminder_and_confirmation_target_correct_states(self):
        invite = self.invites()[0]
        self.assertFalse(seminar.eligible(invite, self.remote(invite), 'remind'))
        invite['deliveries']['invite'] = {'status': 'sent'}
        self.assertEqual(seminar.eligible(invite, self.remote(invite), 'remind'), 'remind')
        self.assertFalse(seminar.eligible(invite, self.remote(invite, 'confirmed'), 'remind'))
        self.assertFalse(seminar.eligible(invite, self.remote(invite), 'confirm'))
        self.assertEqual(seminar.eligible(invite, self.remote(invite, 'confirmed', 'today'), 'confirm'), 'confirm:today')

    def test_backend_cannot_send_admin_secret_to_another_host(self):
        local = seminar.settings()
        local['local_base_url'] = 'https://example.com/'
        seminar.save_private(seminar.PRIVATE / 'settings.json', local)
        with self.assertRaisesRegex(ValueError, 'local backend'):
            seminar.backend(CONFIG, 'responses')

    def test_private_state_permissions(self):
        self.assertEqual((seminar.PRIVATE / 'settings.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(seminar.PRIVATE.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
