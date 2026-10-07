"""expense sub-category, fuel tests, mock drills

Revision ID: f6a1b2c3d4e5
Revises: e5f6a1b2c3d4
Create Date: 2026-10-07

- expenses.sub_category: second-level dropdown under the expense category.
- fuel_tests: fuel drawn off for testing; leaves the tank, is not a sale.
- mock_drills: completed mock drills (mandatory every 3 months).

Idempotent: boot runs db.create_all() before upgrade(), so the new tables may
already exist by the time this runs.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f6a1b2c3d4e5'
down_revision = 'e5f6a1b2c3d4'
branch_labels = None
depends_on = None


def upgrade():
    insp = sa.inspect(op.get_bind())
    tables = insp.get_table_names()

    if 'sub_category' not in {c['name'] for c in insp.get_columns('expenses')}:
        with op.batch_alter_table('expenses') as batch_op:
            batch_op.add_column(sa.Column('sub_category', sa.String(length=50), nullable=True))

    if 'fuel_tests' not in tables:
        op.create_table(
            'fuel_tests',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('op_date', sa.Date(), nullable=False),
            sa.Column('product', sa.String(length=5), nullable=False),
            sa.Column('litres', sa.Float(), nullable=False),
            sa.Column('note', sa.String(length=200), nullable=True),
            sa.Column('logged_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_fuel_tests_op_date', 'fuel_tests', ['op_date'])

    if 'mock_drills' not in tables:
        op.create_table(
            'mock_drills',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('drill_date', sa.Date(), nullable=False),
            sa.Column('notes', sa.String(length=300), nullable=True),
            sa.Column('logged_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_mock_drills_drill_date', 'mock_drills', ['drill_date'])


def downgrade():
    op.drop_index('ix_mock_drills_drill_date', table_name='mock_drills')
    op.drop_table('mock_drills')
    op.drop_index('ix_fuel_tests_op_date', table_name='fuel_tests')
    op.drop_table('fuel_tests')
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.drop_column('sub_category')
