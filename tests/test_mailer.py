import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import seminar
import presentations
import mailer
from waitress.wasyncore import close_all
from server import Store, create_server


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = seminar.read_json(Path(__file__).parent / 'fixtures/programme.json')
        config.update(test_mode=False, site_url='http://localhost:8765/')
        store = Store(self.root / 'responses.sqlite3')
        self.server = create_server(0, store=store, config=config, admin_secret='test-secret')
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.settings = dict(api_url=f'http://127.0.0.1:{self.server.effective_port}/', admin_secret='test-secret',
                             sender_email='organizer@example.com', sender_name='Organizer', smtp_username='uni-user',
                             smtp_host='smtp.invalid', smtp_port=465)
        seminar.save_private(self.root / 'settings.json', dict(self.settings, test_recipient='ada@example.com'))
        for event in config['events']:
            poll = seminar.poll_data(seminar.event_config(config, event['id']))
            people = [dict(id=letter*24, name=name, email=name.lower()+'@example.com', role=role,
                           token=letter.upper()*43, poll=poll, revision=seminar.event_revision(poll), deliveries={})
                      for letter, name, role in [('a','Ada','student'),('b','Bea','student'),('c','Cora','professor')]]
            current = self.server.d3_app.stores[event['id']]
            current.sync([dict(p, token_hash=seminar.digest(p['token']), site_url=config['site_url']) for p in people])
            seminar.save_private(current.database.parent / 'invitations.json', people)
        self.clock = patch.object(seminar, 'now', return_value='2026-10-05T12:00:00+00:00')
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def stop(self):
        self.server.task_dispatcher.shutdown()
        self.server.trigger.pull_trigger(lambda: close_all(self.server._map))
        self.thread.join(timeout=3)

    def preview(self, kind):
        return mailer.api(self.settings, 'preview_mail', mail_event='next', kind=kind)

    def complete(self, token='A'*43):
        store = self.server.d3_app.store
        invite = store.invitation(token)
        store.respond(dict(token=token, revision=invite['revision'], **{'answer:'+s['id']:'available' for s in invite['poll']['slots']}))

    def test_next_seminar_invites_everyone_and_reminds_only_unfinished_invitees(self):
        self.complete()
        plan = self.preview('invite')
        self.assertEqual(plan['event_id'], 'seminar-1')
        self.assertEqual(len(plan['messages']), 3)
        self.assertEqual(self.preview('remind')['messages'], [])
        for row in plan['messages']:
            claim = mailer.api(self.settings, 'claim_mail', event_id=plan['event_id'], kind='invite', participant_id=row['id'], preview=row['preview'], message_id='<test@example.com>')
            self.assertTrue(claim['claimed'])
            mailer.api(self.settings, 'finish_mail', event_id=plan['event_id'], participant_id=row['id'], key=row['key'], message_id='<test@example.com>')
        self.assertEqual(self.preview('invite')['messages'], [])
        self.assertEqual([m['name'] for m in self.preview('remind')['messages']], ['Bea','Cora'])
        store = self.server.d3_app.store
        with store.connect() as connection:
            store.write_state(connection, dict(final_event=dict(start='2026-10-09T14:00:00+02:00', end='2026-10-09T16:00:00+02:00')))
        self.assertEqual(self.preview('invite')['messages'], [])
        with patch.object(seminar, 'now', return_value='2026-10-10T12:00:00+00:00'):
            self.assertEqual(self.preview('invite')['event_id'], 'seminar-2')

    def test_student_nudges_link_to_later_placeholders_and_skip_planned_students(self):
        plan = self.preview('present')
        self.assertEqual([m['name'] for m in plan['messages']], ['Ada','Bea'])
        self.assertIn('present=seminar-1', plan['messages'][0]['body'])
        self.assertIn('present=seminar-2&placeholder=1', plan['messages'][0]['body'])
        self.assertIn('provisional title', plan['messages'][0]['body'])
        self.assertNotIn('Cora', json.dumps(plan['messages']))
        later = self.server.d3_app.stores['seminar-2']
        presentations.save(later, 'A'*43, dict(title='Early research idea', status='placeholder'))
        self.assertEqual([m['name'] for m in self.preview('present')['messages']], ['Bea'])
        row = plan['messages'][0]
        claim = mailer.api(self.settings, 'claim_mail', event_id='seminar-1', kind='present', participant_id=row['id'], preview=row['preview'], message_id='<test@example.com>')
        self.assertFalse(claim['claimed'])

    def test_local_preview_does_not_send_and_smtp_failure_cannot_duplicate_mail(self):
        with patch.object(mailer.smtplib, 'SMTP_SSL') as smtp, contextlib.redirect_stdout(io.StringIO()):
            mailer.run(self.settings, self.root/'local-mail', 'invite')
            smtp.assert_not_called()
        self.assertEqual(len(list((self.root/'local-mail/outbox').glob('*.eml'))), 3)
        self.assertEqual(len(self.preview('invite')['messages']), 3)
        with patch.object(mailer.getpass, 'getpass', return_value='local-only-password'), \
             patch.object(mailer.smtplib, 'SMTP_SSL') as smtp, contextlib.redirect_stdout(io.StringIO()):
            client = smtp.return_value.__enter__.return_value
            client.send_message.side_effect = TimeoutError('Ambiguous SMTP outcome')
            with self.assertRaises(TimeoutError):
                mailer.run(self.settings, self.root/'local-mail', 'invite', send=True)
            client.login.assert_called_once_with('uni-user', 'local-only-password')
            self.assertEqual(client.send_message.call_args.kwargs['to_addrs'], ['ada@example.com'])
        self.assertEqual([m['name'] for m in self.preview('invite')['messages']], ['Bea','Cora'])
        self.assertNotIn('local-only-password', (self.root/'invitations.json').read_text())
        with self.assertRaises(ValueError):
            mailer.api(self.settings, 'send_mail', kind='invite', password='must-not-send')

    def test_unauthorized_mail_access_and_changed_previews_are_rejected(self):
        with self.assertRaises(ValueError):
            mailer.api(dict(self.settings, admin_secret='wrong'), 'preview_mail', kind='invite')
        plan = self.preview('invite')
        row = plan['messages'][0]
        self.server.d3_app.config['semester'] = 'Changed semester'
        with self.assertRaisesRegex(ValueError, 'changed'):
            mailer.api(self.settings, 'claim_mail', event_id='seminar-1', kind='invite', participant_id=row['id'], preview=row['preview'], message_id='<test@example.com>')
        self.server.d3_app.config['test_mode'] = True
        with self.assertRaisesRegex(ValueError, 'Test mode'):
            self.preview('invite')

    def test_successful_local_sending_rechecks_reminders_after_password_entry(self):
        with patch.object(mailer.getpass, 'getpass', return_value='local-only'), patch.object(mailer.smtplib, 'SMTP_SSL') as smtp, contextlib.redirect_stdout(io.StringIO()):
            mailer.run(self.settings, self.root/'mail', 'invite', send=True)
            client = smtp.return_value.__enter__.return_value
            self.assertEqual(client.send_message.call_count, 3)
            self.assertTrue(all(len(call.kwargs['to_addrs']) == 1 for call in client.send_message.call_args_list))
            mailer.run(self.settings, self.root/'mail', 'invite', send=True)
            self.assertEqual(client.send_message.call_count, 3)
        def password():
            self.complete()
            return 'local-only'
        with patch.object(mailer.getpass, 'getpass', side_effect=lambda _:password()), patch.object(mailer.smtplib, 'SMTP_SSL') as smtp, contextlib.redirect_stdout(io.StringIO()):
            mailer.run(self.settings, self.root/'mail', 'remind', send=True)
            sent = smtp.return_value.__enter__.return_value.send_message.call_args_list
            self.assertEqual([call.kwargs['to_addrs'] for call in sent], [['bea@example.com'], ['cora@example.com']])
        self.assertEqual(self.preview('remind')['messages'], [])

    def test_confirmation_preview_has_a_stable_calendar_timestamp(self):
        poll = self.server.d3_app.store.dashboard()['poll']
        with self.server.d3_app.store.connect() as connection:
            self.server.d3_app.store.write_state(connection, dict(final_event=seminar.event_data(poll, poll['slots'][0]), finalized_at='2026-10-01T12:34:56+00:00'))
        plan = self.preview('confirm')
        self.assertIn('DTSTAMP:20261001T123456Z', plan['messages'][0]['attachments'][0]['content'])
        row = plan['messages'][0]
        self.assertTrue(mailer.api(self.settings, 'claim_mail', event_id='seminar-1', kind='confirm', participant_id=row['id'], preview=row['preview'], message_id='<test@example.com>')['claimed'])


if __name__ == '__main__':
    unittest.main()
