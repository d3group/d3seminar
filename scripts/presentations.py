import io
import json
from pathlib import Path
import secrets
from urllib.parse import urlparse
import zipfile

import seminar

MAX_FILE_SIZE = 20 * 1024 * 1024
PRESENTER_ROLES = {'student', 'professor'}
MATERIAL_POLICY = "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'"


def own(connection, participant_id):
    row = connection.execute('SELECT data FROM presentations WHERE participant_id=?', (participant_id,)).fetchone()
    if not row:
        return {}
    result = json.loads(row[0])
    result.setdefault('visibility', 'internal')
    result.setdefault('status', 'talk')
    result.setdefault('presentation_minutes', None)
    result.setdefault('questions_minutes', None)
    result['files'] = {kind:dict(id=key, name=name, mime=mime, size=size) for key, kind, name, mime, size in connection.execute(
        'SELECT id, kind, name, mime, length(content) FROM presentation_files WHERE participant_id=?', (participant_id,))}
    return result


def listing(store, connection, viewer_id=None):
    people = sorted(store.records(connection), key=lambda person: (person['name'].casefold(), person['id']))
    result = []
    for person in people:
        if not person['active'] or person.get('role') not in PRESENTER_ROLES:
            continue
        talk = own(connection, person['id'])
        if talk and (viewer_id is not None or talk['visibility'] == 'public'):
            row = dict(name=person['name'])
            if viewer_id is not None:
                row.update({key:talk[key] for key in ('title', 'abstract', 'visibility', 'status', 'presentation_minutes', 'questions_minutes', 'explainer_url', 'slides_url', 'files')})
                row['mine'] = person['id'] == viewer_id
            result.append(row)
    return result


def editable(store, connection, token, version):
    person = store.lookup(connection, token)
    if person.get('role') not in PRESENTER_ROLES:
        raise ValueError('Your invitation does not allow presentation submissions. Please contact Aabhash.')
    current = own(connection, person['id'])
    if version != current.get('version', ''):
        raise ValueError('This presentation changed in another window. Reload the Agenda page before saving.')
    return person, current


def write(connection, participant_id, proposal):
    data = {key:value for key,value in proposal.items() if key != 'files'}
    data.update(version=secrets.token_hex(16), updated_at=seminar.now())
    connection.execute('INSERT INTO presentations VALUES (?, ?) ON CONFLICT(participant_id) DO UPDATE SET data=excluded.data',
                       (participant_id, json.dumps(data)))
    return own(connection, participant_id)


def save(store, token, proposal, version='', remove=False):
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        person, current = editable(store, connection, token, version)
        if remove:
            connection.execute('DELETE FROM presentation_files WHERE participant_id=?', (person['id'],))
            connection.execute('DELETE FROM presentations WHERE participant_id=?', (person['id'],))
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
        return write(connection, person['id'], data)


def upload(store, token, kind, name, content, version):
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        person, talk = editable(store, connection, token, version)
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
        connection.execute('DELETE FROM presentation_files WHERE participant_id=? AND kind=?', (person['id'], kind))
        connection.execute('INSERT INTO presentation_files VALUES (?, ?, ?, ?, ?, ?)',
                           (secrets.token_hex(24), person['id'], kind, name, mime, content))
        return write(connection, person['id'], talk)


def material(store, token, file_id):
    with store.connect() as connection:
        connection.execute('BEGIN')
        store.lookup(connection, token)
        row = connection.execute('SELECT f.name, f.mime, f.content, i.data FROM presentation_files f JOIN invitations i ON i.id=f.participant_id WHERE f.id=?', (file_id,)).fetchone()
        if not row:
            raise ValueError('This material is no longer available.')
        owner = json.loads(row[3])
        if not owner['active'] or owner.get('role') not in PRESENTER_ROLES:
            raise ValueError('This material is no longer available.')
        return dict(name=row[0], mime=row[1], content=row[2])
