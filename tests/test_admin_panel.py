import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import mailer
import presentations
import seminar
import users
import backup
from models import User
from server import Application, Store, create_app


class AdminPanelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root / 'responses.sqlite3')
        config = seminar.read_json(Path(__file__).parent / 'fixtures/programme.json')
        config.update(site_url='http://localhost:8765/', test_mode=False)
        self.service = Application(store=self.store, config=config, admin_secret='operator-test')
        self.service.port = 8765
        self.client = create_app(self.service).test_client()
        self.settings = dict(sender_name='Organizer', sender_email='organizer@example.org',
                             test_recipient='ada@example.org', smtp_username='test', smtp_host='smtp.invalid', smtp_port=465)
        seminar.save_private(self.root / 'settings.json', self.settings)
        with self.store.session(write=True) as session:
            session.add(User(id='a'*24, name='Ada Example', email='ada@example.org', role='PhD',
                             token='A'*43, role_status='confirmed', is_organizer=True))
        for event_id, store in self.service.stores.items():
            poll = seminar.poll_data(seminar.event_config(config, event_id))
            person = dict(id='a'*24, name='Ada Example', email='ada@example.org', role='student', token='A'*43,
                          revision=seminar.event_revision(poll), poll=poll, deliveries={})
            store.sync([dict(person, token_hash=seminar.digest(person['token']), site_url=config['site_url'])])
            seminar.save_private(store.database.parent / 'invitations.json', [person])
        clock = patch.object(seminar, 'now', return_value='2026-10-05T12:00:00+00:00')
        clock.start()
        self.addCleanup(clock.stop)

    def api(self, action, token='A'*43, **data):
        return self.client.post('/api/admin', base_url='http://localhost:8765/',
                                json=dict(dict(action=action, token=token, event_id='seminar-1'), **data))

    def roster(self):
        response = self.api('users')
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def save(self, person, **changes):
        payload = {key:person[key] for key in ('id', 'name', 'email', 'role', 'is_organizer', 'is_archived', 'events') if key in person}
        return self.api('save_user', user=dict(payload, **changes), version=self.roster()['version'])

    def test_users_permissions_handover_and_safe_removal(self):
        for action in ('users', 'save_user', 'email_preview', 'email_draft', 'save_email_draft', 'queue_mail', 'mail_batches', 'cancel_mail'):
            self.assertEqual(self.api(action, token='B'*43).status_code, 403)
        original = self.roster()['users'][0]
        self.assertNotIn('token', original)
        self.assertEqual(self.save(original, is_organizer=False).status_code, 400)
        self.assertEqual(self.save(original, is_archived=True).status_code, 400)
        response = self.save(dict(name='Bea Example', email='bea@example.org', role='Professor', is_organizer=False,
                                  is_archived=False, events=[]))
        self.assertEqual(response.status_code, 200, response.json)
        bea = next(p for p in self.roster()['users'] if p['name']=='Bea Example')
        self.assertEqual(bea['events'], [])
        self.assertEqual(self.save(bea, is_organizer=True, events=list(self.service.stores)).status_code, 200)
        with self.store.session() as session:
            token = session.get(User, bea['id']).token
        self.assertEqual(self.api('users', token=token).status_code, 200)
        self.assertEqual(self.save(original, is_organizer=False).status_code, 200)
        self.assertEqual(self.api('users').status_code, 403)
        self.assertEqual(self.api('users', token=token).status_code, 200)

    def test_profile_changes_preserve_answers_talks_and_stale_edits_fail(self):
        invite = self.store.invitation('A'*43)
        self.store.respond(dict(token='A'*43, partial='true', revision=invite['revision'],
                                **{'answer:'+invite['poll']['slots'][0]['id']:'available'}))
        talk = presentations.save(self.store, 'A'*43, dict(title='Saved research'))
        old = self.roster()
        person = old['users'][0]
        self.assertEqual(self.save(person, name='Ada Full Name').status_code, 200)
        self.assertEqual(self.api('save_user', user=person, version=old['version']).status_code, 400)
        self.assertEqual(self.store.invitation('A'*43)['name'], 'Ada Full Name')
        self.assertEqual(len(self.store.invitation('A'*43)['answers']), 1)
        with self.store.session() as session:
            self.assertEqual(presentations.own(session, 'a'*24), talk)
        self.assertEqual(self.service.organizer.preview_mail('seminar-1', 'invite')['messages'][0]['name'], 'Ada Full Name')
        self.assertEqual(self.save(person, email='bad').status_code, 400)

    def test_removal_revokes_every_event_and_restoration_keeps_authors_work(self):
        response = self.save(dict(name='Bea Example', email='bea@example.org', role='Postdoc', is_organizer=False,
                                  is_archived=False, events=list(self.service.stores)))
        self.assertEqual(response.status_code, 200, response.json)
        bea = next(p for p in self.roster()['users'] if p['name']=='Bea Example')
        with self.store.session() as session:
            token = session.get(User, bea['id']).token
        talk = presentations.save(self.store, token, dict(title='A postdoc contribution'))
        self.assertEqual(self.save(bea, is_archived=True).status_code, 200)
        for store in self.service.stores.values():
            with self.assertRaises(ValueError): store.invitation(token)
        with self.store.session() as session:
            self.assertEqual(presentations.own(session, bea['id']), talk)
        self.assertNotIn('bea@example.org', str(self.service.organizer.preview_mail('seminar-1', 'invite')))
        self.assertEqual(self.save(bea).status_code, 200)
        self.assertEqual(self.store.invitation(token)['presentation'], talk)
        (self.root / 'participants.csv').write_text('name,email,role\nAda Example,ada@example.org,student\n')
        backup.validate_snapshot(self.root)

    def test_test_mode_and_late_event_failures_leave_no_partial_user_changes(self):
        new = dict(name='Bea', email='bea@example.org', role='PhD', is_organizer=False, is_archived=False, events=['seminar-1'])
        self.service.config['test_mode'] = True
        before = self.roster()
        self.assertEqual(self.save(new).status_code, 400)
        self.assertEqual(self.roster()['users'], before['users'])
        self.service.config['test_mode'] = False
        final_store = self.service.stores['onsite']
        with final_store.session(write=True) as session:
            final_store.write_state(session, {'final_event':{'start':'2027-04-12T09:00:00+02:00'}})
        self.assertEqual(self.save(new, events=list(self.service.stores)).status_code, 400)
        self.assertEqual(self.roster()['users'], before['users'])
        self.assertEqual(self.store.dashboard()['registered'], 1)
        with final_store.session(write=True) as session:
            final_store.write_state(session, {})
        legacy = dict(self.service.organizer.records('seminar-2')[0], id='b'*24, email='bea@example.org',
                      token_hash=seminar.digest('B'*43), site_url=self.service.config['site_url'])
        self.service.stores['seminar-2'].sync([legacy])
        before = self.roster()
        self.assertEqual(self.save(new, events=list(self.service.stores)).status_code, 400)
        self.assertEqual(self.roster()['users'], before['users'])

    def test_email_preview_queue_and_local_delivery(self):
        draft = self.api('email_draft', kind='invite', mail_event='next').json
        self.assertTrue(draft.get('ok'), draft)
        self.assertEqual(self.api('save_email_draft', kind='invite', mail_event=draft['event_id'], version=draft['version'],
                                 subject='Planning together: {event_name}', body=draft['body'] + '\nYour ideas are welcome.').status_code, 200)
        preview = self.api('email_preview', kind='invite', mail_event='next').json
        self.assertTrue(preview.get('ok'), preview)
        self.assertNotIn('A'*43, str(preview))
        queued = self.api('queue_mail', kind='invite', mail_event='next', version=preview['version'])
        self.assertEqual(queued.status_code, 200, queued.json)
        duplicate = self.api('queue_mail', kind='invite', mail_event='next', version=preview['version'])
        self.assertEqual(queued.json['batch_id'], duplicate.json['batch_id'])
        self.assertEqual(self.api('preview_queued').status_code, 403)
        def local_api(settings, action, **data):
            response = self.api(action, secret='operator-test', **data)
            if not response.json.get('ok'): raise ValueError(response.json.get('error'))
            return response.json
        with patch.object(mailer, 'api', side_effect=local_api), patch.object(mailer.smtplib, 'SMTP_SSL') as smtp, \
             patch.object(mailer.getpass, 'getpass', return_value='local-only'), contextlib.redirect_stdout(io.StringIO()):
            mailer.run(self.settings, self.root/'mail', 'queued')
            smtp.assert_not_called()
            mailer.run(self.settings, self.root/'mail', 'queued', send=True)
            client = smtp.return_value.__enter__.return_value
            self.assertEqual(client.send_message.call_count, 1)
            sent = client.send_message.call_args.args[0]
            self.assertEqual(sent['Subject'], 'Planning together: Seminar 1')
            self.assertIn('Your ideas are welcome.', sent.get_body(preferencelist=('plain',)).get_content())
            mailer.run(self.settings, self.root/'mail', 'queued', send=True)
            self.assertEqual(client.send_message.call_count, 1)
        batch = self.api('mail_batches').json['batches'][0]
        self.assertEqual(batch['counts']['sent'], 1)

    def test_email_drafts_are_private_scoped_versioned_and_validated(self):
        for kind in ('initial', 'invite', 'remind', 'present', 'confirm'):
            draft = self.api('email_draft', kind=kind, mail_event='next').json
            self.assertTrue(draft.get('ok'), draft)
            self.assertIn('{name}', draft['body'])
            self.assertNotIn('A'*43, str(draft))
        draft = self.api('email_draft', kind='invite', mail_event='seminar-1').json
        before_other = self.api('email_draft', kind='invite', mail_event='seminar-2').json
        edited = dict(kind='invite', mail_event='seminar-1', version=draft['version'],
                      subject='Hello {first_name}: {event_name}', body=draft['body'] + '\nA new idea: <script>alert(1)</script>')
        result = self.api('save_email_draft', **edited)
        self.assertEqual(result.status_code, 200, result.json)
        saved = self.api('email_draft', kind='invite', mail_event='seminar-1').json
        self.assertEqual(saved['body'], edited['body'])
        self.assertNotEqual(saved['version'], draft['version'])
        self.assertEqual(self.api('email_preview', kind='invite', mail_event='seminar-1', draft_version=draft['version']).status_code, 400)
        self.assertEqual(self.api('save_email_draft', **edited).status_code, 400)
        self.assertEqual(self.api('email_draft', kind='invite', mail_event='seminar-2').json, before_other)
        mail = self.service.organizer.preview_mail('seminar-1', 'invite')['messages'][0]
        self.assertEqual(mail['subject'], 'Hello Ada: Seminar 1')
        self.assertIn('Hello Ada Example,', mail['body'])
        self.assertNotIn('<script>', mail['html'])
        self.assertIn('&lt;script&gt;', mail['html'])
        for changes in [dict(subject='Bad\r\nBcc: outsider@example.org'), dict(body='{unknown}'),
                        dict(body='{name.__class__} {deadline} {invitation_link}'), dict(body='No invitation link'),
                        dict(subject='{schedule}'), dict(body=saved['body']+'\n#token='+'A'*43), dict(body='x'*20001)]:
            invalid = dict(edited, version=saved['version'], **changes)
            self.assertEqual(self.api('save_email_draft', **invalid).status_code, 400)
        self.assertEqual(self.api('email_draft', kind='invite', mail_event='seminar-1').json, saved)

    def test_editing_a_queued_draft_requires_a_new_preview_before_delivery(self):
        preview = self.api('email_preview', kind='initial', mail_event='next').json
        batch = self.api('queue_mail', kind='initial', mail_event='next', version=preview['version']).json['batch_id']
        plan = self.api('preview_queued', secret='operator-test').json['plans'][0]
        row = plan['messages'][0]
        draft = self.api('email_draft', kind='initial', mail_event='next').json
        self.assertEqual(self.api('save_email_draft', kind='initial', mail_event='seminar-1', version=draft['version'],
                                 subject=draft['subject'], body=draft['body']+'\nPlease join the discussion.').status_code, 200)
        self.assertEqual(self.api('preview_queued', secret='operator-test').json['plans'], [])
        self.assertEqual(self.api('mail_batches').json['batches'][0]['counts']['review'], 1)
        stale = self.api('claim_mail', secret='operator-test', kind='initial', participant_id=row['id'],
                         preview=row['preview'], message_id='<stale@example.org>', batch_id=batch)
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(self.api('queue_mail', kind='initial', mail_event='next', version=preview['version']).status_code, 400)
        revised = self.api('email_preview', kind='initial', mail_event='next').json
        self.assertEqual(self.api('queue_mail', kind='initial', mail_event='next', version=revised['version']).status_code, 200)
        ready = self.api('preview_queued', secret='operator-test').json['plans']
        self.assertEqual(len(ready), 1)
        self.assertIn('Please join the discussion.', ready[0]['messages'][0]['body'])

    def test_cancellation_and_changed_recipients_cannot_be_sent_from_stale_queue(self):
        preview = self.api('email_preview', kind='invite', mail_event='next').json
        self.assertTrue(preview.get('ok'), preview)
        batch = self.api('queue_mail', kind='invite', mail_event='next', version=preview['version']).json['batch_id']
        plan = self.api('preview_queued', secret='operator-test').json['plans'][0]
        self.assertEqual(self.api('cancel_mail', batch_id=batch).status_code, 200)
        row = plan['messages'][0]
        claim = self.api('claim_mail', secret='operator-test', kind='invite', participant_id=row['id'],
                         preview=row['preview'], message_id='<test@example.org>', batch_id=batch)
        self.assertFalse(claim.json['claimed'])
        self.assertEqual(self.api('preview_queued', secret='operator-test').json['plans'], [])
        self.api('queue_mail', kind='invite', mail_event='next', version=preview['version'])
        self.save(self.roster()['users'][0], email='new@example.org')
        self.assertEqual(self.api('preview_queued', secret='operator-test').json['plans'], [])
        self.assertEqual(self.api('queue_mail', kind='invite', mail_event='next', version=preview['version']).status_code, 400)

    def test_initial_outreach_targets_first_seminar_once_and_enables_reminders(self):
        with patch.object(self.service.organizer, 'upcoming_seminars', return_value=[{'id':'seminar-2'}]):
            preview = self.api('email_preview', kind='initial', mail_event='next')
        self.assertEqual(preview.status_code, 200, preview.json)
        plan = preview.json
        self.assertEqual(plan['event_id'], 'seminar-1')
        self.assertEqual(len(plan['messages']), 1)
        body = plan['messages'][0]['body']
        self.assertIn('first D3 seminar', body)
        self.assertIn('other seminars and the onsite', body)
        self.assertIn('event=seminar-1#token=[personal-link]', body)
        self.assertNotIn('A'*43, body)
        self.assertEqual(self.api('email_preview', kind='initial', mail_event='onsite').status_code, 400)
        batch = self.api('queue_mail', kind='initial', mail_event='next', version=plan['version']).json['batch_id']
        sender = self.api('preview_queued', secret='operator-test').json['plans'][0]
        row = sender['messages'][0]
        self.assertEqual(sender['kind'], 'initial')
        self.assertEqual(self.api('email_preview', kind='remind', mail_event='seminar-1').json['messages'], [])
        claim = self.api('claim_mail', secret='operator-test', kind='initial', participant_id=row['id'],
                         preview=row['preview'], message_id='<initial@example.org>', batch_id=batch)
        self.assertTrue(claim.json['claimed'])
        self.assertEqual(self.api('email_preview', kind='initial', mail_event='next').json['messages'], [])
        self.assertEqual(self.api('email_preview', kind='remind', mail_event='seminar-1').json['messages'], [])
        self.assertEqual(self.api('finish_mail', secret='operator-test', participant_id=row['id'], key=row['key'], message_id='<initial@example.org>').status_code, 200)
        self.assertEqual(len(self.api('email_preview', kind='remind', mail_event='seminar-1').json['messages']), 1)
        self.assertEqual(self.api('email_preview', kind='remind', mail_event='seminar-2').json['messages'], [])
        proposal = dict(deadline='2026-10-08T17:00', options=[dict(id=s['id'], start=s['start'][:16]) for s in self.store.dashboard()['poll']['slots']])
        proposal['options'][0]['start'] = '2026-11-02T14:00'
        schedule = self.service.organizer.preview_schedule('seminar-1', proposal)
        self.service.organizer.save_schedule('seminar-1', proposal, schedule['preview'], True)
        self.assertEqual(self.api('email_preview', kind='initial', mail_event='next').json['messages'], [])
        self.assertEqual(self.api('mail_batches').json['batches'][0]['counts']['sent'], 1)


if __name__ == '__main__':
    unittest.main()
