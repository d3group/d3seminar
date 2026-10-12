"""Archive retired conditional answers and remove obsolete date proposals once."""

import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = '0002_retire_conditional'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    invitations = sa.table('invitations', sa.column('id', sa.Text), sa.column('data', sa.Text))
    poll_state = sa.table('poll_state', sa.column('id', sa.Integer), sa.column('data', sa.Text))
    history = sa.table('history', sa.column('saved_at', sa.Text), sa.column('data', sa.Text))
    connection = op.get_bind()
    records = [(key, json.loads(data)) for key, data in connection.execute(sa.select(invitations).order_by(invitations.c.id))]
    data = connection.scalar(sa.select(poll_state.c.data).where(poll_state.c.id == 1))
    state = json.loads(data) if data else {}
    affected = [(key, record) for key, record in records if 'if_needed' in record.get('answers', {}).values()]
    retired = {'proposed_slot_id', 'proposal_id'}
    if not affected and not retired.intersection(state):
        return
    connection.execute(history.insert().values(saved_at=datetime.now(timezone.utc).isoformat(),
        data=json.dumps(dict(records=[record for _, record in records], state=state))))
    for key, record in affected:
        record['answers'] = {key:value for key,value in record['answers'].items() if value != 'if_needed'}
        connection.execute(invitations.update().where(invitations.c.id == key).values(data=json.dumps(record)))
    if retired.intersection(state):
        connection.execute(poll_state.update().where(poll_state.c.id == 1).values(
            data=json.dumps({key:value for key,value in state.items() if key not in retired})))


def downgrade():
    raise ValueError('Retired answers cannot be restored automatically; use the archived history or a verified snapshot.')
