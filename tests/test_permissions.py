import json
from pathlib import Path
import sys
import tempfile
import unittest

from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import presentations
import seminar
import users
from models import PresentationFile, User
from server import Application, Store, create_app


class PermissionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / 'responses.sqlite3')
        config = seminar.read_json(Path(__file__).parent / 'fixtures/programme.json')
        config['site_url'] = 'http://localhost:8765/'
        self.service = Application(store=self.store, config=config, admin_secret='test-operator-' * 4)
        self.service.port = 8765
        self.client = create_app(self.service).test_client()
        self.tokens = {'a':'A'*43, 'b':'B'*43}
        with self.store.session(write=True) as session:
            for key, name, role in [('a', 'Ada Lovelace', 'PhD'), ('b', 'Bea Example', 'Professor')]:
                session.add(User(id=key*24, name=name, email=key+'@example.org', role=role,
                                 token=self.tokens[key], role_status='confirmed'))
        for event, store in self.service.stores.items():
            poll = seminar.poll_data(seminar.event_config(config, event))
            store.sync([dict(id=key*24, name=name, email=key+'@example.org', role=role,
                            token_hash=seminar.digest(self.tokens[key]), poll=poll,
                            revision=seminar.event_revision(poll), site_url=config['site_url'])
                        for key, name, role in [('a', 'Ada Lovelace', 'student'), ('b', 'Bea Example', 'professor')]])
        users.set_organizers(self.store.database, ['a@example.org'])
        self.talks = {key:presentations.save(self.store, token, dict(title=f'Talk {key}', visibility='public', presentation_minutes=20, questions_minutes=10))
                      for key, token in self.tokens.items()}

    def api(self, endpoint='admin', token='a', **data):
        return self.client.post('/api/' + endpoint, base_url='http://localhost:8765',
                                json=dict(token=self.tokens.get(token, token), event_id='seminar-1', **data))

    def invitation(self, **data):
        response = self.api('invitation', include_presentations=True, **data)
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_organizer_access_is_separate_from_academic_roles_and_revoked_immediately(self):
        self.assertTrue(self.invitation()['is_organizer'])
        self.assertFalse(self.invitation(token='b')['is_organizer'])
        for action in ['dashboard', 'finalize', 'preview_schedule', 'save_schedule', 'save_programme', 'reorder_presentations', 'move_presentation']:
            for token in ['b', '', 'bad']:
                with self.subTest(action=action, token=token):
                    self.assertEqual(self.api(token=token, action=action, role='organizer', is_organizer=True).status_code, 403)
        self.assertEqual(self.api(action='dashboard').status_code, 200)
        # Organizer links cannot reveal another author's invitation or impersonate the mailer.
        for action in ['sync', 'responses', 'preview_mail', 'claim_mail', 'finish_mail']:
            self.assertEqual(self.api(action=action).status_code, 403)
        users.set_organizers(self.store.database, ['b@example.org'])
        self.assertFalse(self.invitation()['is_organizer'])
        self.assertEqual(self.api(action='dashboard').status_code, 403)
        self.assertTrue(self.invitation(token='b')['is_organizer'])
        self.assertEqual(self.api(token='b', action='dashboard').status_code, 200)
        with self.store.session() as session:
            self.assertEqual(session.get(User, 'a'*24).role, 'PhD')
            self.assertEqual(session.get(User, 'b'*24).role, 'Professor')
            active = [row for row in self.store.records(session) if row['id']=='a'*24]
        self.store.sync(active)
        self.assertEqual(self.api(token='b', action='dashboard').status_code, 403)

    def test_organizer_reorders_without_changing_talks_and_stale_orders_are_rejected(self):
        before = self.invitation()
        result = self.api(action='reorder_presentations', ordered_ids=['b'*24, 'a'*24], version=before['presentation_order_version'])
        self.assertEqual(result.status_code, 200)
        self.assertEqual([row['name'] for row in result.json['presentations']], ['Bea Example', 'Ada Lovelace'])
        self.assertEqual([row['name'] for row in self.invitation(token='b')['presentations']], ['Bea Example', 'Ada Lovelace'])
        self.assertEqual(self.api(action='reorder_presentations', ordered_ids=['a'*24,'b'*24], version=before['presentation_order_version']).status_code, 400)
        self.assertEqual(self.api(action='reorder_presentations', ordered_ids=['b'*24,'b'*24], version=result.json['presentation_order_version']).status_code, 400)
        with self.store.session() as session:
            for key, original in self.talks.items():
                self.assertEqual(presentations.own(session, key*24), original)
        public = self.api('public-event', token='')
        self.assertEqual(public.json['presentations'], [{'name':'Bea Example'}, {'name':'Ada Lovelace'}])
        self.assertNotIn('is_organizer', public.json)

    def test_organizer_cannot_edit_or_withdraw_another_authors_talk(self):
        for remove in (False, True):
            response = self.api('presentation', participant_id='b'*24, version=self.talks['b']['version'],
                                presentation={'title':'Not the author'}, remove=remove)
            self.assertEqual(response.status_code, 400)
        with self.store.session() as session:
            self.assertEqual(presentations.own(session, 'b'*24), self.talks['b'])
        response = self.api('presentation', token='b', version=self.talks['b']['version'], presentation={'title':'Author update'})
        self.assertEqual(response.status_code, 200)

    def test_moving_events_preserves_details_materials_and_author_editing(self):
        uploaded = presentations.upload(self.store, self.tokens['b'], 'slides', 'talk.pdf', b'%PDF-1.4\n', self.talks['b']['version'])
        destination = self.service.stores['seminar-2']
        before = self.invitation()
        source_answers = self.store.invitation(self.tokens['b'])['answers']
        response = self.api(action='move_presentation', participant_id='b'*24, destination_event_id='seminar-2', version=before['presentation_order_version'])
        self.assertEqual(response.status_code, 200, response.json)
        with self.store.session() as source, destination.session() as target:
            self.assertEqual(presentations.own(source, 'b'*24), {})
            self.assertEqual(presentations.own(target, 'b'*24), uploaded)
        self.assertEqual(self.store.invitation(self.tokens['b'])['answers'], source_answers)
        file_id = uploaded['files']['slides']['id']
        self.assertEqual(presentations.material(destination, self.tokens['b'], file_id)['content'], b'%PDF-1.4\n')
        with self.assertRaises(ValueError):
            presentations.material(self.store, self.tokens['b'], file_id)
        with self.assertRaisesRegex(ValueError, 'another window'):
            presentations.save(self.store, self.tokens['b'], dict(title='Stale edit'), uploaded['version'])
        updated = presentations.save(destination, self.tokens['b'], dict(title='Edited by the author'), uploaded['version'])
        self.assertEqual(updated['title'], 'Edited by the author')
        self.assertEqual(updated['files'], uploaded['files'])

    def test_move_does_not_overwrite_destination_or_leave_a_partial_transfer(self):
        destination = self.service.stores['seminar-2']
        original = presentations.save(destination, self.tokens['b'], dict(title='Already planned here'))
        version = self.invitation()['presentation_order_version']
        move = lambda:self.api(action='move_presentation', participant_id='b'*24, destination_event_id='seminar-2', version=version)
        self.assertEqual(move().status_code, 400)
        with destination.session() as session:
            self.assertEqual(presentations.own(session, 'b'*24), original)
        presentations.save(destination, self.tokens['b'], {}, original['version'], remove=True)
        uploaded = presentations.upload(self.store, self.tokens['b'], 'slides', 'talk.pdf', b'%PDF-1.4\n', self.talks['b']['version'])
        with destination.session(write=True) as session:
            session.add(PresentationFile(id=uploaded['files']['slides']['id'], participant_id='a'*24, kind='slides', name='existing.pdf', mime='application/pdf', content=b'existing'))
        with self.assertRaises(IntegrityError):
            presentations.move(self.store, destination, 'b'*24, version)
        with self.store.session() as source, destination.session() as target:
            self.assertEqual(presentations.own(source, 'b'*24), uploaded)
            self.assertEqual(presentations.own(target, 'b'*24), {})
            self.assertEqual(self.store.state(source), {})
            self.assertEqual(destination.state(target), {})
