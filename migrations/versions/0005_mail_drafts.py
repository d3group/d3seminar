"""Keep editable event email drafts in the private database."""

from alembic import op
import sqlalchemy as sa

revision = '0005_mail_drafts'
down_revision = '0004_admin_panel'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mail_drafts', sa.Column('id', sa.Text, primary_key=True), sa.Column('data', sa.Text, nullable=False))


def downgrade():
    op.drop_table('mail_drafts')
