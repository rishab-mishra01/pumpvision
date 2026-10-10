"""fuel tests: nozzle and returned-to-tank

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-10

- fuel_tests.nozzle_no: which nozzle the pump test came out of (NULL on older entries).
- fuel_tests.returned_to_tank: TRUE if poured back into the tank (default), FALSE if it left it.

Idempotent in the same way as the earlier fuel_tests migration.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('fuel_tests')}
    with op.batch_alter_table('fuel_tests') as batch_op:
        if 'nozzle_no' not in cols:
            batch_op.add_column(sa.Column('nozzle_no', sa.Integer(), nullable=True))
        if 'returned_to_tank' not in cols:
            batch_op.add_column(sa.Column('returned_to_tank', sa.Boolean(), nullable=False,
                                          server_default=sa.true()))


def downgrade():
    with op.batch_alter_table('fuel_tests') as batch_op:
        batch_op.drop_column('returned_to_tank')
        batch_op.drop_column('nozzle_no')
