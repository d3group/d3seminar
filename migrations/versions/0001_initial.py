"""Adopt the existing seminar tables or create them for a new installation."""

from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    schema = sa.MetaData()
    sa.Table('invitations', schema,
             sa.Column('id', sa.Text, primary_key=True, nullable=True),
             sa.Column('token_hash', sa.Text, nullable=False, unique=True), sa.Column('data', sa.Text, nullable=False))
    sa.Table('poll_state', schema,
             sa.Column('id', sa.Integer, primary_key=True, nullable=True),
             sa.Column('data', sa.Text, nullable=False), sa.CheckConstraint('id=1'))
    sa.Table('history', schema, sa.Column('saved_at', sa.Text, nullable=False), sa.Column('data', sa.Text, nullable=False))
    sa.Table('presentations', schema,
             sa.Column('participant_id', sa.Text, primary_key=True, nullable=True), sa.Column('data', sa.Text, nullable=False))
    sa.Table('presentation_files', schema,
             sa.Column('id', sa.Text, primary_key=True, nullable=True), sa.Column('participant_id', sa.Text, nullable=False),
             sa.Column('kind', sa.Text, nullable=False), sa.Column('name', sa.Text, nullable=False),
             sa.Column('mime', sa.Text, nullable=False), sa.Column('content', sa.LargeBinary, nullable=False),
             sa.UniqueConstraint('participant_id', 'kind'))
    sa.Table('users', schema,
             sa.Column('id', sa.Text, primary_key=True), sa.Column('name', sa.Text, nullable=False),
             sa.Column('email', sa.Text(collation='NOCASE'), unique=True), sa.Column('role', sa.Text, nullable=False),
             sa.Column('token', sa.Text, nullable=False, unique=True), sa.Column('role_status', sa.Text, nullable=False))

    connection = op.get_bind()
    inspector = sa.inspect(connection)
    for table in schema.tables.values():
        if not inspector.has_table(table.name):
            continue
        columns = inspector.get_columns(table.name)
        expected = [(c.name, c.type.compile(connection.dialect).split(' COLLATE ')[0], c.nullable) for c in table.columns]
        actual = [(c['name'], c['type'].compile(connection.dialect), c['nullable']) for c in columns]
        primary = inspector.get_pk_constraint(table.name)['constrained_columns']
        unique = {tuple(c['column_names']) for c in inspector.get_unique_constraints(table.name)}
        required = {tuple(c.name for c in constraint.columns) for constraint in table.constraints if isinstance(constraint, sa.UniqueConstraint)}
        checks = {c['sqltext'].replace(' ', '') for c in inspector.get_check_constraints(table.name)}
        required_checks = {str(c.sqltext).replace(' ', '') for c in table.constraints if isinstance(c, sa.CheckConstraint)}
        if actual != expected or primary != [c.name for c in table.primary_key] or not required <= unique or not required_checks <= checks:
            raise ValueError(f'Unrecognized schema for {table.name}; migration stopped without changing existing data.')
    schema.create_all(connection)


def downgrade():
    raise ValueError('The initial schema cannot be dropped automatically; restore a verified snapshot instead.')
