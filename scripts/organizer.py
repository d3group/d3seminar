"""Local organiser edits and reviewed email delivery."""

import copy
import fcntl
import json
import re
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import seminar


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
        local = {key:self.config[key] for key in ('site_url', 'backend_url')}
        self.config.clear()
        self.config.update(seminar.read_json(self.config_file), **local)

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
        resets = any(slot not in poll['slots'] for slot in dashboard['poll']['slots'])
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
                    for kind in ('invite', 'remind'):
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
                for record in records:
                    if not record['active']:
                        continue
                    record.update(poll=poll, revision=revision)
                    if checked['resets_answers']:
                        record.update(answers={}, comment='', updated_at='')
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

    def mail_queue(self, event_id, kind):
        if kind not in {'invite', 'remind', 'confirm'}:
            raise ValueError('Choose a supported email type.')
        config = seminar.event_config(self.config, event_id)
        local = seminar.read_json(self.private / 'settings.json')
        invitations = self.records(event_id)
        if config['test_mode'] and any(r['email'] != local['test_recipient'] for r in invitations):
            raise ValueError('Test mode allows only your test address. No email was sent.')
        dashboard = self.stores[event_id].dashboard()
        responses = {p['id']:p for p in dashboard['participants']}
        messages = []
        for invite in invitations:
            key = seminar.eligible(invite, responses.get(invite['id']), kind, dashboard['state'])
            if key:
                mail = seminar.message(config, local, invite, kind, dashboard['state'])
                messages.append((invite, key, mail))
        visible = [dict(to=invite['email'], name=invite['name'], subject=str(mail['Subject']),
                        body=mail.get_body(preferencelist=('plain',)).get_content()) for invite, key, mail in messages]
        preview = seminar.event_revision(dict(kind=kind, messages=visible, sender=local['sender_email'],
                                             keys=[key for invite,key,mail in messages], state=dashboard['state']))
        return dict(preview=preview, messages=visible, sender=local['sender_email']), (config, local, invitations, messages)

    def preview_mail(self, event_id, kind):
        with self.lock():
            return self.mail_queue(event_id, kind)[0]

    def send_mail(self, event_id, kind, preview, password):
        if not isinstance(password, str) or not password:
            raise ValueError('Enter your university password to send the reviewed emails.')
        with self.lock():
            visible, (config, local, invitations, messages) = self.mail_queue(event_id, kind)
            if preview != visible['preview']:
                raise ValueError('Recipients or email details changed. Preview the emails again before sending.')
            if not messages:
                raise ValueError('There are no eligible emails to send.')
            if not local.get('smtp_username'):
                raise ValueError('Configure your university SMTP username first.')
            sent, skipped = 0, 0
            try:
                with seminar.smtplib.SMTP_SSL(local['smtp_host'], local['smtp_port'], context=seminar.ssl.create_default_context(), timeout=30) as smtp:
                    smtp.login(local['smtp_username'], password)
                    for invite, key, mail in messages:
                        dashboard = self.stores[event_id].dashboard()
                        person = next((p for p in dashboard['participants'] if p['id'] == invite['id']), None)
                        if seminar.eligible(invite, person, kind, dashboard['state']) != key:
                            skipped += 1
                            continue
                        outbox = self.directory(event_id) / 'outbox'
                        outbox.mkdir(exist_ok=True, mode=0o700)
                        path = outbox / f'{invite["id"]}-{kind}.eml'
                        path.write_bytes(bytes(mail)); path.chmod(0o600)
                        invite['deliveries'][key] = dict(status='attempted', at=seminar.now(), message_id=mail['Message-ID'])
                        seminar.save_private(self.directory(event_id) / 'invitations.json', invitations)
                        smtp.send_message(mail, from_addr=local['sender_email'], to_addrs=[invite['email']])
                        invite['deliveries'][key]['status'] = 'sent'
                        seminar.save_private(self.directory(event_id) / 'invitations.json', invitations)
                        sent += 1
            except (OSError, seminar.smtplib.SMTPException):
                return dict(sent=sent, skipped=skipped, error='Sending stopped. Check your university login and inbox before retrying. Any attempted messages are excluded from automatic retries.')
            finally:
                password = None
            return dict(sent=sent, skipped=skipped)
