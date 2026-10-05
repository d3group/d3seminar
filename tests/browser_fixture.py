"""Isolated local browser-test backend; never uses the real participant database."""
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import seminar
from server import Store, create_server

with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory) / 'qa.sqlite3')
    config = seminar.read_json(seminar.ROOT / 'tests/fixtures/programme.json')
    if '--presentations' in sys.argv:
        for slot in config['slots']:
            slot['start'] = slot['start'][:11] + '14:00:00+02:00'
            slot['end'] = slot['end'][:11] + '16:00:00+02:00'
    if '--onsite-half-day' in sys.argv:
        event = next(event for event in config['events'] if event['id'] == 'onsite')
        event.update(duration_days=3, daily_hours=dict(start='09:00', end='17:00', last_day_end='13:00'), semester='Summer semester 2027')
        event['slots'] = [dict(id=f'option-{day}', start=f'2027-04-{day:02}T09:00:00+02:00', end=f'2027-04-{day+2:02}T13:00:00+02:00')
                          for day in [12, 13, 14, 19, 20, 21]]
    app = create_server(0, store=store, config=config, admin_secret='browser-test-only-' * 3)
    config['site_url'] = f'http://127.0.0.1:{app.server_address[1]}/'
    config['backend_url'] = config['site_url'] + 'api/respond'
    token = 'T' * 43
    seminar.save_private(store.database.parent / 'settings.json', dict(sender_name='Test Organizer', sender_email='organizer@example.com',
        smtp_username='test-user', smtp_host='smtp.invalid', smtp_port=465, test_recipient='browser-test@example.com'))
    for entry in config['events']:
        event = seminar.event_config(config, entry['id'])
        poll = seminar.poll_data(event)
        people = [dict(id='d'*24, name='Browser Test', role='student', email='browser-test@example.com', event_id=entry['id'],
                     token_hash=seminar.digest(token), revision=seminar.event_revision(poll), poll=poll, site_url=config['site_url'])]
        if '--presentations' in sys.argv:
            people.append(dict(people[0], id='e'*24, name='Professor Test', role='professor', email='professor@example.com', token_hash=seminar.digest('P'*43)))
        app.stores[entry['id']].sync(people)
        seminar.save_private(app.stores[entry['id']].database.parent / 'invitations.json', [dict(id='d'*24, name='Browser Test', role='student',
            email='browser-test@example.com', token=token, revision=seminar.event_revision(poll), poll=poll, deliveries={})])
    fragment = '#' + urlencode(dict(token=token, slot='test-session', revision='legacy'))
    print(json.dumps(dict(base=config['site_url'], home=config['site_url'] + fragment,
                          invitation=config['site_url'] + 'event.html?event=seminar-1' + fragment,
                          onsite=config['site_url'] + 'event.html?event=onsite' + fragment,
                          admin=config['site_url'] + 'admin?event=seminar-1#' + urlencode(dict(key=app.admin_secret)))), flush=True)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.server_close()
