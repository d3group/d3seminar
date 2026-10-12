"""Organiser edits and email delivery records."""

import copy
import fcntl
import json
import re
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import seminar
import presentations


def local_datetime(value, zone):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', value):
        raise ValueError('Enter a date and time in the event timezone.')
    naive = datetime.fromisoformat(value)
    first = naive.replace(tzinfo=ZoneInfo(zone), fold=0)
    second = first.replace(fold=1)
    if first.astimezone(timezone.utc).astimezone(first.tzinfo).replace(tzinfo=None) != naive or first.utcoffset() != second.utcoffset():
        raise ValueError('This clock time is ambiguous or does not exist because of daylight saving. Choose another time.')
    return first


def write_config(path, config):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


class Organizer:
    def __init__(self, config, stores, config_file, private):
        self.config, self.stores, self.config_file, self.private = config, stores, config_file, private
        with (private / 'command.lock').open('a'):
            pass
        self.journal = private / 'pending-edit.json'
        if self.journal.exists():
            with self.lock():
                edit = seminar.read_json(self.journal)
                poll = stores[edit['event_id']].dashboard()['poll']
                version = 'after' if seminar.event_revision(poll) == edit['revision'] else 'before'
                self.write_edit(edit, version)
                self.reload_config()
                self.journal.unlink()

    @contextmanager
    def lock(self):
        with (self.private / 'command.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another organiser action is running. Please wait and try again.') from None
            yield

    def directory(self, event_id):
        return self.stores[event_id].database.parent

    def reload_config(self):
        replacement = seminar.read_json(self.config_file)
        if not isinstance(replacement, dict) or not isinstance(replacement.get('test_mode'), bool):
            raise ValueError('Set test_mode explicitly to true or false.')
        events = replacement.get('events')
        if not isinstance(events, list) or not events:
            raise ValueError('Provide at least one event in the programme.')
        for event in events:
            seminar.validate_poll(seminar.poll_data(seminar.event_config(replacement, event['id'])))
        local = {key:self.config[key] for key in ('site_url', 'api_url', 'backend_url') if key in self.config}
        replacement.update(local)
        seminar.validate_site_url(replacement['site_url'])
        if replacement.get('api_url'):
            seminar.validate_api_url(replacement['api_url'])
        self.config = replacement

    def records(self, event_id):
        records = seminar.read_json(self.directory(event_id) / 'invitations.json')
        current = {r['id']:r for r in self.stores[event_id].responses()}
        if set(current) != {r['id'] for r in records} or any(
            current[r['id']]['revision'] != r['revision'] or current[r['id']]['token_hash'] != seminar.digest(r['token']) for r in records):
            raise ValueError('Invitations and saved responses differ. Prepare and sync the event before continuing.')
        return records

    def preview_schedule(self, event_id, proposal):
        dashboard = self.stores[event_id].dashboard()
        if not dashboard['poll']:
            raise ValueError('Register the event before editing its dates.')
        if dashboard['state'].get('final_event'):
            raise ValueError('This event is finalized. Its dates cannot be changed.')
        poll = copy.deepcopy(dashboard['poll'])
        zone = poll['timezone']
        poll['deadline'] = local_datetime(proposal['deadline'], zone).isoformat()
        options = proposal['options']
        if not isinstance(options, list) or not 2 <= len(options) <= 12:
            raise ValueError('Provide 2–12 candidate times.')
        poll['slots'] = []
        for option in options:
            value = option['start']
            if poll.get('daily_hours'):
                value += 'T' + poll['daily_hours']['start']
            start = local_datetime(value, zone)
            if poll.get('daily_hours'):
                last = start.date() + timedelta(days=poll['duration_days'] - 1)
                hours = poll['daily_hours']
                end = datetime.combine(last, time.fromisoformat(hours.get('last_day_end', hours['end'])), tzinfo=start.tzinfo)
            else:
                end = (start.astimezone(timezone.utc) + timedelta(hours=2)).astimezone(start.tzinfo)
            key = option.get('id') or 'option-' + seminar.digest(start.isoformat())[:12]
            poll['slots'].append(dict(id=key, start=start.isoformat(), end=end.isoformat()))
        seminar.validate_poll(poll)
        old_slots = dashboard['poll']['slots']
        resets = any(slot not in poll['slots'] for slot in old_slots) and any(slot not in old_slots for slot in poll['slots'])
        return dict(poll=poll, resets_answers=resets, preview=seminar.event_revision(dict(poll=poll, snapshot=dashboard['snapshot'])),
                    options=[seminar.when(s, zone, poll.get('daily_hours')) for s in poll['slots']])

    def write_edit(self, edit, version):
        write_config(self.config_file, edit[version]['config'])
        seminar.save_private(self.directory(edit['event_id']) / 'invitations.json', edit[version]['invitations'])

    def save_schedule(self, event_id, proposal, preview, confirm_reset):
        with self.lock():
            checked = self.preview_schedule(event_id, proposal)
            if checked['preview'] != preview:
                raise ValueError('The dates or responses changed. Preview the changes again.')
            if checked['resets_answers'] and confirm_reset is not True:
                raise ValueError('Confirm that you want to start a new round before changing candidate dates.')
            poll, store = checked['poll'], self.stores[event_id]
            invitations = self.records(event_id)
            config = seminar.read_json(self.config_file)
            updated_config, updated_invitations = copy.deepcopy(config), copy.deepcopy(invitations)
            event = next(e for e in updated_config['events'] if e['id'] == event_id)
            event.update(slots=poll['slots'], deadline=poll['deadline'])
            revision = seminar.event_revision(poll)
            if all(r['revision'] == revision for r in invitations):
                return dict(resets_answers=False)
            for invite in updated_invitations:
                old_revision = invite['revision']
                unchanged_options = poll['slots'] == invite['poll']['slots']
                invite.update(poll=poll, revision=revision)
                if unchanged_options:
                    for kind in ('invite', 'remind', 'present'):
                        old_key = kind + ':' + old_revision
                        if old_key in invite['deliveries']:
                            invite['deliveries'][kind + ':' + revision] = invite['deliveries'][old_key]
            edit = dict(event_id=event_id, revision=revision, before=dict(config=config, invitations=invitations),
                        after=dict(config=updated_config, invitations=updated_invitations))
            with store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                dashboard = store.dashboard(connection)
                if seminar.event_revision(dict(poll=poll, snapshot=dashboard['snapshot'])) != preview:
                    raise ValueError('Responses changed. Preview the changes again.')
                records, state = store.records(connection), store.state(connection)
                connection.execute('INSERT INTO history VALUES (?, ?)', (seminar.now(), json.dumps(dict(records=records, state=state))))
                slot_ids = {slot['id'] for slot in poll['slots']}
                for record in records:
                    if not record['active']:
                        continue
                    record.update(poll=poll, revision=revision)
                    if checked['resets_answers']:
                        record.update(answers={}, comment='', updated_at='')
                    else:
                        record['answers'] = {key:value for key,value in record['answers'].items() if key in slot_ids}
                    connection.execute('UPDATE invitations SET data=? WHERE id=?', (json.dumps(record), record['id']))
                if checked['resets_answers']:
                    store.write_state(connection, {key:value for key,value in state.items() if key.startswith('programme')})
                seminar.save_private(self.journal, edit)
                try:
                    self.write_edit(edit, 'after')
                except OSError:
                    self.write_edit(edit, 'before')
                    self.journal.unlink()
                    raise
            self.reload_config()
            self.journal.unlink()
            return dict(resets_answers=checked['resets_answers'])

    def save_programme(self, event_id, programme, snapshot):
        fields = {'topic':300, 'presenters':500, 'room':300, 'agenda':2000}
        if not isinstance(programme, dict) or set(programme) != set(fields):
            raise ValueError('Provide a topic, presenters, room and agenda.')
        for key, limit in fields.items():
            value = programme[key]
            if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
                raise ValueError('Check the programme text and its length.')
        with self.lock(), self.stores[event_id].connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            store = self.stores[event_id]
            if store.dashboard(connection)['snapshot'] != snapshot:
                raise ValueError('The event changed. Refresh responses before saving its programme.')
            state = store.state(connection)
            state.update(programme={key:value.strip() for key,value in programme.items()}, programme_updated_at=seminar.now())
            store.write_state(connection, state)

    def upcoming_seminars(self):
        now = datetime.fromisoformat(seminar.now())
        events = []
        for entry in self.config['events']:
            if entry.get('kind') != 'seminar' or entry['id'] not in self.stores:
                continue
            dashboard = self.stores[entry['id']].dashboard()
            poll = dashboard['poll']
            if not poll:
                continue
            final = dashboard['state'].get('final_event')
            slots = [final] if final else poll['slots']
            remaining = [s for s in slots if datetime.fromisoformat(s['end']) > now]
            if remaining:
                events.append((min(s['start'] for s in remaining), entry))
        return [entry for _, entry in sorted(events, key=lambda item:datetime.fromisoformat(item[0]))]

    def mail_queue(self, event_id, kind):
        if kind not in {'invite', 'remind', 'present', 'confirm'}:
            raise ValueError('Choose a supported email type.')
        upcoming = self.upcoming_seminars()
        if event_id == 'next':
            if not upcoming:
                raise ValueError('There are no upcoming seminars.')
            event_id = upcoming[0]['id']
        config = seminar.event_config(self.config, event_id)
        local = seminar.read_json(self.private / 'settings.json')
        invitations = self.records(event_id)
        if config['test_mode'] and any(r['email'] != local['test_recipient'] for r in invitations):
            raise ValueError('Test mode allows only your test address. No email was sent.')
        dashboard = self.stores[event_id].dashboard()
        config.update(dashboard['poll'] or {})
        responses = {p['id']:p for p in dashboard['participants']}
        index = next((i for i,e in enumerate(upcoming) if e['id'] == event_id), None)
        later = upcoming[index+1:] if index is not None else []
        planned, registered = set(), {}
        if kind == 'present':
            for entry in [{'id':event_id}, *later]:
                store = self.stores[entry['id']]
                with store.connect() as connection:
                    for person in store.records(connection):
                        if not person['active']:
                            continue
                        registered.setdefault(person['email'], set()).add(entry['id'])
                        if presentations.own(connection, person['id']).get('title'):
                            planned.add(person['email'])
        messages = []
        for invite in invitations:
            key = seminar.eligible(invite, responses.get(invite['id']), kind, dashboard['state'])
            if not key or kind == 'present' and invite['email'] in planned:
                continue
            alternatives = [e for e in later if e['id'] in registered.get(invite['email'], set())]
            mail = seminar.message(config, local, invite, kind, dashboard['state'], alternatives)
            row = dict(id=invite['id'], key=key, to=invite['email'], name=invite['name'], subject=str(mail['Subject']),
                       body=mail.get_body(preferencelist=('plain',)).get_content(),
                       html=mail.get_body(preferencelist=('html',)).get_content())
            row['attachments'] = [dict(name=part.get_filename(), content=part.get_payload(decode=True).decode()) for part in mail.iter_attachments()]
            row['preview'] = seminar.event_revision(dict(message=row, sender=local['sender_email'], sender_name=local['sender_name']))
            messages.append(row)
        return dict(event_id=event_id, event_name=config.get('name', config['title']), kind=kind, messages=messages,
                    sender=local['sender_email'], sender_name=local['sender_name'])

    def preview_mail(self, event_id, kind):
        with self.lock():
            return self.mail_queue(event_id, kind)

    def claim_mail(self, event_id, kind, participant_id, preview, message_id):
        if not isinstance(message_id, str) or not re.fullmatch(r'<[^<>\s]{1,250}>', message_id):
            raise ValueError('Invalid message identifier.')
        with self.lock():
            queue = self.mail_queue(event_id, kind)
            row = next((m for m in queue['messages'] if m['id'] == participant_id), None)
            if not row:
                return dict(claimed=False)
            if row['preview'] != preview:
                raise ValueError('The email details changed. Preview the emails again before sending.')
            records = self.records(event_id)
            invite = next(r for r in records if r['id'] == participant_id)
            invite['deliveries'][row['key']] = dict(status='attempted', at=seminar.now(), message_id=message_id)
            seminar.save_private(self.directory(event_id) / 'invitations.json', records)
            return dict(claimed=True)

    def finish_mail(self, event_id, participant_id, key, message_id):
        with self.lock():
            records = self.records(event_id)
            invite = next((r for r in records if r['id'] == participant_id), None)
            delivery = invite.get('deliveries', {}).get(key) if invite else None
            if not delivery or delivery.get('message_id') != message_id:
                raise ValueError('This email attempt does not match the delivery record.')
            delivery.update(status='sent', sent_at=seminar.now())
            seminar.save_private(self.directory(event_id) / 'invitations.json', records)
