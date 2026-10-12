"""Organizer-reviewed batches for the on-demand local email sender."""

from collections import Counter
import re
import secrets

from sqlalchemy import select
from models import MailBatch, MailDraft
import seminar
import mail_templates


def draft(organizer, event_id, kind):
    event_id = organizer.mail_event_id(event_id, kind)
    config = seminar.event_config(organizer.config, event_id)
    default = mail_templates.default_template(kind, config)
    with organizer.stores['seminar-1'].session() as session:
        saved = session.get(MailDraft, event_id + ':' + kind)
        data = saved.data if saved else dict(default, updated_at=None)
    version = seminar.event_revision(dict(event_id=event_id, kind=kind, **data))
    return dict(event_id=event_id, event_name=config.get('name', config['title']), kind=kind, **data, version=version,
                default=default, fields=mail_templates.FIELDS, subject_fields=sorted(mail_templates.SUBJECT_FIELDS),
                required_fields=sorted(mail_templates.required_fields(kind)))


def save_draft(organizer, event_id, kind, content, version):
    mail_templates.validate(content, kind)
    with organizer.lock():
        current = draft(organizer, event_id, kind)
        if current['version'] != version:
            raise ValueError('This draft changed in another window. Reload it before saving.')
        data = dict(content, updated_at=seminar.now())
        with organizer.stores['seminar-1'].session(write=True) as session:
            session.merge(MailDraft(id=current['event_id'] + ':' + kind, data=data))
        return draft(organizer, current['event_id'], kind)


def example(organizer, event_id, kind, content):
    mail_templates.validate(content, kind)
    with organizer.lock():
        event_id = organizer.mail_event_id(event_id, kind)
        config = seminar.event_config(organizer.config, event_id)
        local = seminar.read_json(organizer.private / 'settings.json')
        dashboard = organizer.stores[event_id].dashboard()
        config.update(dashboard['poll'] or {})
        poll, state = seminar.poll_data(config), dashboard['state']
        provisional = kind == 'confirm' and not state.get('final_event')
        if provisional:
            state = seminar.programme_state(dict(state, final_event=seminar.event_data(config, poll['slots'][0])))
        later = []
        if kind == 'present':
            upcoming = organizer.upcoming_seminars()
            index = next((i for i, event in enumerate(upcoming) if event['id'] == event_id), None)
            later = upcoming[index+1:] if index is not None else []
        person = dict(name='Person A', email='person.a@example.org', role='student', token='example-link', poll=poll)
        mail = seminar.message_content(config, local, person, kind, state, later, content)
        return dict(mail, sender=local['sender_email'], sender_name=local['sender_name'], provisional=provisional,
                    attachments=[part['name'] for part in mail['attachments']])


def review(plan):
    messages = [dict(id=row['id'], name=row['name'], to=row['to'], subject=row['subject'],
                     body=re.sub(r'(?<=token=)[A-Za-z0-9_-]{43}', '[personal-link]', row['body']),
                     attachments=[part['name'] for part in row['attachments']]) for row in plan['messages']]
    version = seminar.event_revision(dict(event_id=plan['event_id'], kind=plan['kind'], messages=[m['preview'] for m in plan['messages']]))
    return dict(event_id=plan['event_id'], event_name=plan['event_name'], kind=plan['kind'], sender=plan['sender'],
                messages=messages, version=version)


def preview(organizer, event_id, kind, draft_version=None):
    with organizer.lock():
        if draft_version is not None and draft(organizer, event_id, kind)['version'] != draft_version:
            raise ValueError('This draft changed in another window. Reload it before previewing.')
        return review(organizer.mail_queue(event_id, kind))


def batches(organizer):
    with organizer.stores['seminar-1'].session() as session:
        return sorted(session.scalars(select(MailBatch.data)), key=lambda batch:(batch['created_at'], batch['id']))


def status(organizer, batch):
    error, plan = '', None
    try:
        plan = organizer.mail_queue(batch['event_id'], batch['kind'])
        current = {row['id']:row for row in plan['messages']}
    except ValueError as caught:
        error, current = str(caught), {}
    path = organizer.directory(batch['event_id']) / 'invitations.json'
    deliveries = {r['id']:r.get('deliveries', {}) for r in seminar.read_json(path)} if path.exists() else {}
    people = []
    for row in batch['recipients']:
        delivery = deliveries.get(row['id'], {}).get(row['key'], {})
        candidate = current.get(row['id'])
        state = delivery.get('status') or ('cancelled' if batch.get('cancelled') else 'review' if error else
            'skipped' if not candidate else 'ready' if candidate['preview']==row['preview'] else 'review')
        people.append(dict(id=row['id'], name=row['name'], to=row['to'], status=state))
    return dict(id=batch['id'], event_name=batch['event_name'], kind=batch['kind'], created_at=batch['created_at'],
                cancelled=batch.get('cancelled', False), recipients=people, counts=dict(Counter(p['status'] for p in people)), error=error), plan


def listing(organizer):
    with organizer.lock():
        return [status(organizer, batch)[0] for batch in reversed(batches(organizer))]


def enqueue(organizer, event_id, kind, version):
    with organizer.lock():
        plan = organizer.mail_queue(event_id, kind)
        if review(plan)['version'] != version:
            raise ValueError('Recipients or email details changed. Preview again before queuing.')
        if not plan['messages']:
            raise ValueError('There are no eligible emails to queue.')
        for batch in batches(organizer):
            if batch.get('cancelled') or (batch['event_id'], batch['kind']) != (plan['event_id'], kind):
                continue
            if batch['version'] == version:
                return batch['id']
            if status(organizer, batch)[0]['counts'].get('ready'):
                raise ValueError('Cancel the existing batch for this email type before queuing a new one.')
        batch_id = secrets.token_hex(12)
        data = dict(id=batch_id, event_id=plan['event_id'], event_name=plan['event_name'], kind=kind,
                    created_at=seminar.now(), version=version, cancelled=False,
                    recipients=[{key:row[key] for key in ('id', 'name', 'to', 'key', 'preview')} for row in plan['messages']])
        with organizer.stores['seminar-1'].session(write=True) as session:
            session.add(MailBatch(id=batch_id, data=data))
        return batch_id


def cancel(organizer, batch_id):
    with organizer.lock(), organizer.stores['seminar-1'].session(write=True) as session:
        batch = session.get(MailBatch, batch_id)
        if not batch:
            raise ValueError('Unknown email batch.')
        batch.data = dict(batch.data, cancelled=True)


def approved(organizer, batch_id, event_id, kind, participant_id, preview):
    with organizer.stores['seminar-1'].session() as session:
        batch = session.get(MailBatch, batch_id)
        if not batch or batch.data.get('cancelled'):
            return False
        data = batch.data
        return data['event_id']==event_id and data['kind']==kind and any(
            row['id']==participant_id and row['preview']==preview for row in data['recipients'])


def sender_preview(organizer):
    plans = []
    with organizer.lock():
        for batch in batches(organizer):
            overview, plan = status(organizer, batch)
            if not overview['counts'].get('ready'):
                continue
            ready = {row['id'] for row in overview['recipients'] if row['status']=='ready'}
            plans.append(dict(plan, batch_id=batch['id'], messages=[row for row in plan['messages'] if row['id'] in ready]))
    return plans
