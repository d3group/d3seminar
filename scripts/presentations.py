import io
import json
from pathlib import Path
import secrets
from urllib.parse import urlparse
import zipfile

from sqlalchemy import delete, func, select

import seminar
from models import Invitation, Presentation, PresentationFile

MAX_FILE_SIZE = 20 * 1024 * 1024
PRESENTER_ROLES = {'student', 'professor', 'member'}
MATERIAL_POLICY = "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'"


def own(session, participant_id):
    result = session.scalar(select(Presentation.data).where(Presentation.participant_id == participant_id))
    if result is None:
        return {}
    result.setdefault('visibility', 'internal')
    result.setdefault('status', 'talk')
    result.setdefault('presentation_minutes', None)
    result.setdefault('questions_minutes', None)
    result['files'] = {kind:dict(id=key, name=name, mime=mime, size=size) for key, kind, name, mime, size in session.execute(
        select(PresentationFile.id, PresentationFile.kind, PresentationFile.name, PresentationFile.mime,
               func.length(PresentationFile.content)).where(PresentationFile.participant_id == participant_id))}
    return result


def ordered_people(people, state):
    order = {key:index for index, key in enumerate(state.get('programme_order', []))}
    return sorted(people, key=lambda person: (order.get(person['id'], len(order)), person['name'].casefold(), person['id']))


def listing(store, session, viewer_id=None):
    people = ordered_people(store.records(session), store.state(session))
    result = []
    for person in people:
        if not person['active'] or person.get('role') not in PRESENTER_ROLES:
            continue
        talk = own(session, person['id'])
        if talk and (viewer_id is not None or talk['visibility'] == 'public'):
            row = dict(name=person['name'])
            if viewer_id is not None:
                row['participant_id'] = person['id']
                row['email'] = person['email']
                row.update({key:talk[key] for key in ('title', 'abstract', 'visibility', 'status', 'presentation_minutes', 'questions_minutes', 'explainer_url', 'slides_url', 'files')})
                row['mine'] = person['id'] == viewer_id
            result.append(row)
    return result


def order_version(talks):
    return seminar.event_revision([talk['participant_id'] for talk in talks])


def reorder(store, ordered_ids, version):
    with store.session(write=True) as session:
        talks = listing(store, session, viewer_id='')
        current = [talk['participant_id'] for talk in talks]
        if version != order_version(talks):
            raise ValueError('The running order changed. Reload the agenda before moving a talk.')
        if (not isinstance(ordered_ids, list) or any(not isinstance(key, str) for key in ordered_ids)
                or len(ordered_ids) != len(current) or set(ordered_ids) != set(current)):
            raise ValueError('Include each current presenter exactly once in the running order.')
        store.write_state(session, dict(store.state(session), programme_order=ordered_ids))


def move(store, destination, participant_id, version):
    if store.database == destination.database:
        raise ValueError('Choose a different event.')
    # One SQLite transaction covers both event databases, including uploaded files.
    with store.session(write=True) as session:
        connection = session.connection()
        connection.exec_driver_sql('ATTACH DATABASE ? AS destination', (str(destination.database.resolve()),))
        talks = listing(store, session, viewer_id='')
        if version != order_version(talks):
            raise ValueError('The running order changed. Reload the agenda before moving a talk.')
        if participant_id not in [talk['participant_id'] for talk in talks]:
            raise ValueError('Choose a current presenter.')
        person = next(person for person in store.records(session) if person['id'] == participant_id)
        target = connection.exec_driver_sql('SELECT data FROM destination.invitations WHERE id=?', (participant_id,)).scalar()
        target = json.loads(target) if target else None
        if (not target or not target.get('active') or target.get('role') not in PRESENTER_ROLES
                or target['token_hash'] != person['token_hash'] or target['email'] != person['email']):
            raise ValueError('The author needs a matching active invitation to the destination event.')
        if connection.exec_driver_sql('SELECT 1 FROM destination.presentations WHERE participant_id=?', (participant_id,)).scalar():
            raise ValueError('This author already has a talk in that event. Ask them to resolve it before moving this talk.')
        target_state = connection.exec_driver_sql('SELECT data FROM destination.poll_state WHERE id=1').scalar()
        target_state = json.loads(target_state) if target_state else {}
        target_people = [json.loads(raw) for raw, in connection.exec_driver_sql('SELECT data FROM destination.invitations')]
        target_talks = set(connection.exec_driver_sql('SELECT participant_id FROM destination.presentations').scalars())
        target_state['programme_order'] = [person['id'] for person in ordered_people(target_people, target_state)
                                           if person['active'] and person['id'] in target_talks] + [participant_id]
        connection.exec_driver_sql('INSERT INTO destination.presentations (participant_id, data) SELECT participant_id, data FROM main.presentations WHERE participant_id=?', (participant_id,))
        connection.exec_driver_sql('INSERT INTO destination.presentation_files (id, participant_id, kind, name, mime, content) SELECT id, participant_id, kind, name, mime, content FROM main.presentation_files WHERE participant_id=?', (participant_id,))
        connection.exec_driver_sql('DELETE FROM main.presentation_files WHERE participant_id=?', (participant_id,))
        connection.exec_driver_sql('DELETE FROM main.presentations WHERE participant_id=?', (participant_id,))
        connection.exec_driver_sql('INSERT INTO destination.poll_state (id, data) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (json.dumps(target_state),))
        state = store.state(session)
        state['programme_order'] = [talk['participant_id'] for talk in talks if talk['participant_id'] != participant_id]
        store.write_state(session, state)


def editable(store, session, token, version):
    person = store.lookup(session, token)
    if person.get('role') not in PRESENTER_ROLES:
        raise ValueError('Your invitation does not allow presentation submissions. Please contact Aabhash.')
    current = own(session, person['id'])
    if version != current.get('version', ''):
        raise ValueError('This presentation changed in another window. Reload the Agenda page before saving.')
    return person, current


def write(session, participant_id, proposal):
    data = {key:value for key,value in proposal.items() if key != 'files'}
    data.update(version=secrets.token_hex(16), updated_at=seminar.now())
    session.merge(Presentation(participant_id=participant_id, data=data))
    return own(session, participant_id)


def save(store, token, proposal, version='', remove=False):
    with store.session(write=True) as session:
        person, current = editable(store, session, token, version)
        if remove:
            session.execute(delete(PresentationFile).where(PresentationFile.participant_id == person['id']))
            session.execute(delete(Presentation).where(Presentation.participant_id == person['id']))
            return {}
        if not isinstance(proposal, dict):
            raise ValueError('Enter your presentation details.')
        data = {}
        visibility = proposal.get('visibility', 'internal')
        if visibility not in ('internal', 'public'):
            raise ValueError('Choose Internal or Public for your talk.')
        data['visibility'] = visibility
        status = proposal.get('status', current.get('status', 'talk'))
        if status not in {'talk', 'placeholder'}:
            raise ValueError('Choose a talk or a provisional placeholder.')
        if status == 'placeholder' and visibility != 'internal':
            raise ValueError('Keep provisional placeholders internal until the topic is ready to share.')
        data['status'] = status
        for key, minimum, label in [('presentation_minutes', 1, 'Presentation'), ('questions_minutes', 0, 'Questions')]:
            value = proposal.get(key, current.get(key))
            if value is not None and (type(value) is not int or not minimum <= value <= 480):
                raise ValueError(f'{label} time must be a whole number from {minimum} to 480 minutes.')
            data[key] = value
        for key, limit in [('title', 200), ('abstract', 5000), ('explainer_url', 2048), ('slides_url', 2048)]:
            value = proposal.get(key, '')
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError(f'Keep {key.replace("_", " ")} under {limit:,} characters.')
            data[key] = value.strip()
        if not data['title']:
            raise ValueError('Enter a title for your talk.')
        for key in ('explainer_url', 'slides_url'):
            if not data[key]:
                continue
            url = urlparse(data[key])
            if url.scheme != 'https' or not url.hostname or url.username or url.password or any(c.isspace() for c in data[key]):
                raise ValueError('Use a full HTTPS link for presentation materials.')
        return write(session, person['id'], data)


def upload(store, token, kind, name, content, version):
    with store.session(write=True) as session:
        person, talk = editable(store, session, token, version)
        if not talk:
            raise ValueError('Save a talk title before uploading materials.')
        if kind not in ('explainer', 'slides') or not isinstance(name, str) or not 1 <= len(name) <= 180 or any(c in name for c in '/\\\r\n\x00'):
            raise ValueError('Choose a valid presentation filename.')
        if not 0 < len(content) <= MAX_FILE_SIZE:
            raise ValueError('Choose a file of at most 20 MB.')
        suffix = Path(name).suffix.lower()
        if suffix == '.pdf' and content.startswith(b'%PDF-'):
            mime = 'application/pdf'
        elif kind == 'explainer' and suffix in ('.html', '.htm'):
            try:
                text = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise ValueError('Upload a UTF-8 HTML explainer.') from None
            if '\x00' in text:
                raise ValueError('Upload a UTF-8 HTML explainer.')
            mime = 'text/html; charset=utf-8'
        elif kind == 'slides' and suffix == '.pptx' and zipfile.is_zipfile(io.BytesIO(content)):
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if not {'[Content_Types].xml', 'ppt/presentation.xml'} <= set(archive.namelist()):
                    raise ValueError('Upload a valid PowerPoint file.')
            mime = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        else:
            raise ValueError('Explainers accept HTML or PDF. Slides accept PDF or PPTX.')
        session.execute(delete(PresentationFile).where(PresentationFile.participant_id == person['id'], PresentationFile.kind == kind))
        session.add(PresentationFile(id=secrets.token_hex(24), participant_id=person['id'], kind=kind, name=name, mime=mime, content=content))
        return write(session, person['id'], talk)


def material(store, token, file_id):
    with store.session() as session:
        store.lookup(session, token)
        row = session.execute(select(PresentationFile.name, PresentationFile.mime, PresentationFile.content, Invitation.data)
                                 .join(Invitation, Invitation.id == PresentationFile.participant_id)
                                 .where(PresentationFile.id == file_id)).first()
        if not row:
            raise ValueError('This material is no longer available.')
        owner = row[3]
        if not owner['active'] or owner.get('role') not in PRESENTER_ROLES:
            raise ValueError('This material is no longer available.')
        return dict(name=row[0], mime=row[1], content=row[2])
