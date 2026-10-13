"""Plain-text email drafts with a small set of personalized placeholders."""

import re
from string import Formatter


TEMPLATES = {
    'initial': ('Help plan our first D3 seminar', 'Please enter your availability for our first D3 seminar so we can choose a time when everyone can attend.'),
    'invite': ('Find a time for our seminar', 'Please mark your availability for each option. We are looking for one time when everyone can attend.'),
    'remind': ('Reminder: your seminar availability', 'Please finish marking your availability so we can find a time for everyone.'),
    'present': ('Bring your research to our next seminar', 'Please add a talk for our next seminar. Work in progress, an early result, or a question you would like feedback on is welcome.'),
    'confirm': ('Our seminar time is confirmed', 'Everyone has confirmed their availability. Our seminar will take place at the time below.'),
}
FIELDS = {
    'name':'Full name', 'first_name':'First name', 'event_name':'Event name', 'semester':'Semester',
    'schedule':'Candidate dates or confirmed schedule', 'location':'Location', 'deadline':'Reply deadline',
    'invitation_link':'Personal invitation', 'programme_link':'All events', 'presentation_link':'Add a talk',
    'later_options':'Later seminar links', 'onsite_note':'Onsite attendance note',
    'sender_name':'Sender name', 'sender_email':'Sender email',
}
SUBJECT_FIELDS = {'name', 'first_name', 'event_name', 'semester', 'sender_name', 'sender_email'}


def sender_display_name(kind, name):
    return 'Organizer D3 Seminar' if kind == 'initial' else name


def default_template(kind, config):
    if kind not in TEMPLATES:
        raise ValueError('Choose a supported email type.')
    subject, introduction = TEMPLATES[kind]
    if config.get('kind') == 'onsite':
        subject, introduction = subject.replace('seminar', 'onsite meeting'), introduction.replace('seminar', 'onsite meeting')
        if kind != 'confirm':
            introduction += '{onsite_note}'
    body = ('Hello {name},\n\n' + introduction + '\n\n'
            '{event_name} · {semester}\n{schedule}\n{location}\n\n')
    if kind not in {'present', 'confirm'}:
        body += 'Please set your preferences by {deadline}.\n\n'
    if kind == 'present':
        body += ('Start with a title; your abstract, explainer and slides can follow later.\n'
                 'Add your presentation:\n{presentation_link}\n\n{later_options}\n\n'
                 'Plan to contribute at least once across the three seminars and at the onsite meeting.\n\n')
    else:
        body += 'Open your personal invitation:\n{invitation_link}\n\n'
    if kind == 'initial':
        body += ('If you can, also enter your availability for the other seminars and the onsite. This is optional, but helps us plan ahead.\n'
                 'View all events:\n{programme_link}\n\n')
    body += 'Best,\n{sender_name}\n\nPlease send any comments or suggestions to {sender_email}.'
    return dict(subject=subject + ' | {event_name} · {semester}', body=body)


def required_fields(kind):
    required = {'presentation_link'} if kind == 'present' else {'invitation_link'}
    if kind in {'initial', 'invite', 'remind'}:
        required.add('deadline')
    if kind == 'confirm':
        required.add('schedule')
    return required


def validate(draft, kind):
    if not isinstance(draft, dict) or set(draft) != {'subject', 'body'} or kind not in TEMPLATES:
        raise ValueError('Provide an email subject and message.')
    for field, limit, allowed in [('subject', 250, SUBJECT_FIELDS), ('body', 20000, set(FIELDS))]:
        value = draft[field]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f'The email {field} must contain 1–{limit} characters.')
        if any(ord(c) < 32 and (field == 'subject' or c not in '\n\t') or ord(c) == 127 for c in value):
            raise ValueError('Use a single-line subject and plain text in the message.')
        if re.search(r'token=[A-Za-z0-9_-]{43}', value):
            raise ValueError('Use a personal-link placeholder instead of pasting an invitation token.')
        used = set()
        try:
            for _, name, spec, conversion in Formatter().parse(value):
                if name is None:
                    continue
                if name not in allowed or spec or conversion:
                    raise ValueError
                used.add(name)
        except ValueError:
            raise ValueError(f'Use only the listed placeholders in the {field}, with single braces.') from None
        if field == 'body' and (missing := required_fields(kind) - used):
            raise ValueError('Keep these placeholders in the message: ' + ', '.join('{' + name + '}' for name in sorted(missing)))


def render(draft, kind, values):
    validate(draft, kind)
    subject, body = draft['subject'].format_map(values), draft['body'].format_map(values)
    if any(ord(c) < 32 or ord(c) == 127 for c in subject):
        raise ValueError('The personalized subject must be a single line.')
    return subject, body
