"""Keep organizer permissions separate from academic roles."""

from alembic import op
import sqlalchemy as sa

revision = '0003_organizers'
down_revision = '0002_retire_conditional'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('is_organizer', sa.Boolean, nullable=False, server_default='0'))


def downgrade():
    op.drop_column('users', 'is_organizer')
