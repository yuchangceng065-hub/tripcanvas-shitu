"""Immutable owner-scoped scenario branches."""
from alembic import op
import sqlalchemy as sa
revision='f39a27cb0156'
down_revision='c17e94d25b80'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('scenario_branches',sa.Column('id',sa.String(),primary_key=True),sa.Column('owner',sa.String(),nullable=False),sa.Column('source_trip_id',sa.String(),nullable=False),sa.Column('source_revision',sa.Integer(),nullable=False),sa.Column('request_id',sa.String(),nullable=False,unique=True),sa.Column('request_hash',sa.String(),nullable=False),sa.Column('label',sa.String(),nullable=False),sa.Column('snapshot',sa.Text(),nullable=False),sa.Column('created',sa.Float(),nullable=False))
    op.create_index('ix_scenario_branches_owner','scenario_branches',['owner'])
    op.create_index('ix_scenario_branches_source_trip_id','scenario_branches',['source_trip_id'])

def downgrade():
    op.drop_index('ix_scenario_branches_source_trip_id',table_name='scenario_branches')
    op.drop_index('ix_scenario_branches_owner',table_name='scenario_branches')
    op.drop_table('scenario_branches')
