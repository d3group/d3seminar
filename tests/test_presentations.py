import copy
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import presentations
import seminar
from server import Store
from test_server import INVITE, POLL, RESPONSE, SLOT, TOKEN


class PresentationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / 'responses.sqlite3')
        self.people = [dict(INVITE, role='student'),
                       dict(INVITE, id='b'*24, role='professor', name='Bea', email='bea@example.com', token_hash=seminar.digest('B'*43)),
                       dict(INVITE, id='c'*24, role='student', name='Cora', email='cora@example.com', token_hash=seminar.digest('C'*43))]
        self.store.sync(self.people)
        self.talk = dict(title='A research update', abstract='An idea to discuss.', explainer_url='https://example.org/explainer', slides_url='')

    def test_students_and_professors_edit_only_their_own_talk_with_an_active_token(self):
        for token in ('X'*43, ''):
            with self.assertRaises(ValueError):
                presentations.save(self.store, token, self.talk)
        saved = presentations.save(self.store, TOKEN, self.talk)
        other = presentations.save(self.store, 'C'*43, dict(self.talk, title='Another talk'))
        professor = presentations.save(self.store, 'B'*43, dict(self.talk, title='An optional contribution'))
        result = self.store.invitation('B'*43, include_presentations=True)
        self.assertEqual(result['role'], 'professor')
        self.assertEqual(result['presentation'], professor)
        self.assertEqual([row['name'] for row in result['presentations']], ['Ada', 'Bea', 'Cora'])
        self.assertEqual([row['name'] for row in result['presentations'] if row['mine']], ['Bea'])
        self.assertFalse(any('email' in row or 'token_hash' in row for row in result['presentations']))
        with self.assertRaisesRegex(ValueError, 'changed'):
            presentations.save(self.store, TOKEN, self.talk)
        with self.assertRaises(ValueError):
            presentations.save(self.store, TOKEN, dict(self.talk, slides_url='javascript:alert(1)'), saved['version'])
        presentations.save(self.store, TOKEN, {}, saved['version'], remove=True)
        self.assertEqual(self.store.invitation(TOKEN)['presentation'], {})
        self.assertEqual(self.store.invitation('C'*43)['presentation']['version'], other['version'])
        self.assertEqual(self.store.invitation('B'*43)['presentation']['version'], professor['version'])
        uploaded = presentations.upload(self.store, 'B'*43, 'slides', 'talk.pdf', b'%PDF-1.4\n', professor['version'])
        self.assertEqual(presentations.material(self.store, TOKEN, uploaded['files']['slides']['id'])['content'], b'%PDF-1.4\n')
        self.store.sync([self.people[0], self.people[2]])
        with self.assertRaises(ValueError):
            presentations.save(self.store, 'B'*43, self.talk, uploaded['version'])
        self.assertEqual([row['name'] for row in self.store.invitation(TOKEN, include_presentations=True)['presentations']], ['Cora'])
        with self.assertRaises(ValueError):
            presentations.material(self.store, TOKEN, uploaded['files']['slides']['id'])

    def test_presentations_survive_date_changes_and_can_be_updated_after_finalization(self):
        self.store.respond(dict(RESPONSE, comment='Keep availability separate.'))
        saved = presentations.save(self.store, TOKEN, self.talk)
        self.assertEqual(self.store.invitation(TOKEN)['answers'], {SLOT:'available'})
        changed = copy.deepcopy(self.people)
        for person in changed:
            person['poll'] = dict(POLL, location='New room')
            person['revision'] = seminar.event_revision(person['poll'])
        self.store.sync(changed)
        self.assertEqual(self.store.invitation(TOKEN)['presentation'], saved)
        with self.store.session(write=True) as connection:
            self.store.write_state(connection, dict(final_event=seminar.event_data(POLL, POLL['slots'][0])))
        updated = presentations.save(self.store, TOKEN, dict(self.talk, abstract='Updated abstract.'), saved['version'])
        self.assertEqual(updated['abstract'], 'Updated abstract.')

    def test_author_timings_persist_and_reject_invalid_minutes(self):
        saved = presentations.save(self.store, TOKEN, self.talk)
        self.assertIsNone(saved['presentation_minutes'])
        self.assertIsNone(saved['questions_minutes'])
        proposal = dict(self.talk, presentation_minutes=25, questions_minutes=10)
        saved = presentations.save(self.store, TOKEN, proposal, saved['version'])
        uploaded = presentations.upload(self.store, TOKEN, 'slides', 'talk.pdf', b'%PDF-1.4\n', saved['version'])
        self.assertEqual(uploaded['presentation_minutes'], 25)
        self.assertEqual(uploaded['questions_minutes'], 10)
        talk = self.store.invitation('B'*43, include_presentations=True)['presentations'][0]
        self.assertEqual((talk['presentation_minutes'], talk['questions_minutes']), (25, 10))
        for key in ('presentation_minutes', 'questions_minutes'):
            for value in (-1, 481, 2.5, True, '20'):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    presentations.save(self.store, TOKEN, dict(proposal, **{key:value}), uploaded['version'])
        with self.assertRaises(ValueError):
            presentations.save(self.store, TOKEN, dict(proposal, presentation_minutes=0), uploaded['version'])
        zero = presentations.save(self.store, TOKEN, dict(proposal, questions_minutes=0), uploaded['version'])
        self.assertEqual(zero['questions_minutes'], 0)
        cleared = presentations.save(self.store, TOKEN, dict(self.talk, questions_minutes=None), zero['version'])
        self.assertIsNone(cleared['questions_minutes'])
        self.assertEqual(cleared['presentation_minutes'], 25)

    def test_placeholder_requires_a_title_stays_internal_and_can_become_a_talk(self):
        with self.assertRaises(ValueError):
            presentations.save(self.store, TOKEN, dict(title='', status='placeholder'))
        with self.assertRaises(ValueError):
            presentations.save(self.store, TOKEN, dict(title='Provisional topic', status='placeholder', visibility='public'))
        saved = presentations.save(self.store, TOKEN, dict(title='Provisional topic', status='placeholder'))
        self.assertEqual(saved['status'], 'placeholder')
        self.assertEqual(self.store.invitation(TOKEN, include_presentations=True)['presentations'][0]['status'], 'placeholder')
        updated = presentations.save(self.store, TOKEN, dict(self.talk, status='talk'), saved['version'])
        self.assertEqual(updated['status'], 'talk')

    def test_visibility_defaults_to_internal_and_public_access_can_be_revoked(self):
        saved = presentations.save(self.store, TOKEN, self.talk)
        self.assertEqual(saved['visibility'], 'internal')
        # Existing proposals without a visibility choice remain internal.
        with self.store.session(write=True) as connection:
            legacy = {key:value for key,value in saved.items() if key not in ('files', 'visibility', 'presentation_minutes', 'questions_minutes')}
            connection.connection().exec_driver_sql('UPDATE presentations SET data=?', (json.dumps(legacy),))
        self.assertEqual(self.store.invitation(TOKEN)['presentation']['visibility'], 'internal')
        self.assertIsNone(self.store.invitation(TOKEN)['presentation']['presentation_minutes'])
        self.assertIsNone(self.store.invitation(TOKEN)['presentation']['questions_minutes'])
        uploaded = presentations.upload(self.store, TOKEN, 'slides', 'talk.pdf', b'%PDF-1.4\n', saved['version'])
        file_id = uploaded['files']['slides']['id']
        def public_talks():
            with self.store.session(write=True) as connection:
                return presentations.listing(self.store, connection)
        self.assertEqual(public_talks(), [])
        with self.assertRaises(ValueError):
            presentations.material(self.store, None, file_id)
        for visibility in ('everyone', '', None, True, []):
            with self.subTest(visibility=visibility), self.assertRaises(ValueError):
                presentations.save(self.store, TOKEN, dict(self.talk, visibility=visibility), uploaded['version'])
        public = presentations.save(self.store, TOKEN, dict(self.talk, visibility='public'), uploaded['version'])
        presentations.save(self.store, 'C'*43, dict(self.talk, title='Unpublished idea'))
        listing = public_talks()
        self.assertEqual([row['name'] for row in listing], ['Ada'])
        self.assertEqual(listing, [{'name':'Ada'}])
        for token in (None, '', 'X'*43):
            with self.subTest(token=token), self.assertRaises(ValueError):
                presentations.material(self.store, token, file_id)
        private = self.store.invitation('B'*43, include_presentations=True)['presentations'][0]
        self.assertEqual(private['title'], self.talk['title'])
        self.assertEqual(private['abstract'], self.talk['abstract'])
        self.assertEqual(private['explainer_url'], self.talk['explainer_url'])
        self.assertEqual(presentations.material(self.store, 'B'*43, file_id)['content'], b'%PDF-1.4\n')
        internal = presentations.save(self.store, TOKEN, dict(self.talk, visibility='internal'), public['version'])
        self.assertEqual(public_talks(), [])
        with self.assertRaises(ValueError):
            presentations.material(self.store, None, file_id)
        self.assertEqual(presentations.material(self.store, 'B'*43, file_id)['content'], b'%PDF-1.4\n')
        public = presentations.save(self.store, TOKEN, dict(self.talk, visibility='public'), internal['version'])
        presentations.save(self.store, TOKEN, {}, public['version'], remove=True)
        self.assertEqual(public_talks(), [])
        with self.assertRaises(ValueError):
            presentations.material(self.store, None, file_id)

    def test_uploads_are_private_replaceable_and_revoked_with_the_presenter(self):
        saved = presentations.save(self.store, TOKEN, self.talk)
        for kind, name, data in [('slides', 'bad.html', b'<script>x</script>'), ('explainer', '../x.html', b'<p>x</p>'),
                                 ('slides', 'fake.pdf', b'not a PDF'), ('slides', 'large.pdf', b'%PDF-' + b'x'*presentations.MAX_FILE_SIZE)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                presentations.upload(self.store, TOKEN, kind, name, data, saved['version'])
        with self.assertRaises(ValueError):
            presentations.upload(self.store, 'B'*43, 'slides', 'talk.pdf', b'%PDF-1.4\n', '')
        uploaded = presentations.upload(self.store, TOKEN, 'explainer', 'idea.html', b'<h1>My idea</h1>', saved['version'])
        file_id = uploaded['files']['explainer']['id']
        self.assertEqual(presentations.material(self.store, 'B'*43, file_id)['content'], b'<h1>My idea</h1>')
        with self.assertRaises(ValueError):
            presentations.material(self.store, 'X'*43, file_id)
        replaced = presentations.upload(self.store, TOKEN, 'explainer', 'new.html', b'<h1>New idea</h1>', uploaded['version'])
        with self.assertRaises(ValueError):
            presentations.material(self.store, TOKEN, file_id)
        self.store.sync(self.people[1:])
        with self.assertRaises(ValueError):
            presentations.material(self.store, 'B'*43, replaced['files']['explainer']['id'])
