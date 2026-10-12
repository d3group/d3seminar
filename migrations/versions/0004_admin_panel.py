"""Archive users without deleting their work and retain approved email batches."""

from alembic import op
import sqlalchemy as sa

revision = '0004_admin_panel'
down_revision = '0003_organizers'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('is_archived', sa.Boolean, nullable=False, server_default='0'))
    op.create_table('mail_batches', sa.Column('id', sa.Text, primary_key=True), sa.Column('data', sa.Text, nullable=False))


def downgrade():
    op.drop_table('mail_batches')
    op.drop_column('users', 'is_archived')
