import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import seminar
from server import Store
from organizer import Organizer, local_datetime


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.private = root / '.private'
        self.config = seminar.read_json(Path(__file__).parent / 'fixtures/programme.json')
        self.config.update(site_url='http://127.0.0.1:8765/', backend_url='http://127.0.0.1:8765/api/respond')
        self.config_file = root / 'config.json'
        self.config_file.write_text(json.dumps(self.config))
        self.store = Store(self.private / 'responses.sqlite3')
        self.poll = seminar.poll_data(self.config)
        self.token = 'A' * 43
        self.invite = dict(id='a'*24, name='Ada', email='ada@example.com', token=self.token, poll=self.poll,
                           revision=seminar.event_revision(self.poll), deliveries={})
        seminar.save_private(self.private / 'invitations.json', [self.invite])
        seminar.save_private(self.private / 'settings.json', dict(sender_name='Organizer', sender_email='organizer@example.com',
            smtp_username='test-user', smtp_host='smtp.example.com', smtp_port=465, test_recipient='ada@example.com'))
        self.store.sync([dict(self.invite, token_hash=seminar.digest(self.token), site_url=self.config['site_url'])])
        self.organizer = Organizer(self.config, {'seminar-1':self.store}, self.config_file, self.private)

    def answer(self):
        invitation = self.store.invitation(self.token)
        self.store.respond(dict(token=self.token, revision=invitation['revision'], response_version=invitation['response_version'], comment='Keep my note',
            **{'answer:' + invitation['poll']['slots'][0]['id']:'available'}))

    def schedule(self):
        return dict(deadline='2026-10-08T17:00', options=[dict(id=s['id'], start=s['start'][:16]) for s in self.poll['slots']])

    def test_failed_config_reload_keeps_the_last_valid_settings(self):
        before = copy.deepcopy(self.organizer.config)
        invalid_poll = copy.deepcopy(before)
        invalid_poll['events'][-1]['slots'] = []
        failures = [(None, FileNotFoundError), ('{', json.JSONDecodeError), ('{}', ValueError),
                    (json.dumps(dict(before, events=[])), ValueError),
                    (json.dumps(dict(before, test_mode='false')), ValueError),
                    (json.dumps(invalid_poll), ValueError)]
        for case, (content, error) in enumerate(failures):
            with self.subTest(case=case):
                if content is None:
                    self.config_file.unlink()
                else:
                    self.config_file.write_text(content)
                with self.assertRaises(error):
                    self.organizer.reload_config()
                self.assertEqual(self.organizer.config, before)
        replacement = dict(before, subtitle='Updated after a failed reload')
        self.config_file.write_text(json.dumps(replacement))
        self.organizer.reload_config()
        self.assertEqual(self.organizer.config, replacement)

    def test_deadline_edit_preserves_answers_links_and_delivery_history(self):
        self.answer()
        records = seminar.read_json(self.private / 'invitations.json')
        records[0]['deliveries']['invite:' + records[0]['revision']] = {'status':'sent'}
        seminar.save_private(self.private / 'invitations.json', records)
        preview = self.organizer.preview_schedule('seminar-1', self.schedule())
        self.assertFalse(preview['resets_answers'])
        self.organizer.save_schedule('seminar-1', self.schedule(), preview['preview'], False)
        current = self.store.invitation(self.token)
        self.assertEqual(current['answers'], {self.poll['slots'][0]['id']:'available'})
        self.assertEqual(current['comment'], 'Keep my note')
        record = seminar.read_json(self.private / 'invitations.json')[0]
        self.assertEqual(record['token'], self.token)
        self.assertEqual(record['revision'], current['revision'])
        self.assertEqual(record['deliveries']['invite:' + record['revision']]['status'], 'sent')
        self.assertEqual(seminar.event_config(seminar.read_json(self.config_file))['deadline'], current['poll']['deadline'])

    def test_new_dates_require_reset_consent_and_archive_old_answers(self):
        self.answer()
        proposal = self.schedule()
        proposal['options'][0]['start'] = '2026-11-02T14:00'
        preview = self.organizer.preview_schedule('seminar-1', proposal)
        self.assertTrue(preview['resets_answers'])
        with self.assertRaisesRegex(ValueError, 'new round'):
            self.organizer.save_schedule('seminar-1', proposal, preview['preview'], False)
        self.organizer.save_schedule('seminar-1', proposal, preview['preview'], True)
        current = self.store.invitation(self.token)
        self.assertEqual(current['answers'], {})
        self.assertTrue(current['poll']['slots'][0]['start'].endswith('+01:00'))
        with self.store.session(write=True) as connection:
            history = json.loads(connection.connection().exec_driver_sql('SELECT data FROM history ORDER BY rowid DESC').fetchone()[0])
        self.assertEqual(history['records'][0]['comment'], 'Keep my note')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.respond(dict(token=self.token, revision=self.invite['revision']))

    def test_date_edits_keep_the_onsite_final_morning(self):
        event = seminar.event_config(self.config, 'onsite')
        event.update(duration_days=3, daily_hours=dict(start='09:00', end='17:00', last_day_end='13:00'))
        event['slots'] = [dict(id='a', start='2027-04-12T09:00:00+02:00', end='2027-04-14T13:00:00+02:00'),
                          dict(id='b', start='2027-04-19T09:00:00+02:00', end='2027-04-21T13:00:00+02:00')]
        poll = seminar.poll_data(event)
        store = Store(self.private / 'events/onsite/responses.sqlite3')
        store.sync([dict(self.invite, poll=poll, revision=seminar.event_revision(poll), token_hash=seminar.digest(self.token), site_url=self.config['site_url'])])
        self.organizer.stores['onsite'] = store
        proposal = dict(deadline='2026-10-08T17:00', options=[dict(start='2027-04-13'), dict(start='2027-04-20')])
        preview = self.organizer.preview_schedule('onsite', proposal)
        self.assertEqual([s['end'] for s in preview['poll']['slots']], ['2027-04-15T13:00:00+02:00', '2027-04-22T13:00:00+02:00'])

    def test_adding_dates_preserves_existing_replies_and_leaves_new_options_unanswered(self):
        self.answer()
        records = seminar.read_json(self.private / 'invitations.json')
        old_key = 'invite:' + records[0]['revision']
        records[0]['deliveries'][old_key] = {'status':'sent'}
        seminar.save_private(self.private / 'invitations.json', records)
        before = self.store.invitation(self.token)
        proposal = self.schedule()
        proposal['options'].append(dict(start='2026-11-02T14:00'))
        preview = self.organizer.preview_schedule('seminar-1', proposal)
        self.assertFalse(preview['resets_answers'])
        self.organizer.save_schedule('seminar-1', proposal, preview['preview'], False)
        current = self.store.invitation(self.token)
        self.assertEqual(current['answers'], before['answers'])
        self.assertEqual(current['comment'], before['comment'])
        self.assertEqual(current['updated_at'], before['updated_at'])
        self.assertEqual(len(current['poll']['slots']), len(before['poll']['slots']) + 1)
        self.assertNotIn(current['poll']['slots'][-1]['id'], current['answers'])
        record = seminar.read_json(self.private / 'invitations.json')[0]
        self.assertEqual(record['token'], self.token)
        self.assertEqual(record['deliveries'][old_key]['status'], 'sent')
        self.assertNotIn('invite:' + record['revision'], record['deliveries'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.respond(dict(token=self.token, revision=before['revision']))

    def test_removing_dates_preserves_remaining_answers_notes_and_personal_link(self):
        self.answer()
        removed, kept = self.poll['slots'][:2]
        self.store.respond(dict(token=self.token, revision=self.invite['revision'], partial='true',
                                **{'answer:' + kept['id']:'unavailable'}))
        before = self.store.invitation(self.token)
        proposal = self.schedule()
        proposal['options'] = proposal['options'][1:]
        preview = self.organizer.preview_schedule('seminar-1', proposal)
        self.assertFalse(preview['resets_answers'])
        self.organizer.save_schedule('seminar-1', proposal, preview['preview'], False)
        current = self.store.invitation(self.token)
        self.assertEqual(current['answers'], {kept['id']:'unavailable'})
        self.assertEqual(current['comment'], before['comment'])
        self.assertEqual(current['updated_at'], before['updated_at'])
        record = seminar.read_json(self.private / 'invitations.json')[0]
        self.assertEqual(record['token'], self.token)
        self.assertEqual(record['revision'], current['revision'])
        with self.store.session(write=True) as connection:
            history = json.loads(connection.connection().exec_driver_sql('SELECT data FROM history ORDER BY rowid DESC').fetchone()[0])
        self.assertEqual(history['records'][0]['answers'], before['answers'])
        self.assertNotIn(removed['id'], current['answers'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.respond(dict(token=self.token, revision=before['revision']))

    def test_schedule_preview_expires_after_response_and_cannot_change_finalized_time(self):
        proposal = self.schedule()
        preview = self.organizer.preview_schedule('seminar-1', proposal)
        self.answer()
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.organizer.save_schedule('seminar-1', proposal, preview['preview'], True)
        dashboard = self.store.dashboard()
        self.store.select('finalize', self.poll['slots'][0]['id'], dashboard['snapshot'])
        with self.assertRaisesRegex(ValueError, 'finalized'):
            self.organizer.preview_schedule('seminar-1', proposal)

    def test_programme_changes_do_not_reset_answers_and_appear_in_calendar(self):
        self.answer()
        before = self.store.invitation(self.token)
        programme = dict(topic='Research progress', presenters='Ada & Bea', room='Room 2', agenda='First talk\nDiscussion')
        self.organizer.save_programme('seminar-1', programme, self.store.dashboard()['snapshot'])
        current = self.store.invitation(self.token)
        self.assertEqual(current['answers'], before['answers'])
        self.assertEqual(current['revision'], before['revision'])
        self.assertEqual(current['state']['programme'], programme)
        self.store.select('finalize', self.poll['slots'][0]['id'], self.store.dashboard()['snapshot'])
        event = self.store.invitation(self.token)['state']['final_event']
        calendar = seminar.calendar_bytes(event)
        self.assertIn(b'LOCATION:Room 2', calendar)
        self.assertIn(b'Research progress', calendar)

    def test_local_times_reject_nonexistent_or_ambiguous_clock_times(self):
        for value in ('2027-03-28T02:30', '2026-10-25T02:30'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_datetime(value, 'Europe/Berlin')
        self.assertEqual(local_datetime('2026-12-07T14:00', 'Europe/Berlin').isoformat(), '2026-12-07T14:00:00+01:00')

    def test_failed_file_write_rolls_back_poll_and_restart_recovers_interrupted_edit(self):
        self.answer()
        before = self.store.invitation(self.token)
        preview = self.organizer.preview_schedule('seminar-1', self.schedule())
        original_write = self.organizer.write_edit
        def fail_after_config(edit, version):
            if version == 'after':
                self.config_file.write_text(json.dumps(edit['after']['config']))
                raise OSError('test disk failure')
            original_write(edit, version)
        with patch.object(self.organizer, 'write_edit', side_effect=fail_after_config):
            with self.assertRaises(OSError):
                self.organizer.save_schedule('seminar-1', self.schedule(), preview['preview'], False)
        self.assertEqual(self.store.invitation(self.token), before)
        self.assertEqual(seminar.read_json(self.config_file), self.config)
        edit = dict(event_id='seminar-1', revision='0'*64,
                    before=dict(config=self.config, invitations=[self.invite]),
                    after=dict(config={}, invitations=[]))
        seminar.save_private(self.organizer.journal, edit)
        self.config_file.write_text('{}')
        Organizer(self.config, {'seminar-1':self.store}, self.config_file, self.private)
        self.assertFalse(self.organizer.journal.exists())
        self.assertTrue(seminar.read_json(self.config_file)['events'])


if __name__ == '__main__':
    unittest.main()
