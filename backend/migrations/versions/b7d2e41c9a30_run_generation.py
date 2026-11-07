"""Fence planning runs with a monotonic activation generation."""
from alembic import op
import sqlalchemy as sa

revision = 'b7d2e41c9a30'
down_revision = 'd59e925296bc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('generation', sa.Integer(),
                                    nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('runs', 'generation')
