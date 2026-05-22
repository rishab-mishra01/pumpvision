"""add sdms_summaries table

Revision ID: a1b2c3d4e5f6
Revises: 2fc50a7d52a6
Create Date: 2026-05-22

"""
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = '2fc50a7d52a6'
branch_labels = None
depends_on = None


def upgrade():
    # sdms_summaries is created by db.create_all() at startup, matching the
    # project's established pattern (see 2fc50a7d52a6 for tank_readings/cng_shift_readings).
    # This migration records the schema version advance without DDL.
    pass


def downgrade():
    pass
