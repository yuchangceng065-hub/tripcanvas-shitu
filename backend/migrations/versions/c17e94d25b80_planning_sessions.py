"""Durable owner-scoped conversation and agent checkpoints."""
from alembic import op
import sqlalchemy as sa
revision = 'c17e94d25b80'
down_revision = '8a402d074571'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('planning_sessions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('owner', sa.String(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('updated', sa.Float(), nullable=False))
    op.create_index('ix_planning_sessions_owner', 'planning_sessions', ['owner'])


def downgrade():
    op.drop_index('ix_planning_sessions_owner', table_name='planning_sessions')
    op.drop_table('planning_sessions')
