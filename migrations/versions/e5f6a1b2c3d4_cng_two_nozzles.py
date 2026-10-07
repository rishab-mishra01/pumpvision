"""CNG has two nozzles: one shift reading per nozzle per day

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-10-06

cng_shift_readings.nozzle_no (1 or 2); existing rows become nozzle 1.
The unique key moves from op_date to (op_date, nozzle_no).
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a1b2c3d4'
down_revision = 'd4e5f6a1b2c3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('cng_shift_readings',
                  sa.Column('nozzle_no', sa.Integer(), nullable=False, server_default='1'))
    op.drop_index('ix_cng_shift_readings_op_date', table_name='cng_shift_readings')
    op.create_index('ix_cng_shift_readings_op_date', 'cng_shift_readings', ['op_date'])
    op.create_unique_constraint('uq_cng_shift_op_date_nozzle', 'cng_shift_readings',
                                ['op_date', 'nozzle_no'])


def downgrade():
    op.drop_constraint('uq_cng_shift_op_date_nozzle', 'cng_shift_readings', type_='unique')
    op.drop_index('ix_cng_shift_readings_op_date', table_name='cng_shift_readings')
    op.create_index('ix_cng_shift_readings_op_date', 'cng_shift_readings', ['op_date'], unique=True)
    op.drop_column('cng_shift_readings', 'nozzle_no')
