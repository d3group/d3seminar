import importlib.util
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import calendar_check as calendar

UTC = timezone.utc
BERLIN = ZoneInfo('Europe/Berlin')
PEOPLE = [dict(name='Person A', email='a@example.com'), dict(name='Person B', email='b@example.com')]


class CalendarCheckTests(unittest.TestCase):
    def test_only_explicitly_free_intervals_count_as_free(self):
        self.assertEqual(calendar.slot_status('0000', 0, 4), 'free')
        for values in ('0100', '0020', '0003'):
            self.assertEqual(calendar.slot_status(values, 0, 4), 'busy')
        for values in ('0040', '0050', '00?0', '000', ''):
            self.assertEqual(calendar.slot_status(values, 0, 4), 'unknown')
        self.assertEqual(calendar.slot_status('0000', -1, 4), 'unknown')
        self.assertEqual(calendar.slot_status('2000', 1, 3), 'free')

    def test_denied_missing_and_truncated_calendars_remain_unknown(self):
        for bad in (PermissionError(), SimpleNamespace(merged=None), SimpleNamespace(merged='000')):
            calendars, warnings = calendar.decode_views(PEOPLE, [SimpleNamespace(merged='0000'), bad], 4)
            self.assertEqual(calendars['a@example.com'], '0000')
            self.assertEqual(calendars['b@example.com'], '????')
            self.assertEqual(len(warnings), 1)
        with self.assertRaises(ValueError):
            calendar.decode_views(PEOPLE, [SimpleNamespace(merged='0000')], 4)

    def test_best_slots_first_and_conflicts_at_boundaries(self):
        day = date(2026, 11, 4)
        origin, end = calendar.window(day, day, BERLIN, time(9), time(12), 120)
        count = int((end - origin).total_seconds() // 900)
        busy = list('0' * count)
        nine = datetime.combine(day, time(9), BERLIN).astimezone(UTC)
        offset = int((nine - origin).total_seconds() // 900)
        busy[offset] = '2'
        slots = calendar.find_slots(PEOPLE, {'a@example.com': ''.join(busy), 'b@example.com': '0' * count},
                                    origin, day, day, BERLIN, time(9), time(12), 120)
        self.assertEqual(len(slots), 5)
        self.assertEqual(slots[0]['start'], '2026-11-04T09:15:00+01:00')
        self.assertEqual(slots[0]['free'], 2)
        self.assertEqual(slots[-1]['start'], '2026-11-04T09:00:00+01:00')
        self.assertEqual(slots[-1]['busy'], 1)
        self.assertEqual(slots[-1]['unknown'], 0)

    def test_missing_calendar_prevents_unanimous_free_result(self):
        day = date(2026, 11, 4)
        origin, _ = calendar.window(day, day, BERLIN, time(9), time(11), 120)
        slots = calendar.find_slots(PEOPLE, {'a@example.com': '0' * 96}, origin, day, day,
                                    BERLIN, time(9), time(11), 120)
        self.assertEqual((slots[0]['free'], slots[0]['unknown']), (1, 1))

    def test_dst_utc_offsets_weekends_and_excluded_dates(self):
        for first, last, before, after in [(date(2026, 10, 23), date(2026, 10, 26), '+02:00', '+01:00'),
                                           (date(2027, 3, 26), date(2027, 3, 29), '+01:00', '+02:00')]:
            origin, end = calendar.window(first, last, BERLIN, time(9), time(11), 120)
            count = int((end - origin).total_seconds() // 900)
            busy = list('0' * count)
            monday = datetime.combine(last, time(9), BERLIN).astimezone(UTC)
            busy[int((monday - origin).total_seconds() // 900)] = '2'
            calendars = {p['email']: ''.join(busy) for p in PEOPLE}
            slots = calendar.find_slots(PEOPLE, calendars, origin, first, last, BERLIN, time(9), time(11), 120)
            self.assertEqual(len(slots), 2)
            self.assertTrue(slots[0]['start'].endswith(before))
            self.assertTrue(slots[1]['start'].endswith(after))
            self.assertEqual([s['free'] for s in slots], [2, 0])
            self.assertEqual(len(calendar.find_slots(PEOPLE, calendars, origin, first, last,
                                                    BERLIN, time(9), time(11), 120, [last])), 1)

    def test_invalid_windows_and_duplicate_people(self):
        day = date(2026, 11, 4)
        for end, start_hour, end_hour, duration in [(day - timedelta(days=1), time(9), time(17), 120),
                (day + timedelta(days=31), time(9), time(17), 120), (day, time(9, 1), time(17), 120),
                (day, time(17), time(9), 120), (day, time(9), time(10), 120), (day, time(9), time(17), 0)]:
            with self.assertRaises(ValueError):
                calendar.window(day, end, BERLIN, start_hour, end_hour, duration)
        with self.assertRaises(ValueError):
            calendar.validate_people(PEOPLE + [PEOPLE[0]])
        with self.assertRaises(ValueError):
            calendar.validate_people([])

    def test_setup_copies_only_professor_names_emails_and_username(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roster = root / 'roster.csv'
            roster.write_text('name,email,role,token\nPerson A,a@example.com,Professor,private-token\nPerson B,b@example.com,PhD,other-token\n')
            with patch.object(calendar, 'PRIVATE', root / 'calendar'):
                calendar.setup(roster, 'example-user')
                path = root / 'calendar/settings.json'
                settings = json.loads(path.read_text())
                self.assertEqual(settings['people'], [PEOPLE[0]])
                self.assertNotIn('token', path.read_text())
                self.assertNotIn('password', settings)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(ValueError):
                    calendar.setup(roster, 'example-user')

    def test_report_retains_unknown_status_and_stays_private(self):
        day = date(2026, 11, 4)
        origin, _ = calendar.window(day, day, BERLIN, time(9), time(11), 120)
        slots = calendar.find_slots(PEOPLE, {'a@example.com': '0' * 96}, origin, day, day,
                                    BERLIN, time(9), time(11), 120)
        args = SimpleNamespace(start=day, end=day, duration=120, work_start=time(9), work_end=time(11), exclude_date=[])
        settings = dict(people=PEOPLE, timezone='Europe/Berlin', username='private-login')
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            root = Path(directory)
            with patch.object(calendar, 'PRIVATE', root):
                calendar.write_report(settings, slots, ['Person B: unknown'], args)
            self.assertIn('0 candidate start time(s) with all 2 calendars free', output.getvalue())
            report = next(root.glob('*.json'))
            self.assertNotIn('private-login', report.read_text())
            self.assertEqual(json.loads(report.read_text())['slots'][0]['unknown'], 1)
            csv_path = next(root.glob('*.csv'))
            self.assertIn('free,unknown', csv_path.read_text())
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(csv_path.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(importlib.util.find_spec('exchangelib'), 'Install requirements-calendar.txt for the EWS adapter test')
    def test_ews_requests_only_merged_free_busy_at_fixed_endpoint(self):
        from exchangelib.protocol import Protocol
        from exchangelib.properties import FreeBusyView
        start, end = datetime(2026, 11, 4, tzinfo=UTC), datetime(2026, 11, 5, tzinfo=UTC)
        settings = dict(username='example-user', people=PEOPLE)
        views = [FreeBusyView(view_type='MergedOnly', merged='0' * 96) for _ in PEOPLE]
        with patch.object(Protocol, 'get_free_busy_info', autospec=True, return_value=iter(views)) as fetch:
            calendars, warnings = calendar.fetch_availability(settings, start, end, 'test-password')
        self.assertFalse(warnings)
        self.assertEqual(calendars['a@example.com'], '0' * 96)
        protocol = fetch.call_args.args[0]
        self.assertEqual(protocol.service_endpoint, calendar.ENDPOINT)
        self.assertIsNone(protocol.config.credentials)
        self.assertEqual(fetch.call_args.kwargs['requested_view'], 'MergedOnly')
        self.assertEqual(fetch.call_args.kwargs['merged_free_busy_interval'], 15)
        self.assertEqual(fetch.call_args.kwargs['accounts'], [('a@example.com', 'Required', False), ('b@example.com', 'Required', False)])

    @unittest.skipUnless(importlib.util.find_spec('exchangelib'), 'Install requirements-calendar.txt for the EWS adapter test')
    def test_exchange_failure_does_not_print_server_details(self):
        from exchangelib.protocol import Protocol
        start, end = datetime(2026, 11, 4, tzinfo=UTC), datetime(2026, 11, 5, tzinfo=UTC)
        with patch.object(Protocol, 'get_free_busy_info', side_effect=RuntimeError('private-server-response')):
            with self.assertRaises(ValueError) as failure:
                calendar.fetch_availability(dict(username='example-user', people=PEOPLE), start, end, 'test-password')
        self.assertNotIn('private-server-response', str(failure.exception))
        self.assertIn('RuntimeError', str(failure.exception))


if __name__ == '__main__':
    unittest.main()
