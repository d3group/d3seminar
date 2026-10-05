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
CONFIG = json.loads((Path(__file__).parent / 'fixtures/programme.json').read_text())
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
                    token_hash=seminar.digest(invite['token']), answers={slot['id']:'available' for slot in CONFIG['slots']} if status == 'confirmed' else {}, updated_at=updated, comment='')

    def args(self, kind='invite', send=False):
        return argparse.Namespace(kind=kind, send=send, to=ADDRESS, all=False)

    def test_preparing_again_preserves_tokens_and_delivery_history(self):
        records = self.invites()
        records[0]['deliveries']['invite'] = {'status': 'sent'}
        seminar.save_private(seminar.PRIVATE / 'invitations.json', records)
        seminar.prepare(argparse.Namespace(csv=None))
        self.assertEqual(self.invites(), records)

    def test_roster_roles_survive_preparation_and_invalid_roles_are_rejected(self):
        before = self.invites()[0]
        roster = seminar.PRIVATE / 'participants.csv'
        roster.write_text('name,email,role\nOrganizer,' + ADDRESS + ',student\n')
        seminar.prepare(argparse.Namespace(csv=None))
        after = self.invites()[0]
        self.assertEqual(after['role'], 'student')
        self.assertEqual(after['token'], before['token'])
        self.assertEqual(after['revision'], before['revision'])
        roster.write_text('name,email,role\nOrganizer,' + ADDRESS + ',visitor\n')
        with self.assertRaisesRegex(ValueError, 'role'):
            seminar.prepare(argparse.Namespace(csv=None))
        self.assertEqual(self.invites()[0], after)

    def test_changed_time_preserves_personal_link_and_archives_old_invitation(self):
        before = self.invites()[0]
        config = copy.deepcopy(CONFIG)
        config['location'] = 'New room'
        seminar.CONFIG.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'details changed'):
            seminar.load_invitations(config)
        seminar.prepare(argparse.Namespace(csv=None))
        after = self.invites()[0]
        self.assertEqual(before['token'], after['token'])
        self.assertEqual(len(list((seminar.PRIVATE / 'history').glob('*.json'))), 1)
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
        html_body = mail.get_body(preferencelist=('html',)).get_content()
        self.assertIn('<p ', html_body)
        self.assertIn('<br>', html_body)
        self.assertIn('>Open your personal invitation</a>', html_body)

    def test_confirmation_has_calendar_attachment(self):
        mail = seminar.message(CONFIG, seminar.settings(), self.invites()[0], 'confirm', {'final_event':seminar.event_data(CONFIG, CONFIG['slots'][0])})
        attachments = list(mail.iter_attachments())
        self.assertEqual(attachments[0].get_filename(), 'd3-seminar.ics')
        self.assertIn(b'STATUS:CONFIRMED', attachments[0].get_payload(decode=True))
        self.assertIn(b'DTSTART:20261009T120000Z', attachments[0].get_payload(decode=True))
        self.assertIn(b'DTEND:20261009T140000Z', attachments[0].get_payload(decode=True))

    def test_only_student_emails_link_directly_to_the_presentation_form(self):
        for role in ('student', 'professor', ''):
            for kind in ('invite', 'remind', 'confirm'):
                with self.subTest(role=role, kind=kind):
                    invite = dict(self.invites()[0], role=role)
                    mail = seminar.message(CONFIG, seminar.settings(), invite, kind,
                                           {'final_event':seminar.event_data(CONFIG, CONFIG['slots'][0])})
                    plain = mail.get_body(preferencelist=('plain',)).get_content()
                    rich = mail.get_body(preferencelist=('html',)).get_content()
                    if role == 'student':
                        self.assertIn('agenda.html?present=seminar-1#token=' + invite['token'], plain)
                        self.assertIn('at least one of the three seminars and for the onsite', plain)
                        self.assertIn('>Add your presentation</a>', rich)
                    else:
                        self.assertNotIn('agenda.html?present=', plain)
                        self.assertNotIn('Add your presentation', rich)

    def test_dry_run_never_connects_to_smtp(self):
        with patch.object(seminar.smtplib, 'SMTP_SSL') as smtp:
            seminar.send(self.args())
            smtp.assert_not_called()
        self.assertEqual(len(list((seminar.PRIVATE / 'outbox').glob('*.eml'))), 1)
        self.assertEqual(self.invites()[0]['deliveries'], {})

    def test_send_is_individual_and_not_repeated(self):
        invite = self.invites()[0]
        smtp = MagicMock()
        with patch.object(seminar, 'backend', return_value={'records': [self.remote(invite)], 'state':{}}), \
             patch.object(seminar.getpass, 'getpass', return_value='test-password'), \
             patch.object(seminar.smtplib, 'SMTP_SSL', return_value=smtp):
            seminar.send(self.args(send=True))
            seminar.send(self.args(send=True))
        client = smtp.__enter__.return_value
        client.login.assert_called_once_with('jmu-user', 'test-password')
        client.send_message.assert_called_once()
        self.assertEqual(client.send_message.call_args.kwargs['to_addrs'], [ADDRESS])
        self.assertEqual(self.invites()[0]['deliveries']['invite:' + invite['revision']]['status'], 'sent')

    def test_ambiguous_smtp_failure_is_not_retried_automatically(self):
        invite = self.invites()[0]
        smtp = MagicMock()
        smtp.__enter__.return_value.send_message.side_effect = OSError('connection lost')
        with patch.object(seminar, 'backend', return_value={'records': [self.remote(invite)], 'state':{}}), \
             patch.object(seminar.getpass, 'getpass', return_value='test-password'), \
             patch.object(seminar.smtplib, 'SMTP_SSL', return_value=smtp):
            with self.assertRaises(OSError):
                seminar.send(self.args(send=True))
            seminar.send(self.args(send=True))
        smtp.__enter__.return_value.send_message.assert_called_once()
        self.assertEqual(self.invites()[0]['deliveries']['invite:' + invite['revision']]['status'], 'attempted')

    def test_remote_mismatch_blocks_mail_before_smtp(self):
        with patch.object(seminar, 'backend', return_value={'records': []}), \
             patch.object(seminar.smtplib, 'SMTP_SSL') as smtp:
            with self.assertRaisesRegex(ValueError, 'Run sync'):
                seminar.send(self.args(send=True))
            smtp.assert_not_called()

    def test_reminder_and_confirmation_target_correct_states(self):
        invite = self.invites()[0]
        self.assertFalse(seminar.eligible(invite, self.remote(invite), 'remind'))
        invite['deliveries']['invite:' + invite['revision']] = {'status': 'sent'}
        self.assertEqual(seminar.eligible(invite, self.remote(invite), 'remind'), 'remind:' + invite['revision'])
        self.assertFalse(seminar.eligible(invite, self.remote(invite, 'confirmed'), 'remind'))
        self.assertFalse(seminar.eligible(invite, self.remote(invite), 'confirm'))
        state = dict(final_event=seminar.event_data(CONFIG, CONFIG['slots'][0]), finalized_at='today')
        self.assertEqual(seminar.eligible(invite, self.remote(invite, 'confirmed'), 'confirm', state), 'confirm:today')
        self.assertFalse(seminar.eligible(invite, self.remote(invite), 'invite', state))

    def test_retired_conditional_reply_is_unfinished_and_check_mail_is_rejected(self):
        invite = self.invites()[0]
        invite['deliveries']['invite:' + invite['revision']] = dict(status='sent')
        response = dict(answers={slot['id']:'if_needed' for slot in CONFIG['slots']})
        self.assertEqual(seminar.eligible(invite, response, 'remind'), 'remind:' + invite['revision'])
        self.assertFalse(seminar.eligible(invite, response, 'check'))
        with self.assertRaises(ValueError):
            seminar.message(CONFIG, seminar.settings(), invite, 'check')

    def test_calendar_cannot_be_sent_before_finalization(self):
        with self.assertRaisesRegex(ValueError, 'Finalize'):
            seminar.message(CONFIG, seminar.settings(), self.invites()[0], 'confirm')

    def test_response_arriving_during_password_prompt_skips_reminder(self):
        invite = self.invites()[0]
        invite['deliveries']['invite:' + invite['revision']] = dict(status='sent')
        seminar.save_private(seminar.PRIVATE / 'invitations.json', [invite])
        pending = {invite['id']:self.remote(invite)}
        done = {invite['id']:self.remote(invite, 'confirmed')}
        with patch.object(seminar, 'get_responses', side_effect=[pending, done]), \
             patch.object(seminar, 'backend', return_value={'state':{}}), \
             patch.object(seminar.getpass, 'getpass', return_value='test-password'), \
             patch.object(seminar.smtplib, 'SMTP_SSL') as smtp:
            seminar.send(self.args(kind='remind', send=True))
        smtp.return_value.__enter__.return_value.send_message.assert_not_called()

    def test_backend_cannot_send_admin_secret_to_another_host(self):
        local = seminar.settings()
        local['local_base_url'] = 'https://example.com/'
        seminar.save_private(seminar.PRIVATE / 'settings.json', local)
        with self.assertRaisesRegex(ValueError, 'local backend'):
            seminar.backend(CONFIG, 'responses')

    def test_events_share_the_personal_link_but_keep_deliveries_separate(self):
        first = self.invites()[0]
        first['deliveries']['invite:' + first['revision']] = {'status':'sent'}
        seminar.save_private(seminar.PRIVATE / 'invitations.json', [first])
        with patch.object(seminar, 'EVENT_ID', 'seminar-2'):
            seminar.prepare(argparse.Namespace(csv=None))
            config = seminar.load_config()
            second = seminar.load_invitations(config)[0]
            self.assertEqual(second['token'], first['token'])
            self.assertNotEqual(second['revision'], first['revision'])
            self.assertEqual(second['deliveries'], {})
            self.assertIn('event=seminar-2#token=', seminar.invitation_url(config, second))
            seminar.send(self.args())
            self.assertEqual(len(list((seminar.PRIVATE / 'events/seminar-2/outbox').glob('*.eml'))), 1)
        self.assertEqual(self.invites()[0], first)

    def test_onsite_mail_and_calendar_cover_both_dates(self):
        with patch.object(seminar, 'EVENT_ID', 'onsite'):
            seminar.prepare(argparse.Namespace(csv=None))
            config = seminar.load_config()
            invite = seminar.load_invitations(config)[0]
            event = seminar.event_data(config, config['slots'][0])
            mail = seminar.message(config, seminar.settings(), invite, 'confirm', {'final_event':event})
            plain = mail.get_body(preferencelist=('plain',)).get_content()
            self.assertIn('Monday, 02 November 2026', plain)
            self.assertIn('Tuesday, 03 November 2026', plain)
            self.assertIn('onsite meeting', str(mail['Subject']))
            calendar = next(mail.iter_attachments()).get_payload(decode=True)
            self.assertIn(b'DTSTART:20261102T080000Z', calendar)
            self.assertIn(b'DTEND:20261103T160000Z', calendar)
            self.assertEqual(calendar.count(b'BEGIN:VEVENT'), 2)
            self.assertIn(b'DTEND:20261102T160000Z', calendar)
            self.assertIn(b'DTSTART:20261103T080000Z', calendar)
            self.assertIn(b'UID:d3seminar-mvp-onsite-day-1@d3seminar', calendar)
            self.assertIn(b'UID:d3seminar-mvp-onsite-day-2@d3seminar', calendar)
            self.assertIn('09:00–17:00 each day', plain)

    def test_onsite_options_require_two_calendar_days(self):
        poll = seminar.poll_data(seminar.event_config(CONFIG, 'onsite'))
        bad = copy.deepcopy(poll)
        bad['slots'][0]['end'] = '2026-11-02T17:00:00+01:00'
        with self.assertRaisesRegex(ValueError, 'calendar days'):
            seminar.validate_poll(bad)
        bad = copy.deepcopy(poll)
        bad['slots'][0] = dict(id='dst-change', start='2026-10-24T09:00:00+02:00', end='2026-10-25T17:00:00+01:00')
        seminar.validate_poll(bad)

    def test_daily_calendar_hours_follow_local_clock_across_daylight_saving(self):
        config = seminar.event_config(CONFIG, 'onsite')
        slot = dict(id='dst-change', start='2026-10-24T09:00:00+02:00', end='2026-10-25T17:00:00+01:00')
        calendar = seminar.calendar_bytes(seminar.event_data(config, slot))
        self.assertEqual(calendar.count(b'BEGIN:VEVENT'), 2)
        for line in [b'DTSTART:20261024T070000Z', b'DTEND:20261024T150000Z', b'DTSTART:20261025T080000Z', b'DTEND:20261025T160000Z']:
            self.assertIn(line, calendar)

    def test_onsite_final_morning_is_validated_and_exported_without_blocking_afternoons(self):
        config = seminar.event_config(CONFIG, 'onsite')
        config.update(duration_days=3, daily_hours=dict(start='09:00', end='17:00', last_day_end='13:00'))
        config['slots'] = [dict(id='dst', start='2026-10-24T09:00:00+02:00', end='2026-10-26T13:00:00+01:00'),
                           dict(id='nov', start='2026-11-02T09:00:00+01:00', end='2026-11-04T13:00:00+01:00')]
        poll = seminar.poll_data(config)
        seminar.validate_poll(poll)
        invite = dict(self.invites()[0], poll=poll)
        event = seminar.event_data(config, config['slots'][0])
        mail = seminar.message(config, seminar.settings(), invite, 'confirm', {'final_event':event})
        plain = mail.get_body(preferencelist=('plain',)).get_content()
        self.assertIn('final day 09:00–13:00', plain)
        calendar = next(mail.iter_attachments()).get_payload(decode=True)
        self.assertEqual(calendar.count(b'BEGIN:VEVENT'), 3)
        for line in [b'DTSTART:20261024T070000Z', b'DTEND:20261024T150000Z', b'DTSTART:20261025T080000Z',
                     b'DTEND:20261025T160000Z', b'DTSTART:20261026T080000Z', b'DTEND:20261026T120000Z']:
            self.assertIn(line, calendar)
        invitation = seminar.message(config, seminar.settings(), invite, 'invite').get_body(preferencelist=('plain',)).get_content()
        self.assertIn('final day 09:00–13:00', invitation)
        self.assertNotIn('two consecutive days', invitation)
        bad = copy.deepcopy(poll)
        bad['slots'][0]['end'] = '2026-10-26T17:00:00+01:00'
        with self.assertRaisesRegex(ValueError, 'daily hours'):
            seminar.validate_poll(bad)
        for end in ['09:00', '08:00', '17:00', '18:00']:
            bad = copy.deepcopy(poll)
            bad['daily_hours']['last_day_end'] = end
            with self.assertRaises(ValueError):
                seminar.validate_poll(bad)

    def test_private_state_permissions(self):
        self.assertEqual((seminar.PRIVATE / 'settings.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(seminar.PRIVATE.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
