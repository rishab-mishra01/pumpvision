"""entry_audit: edit / archive / restore trail for manual entries

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-11

An archived entry is deleted from its own table and kept here as a JSON snapshot, so
it can be restored with the same id. Idempotent: skips the table if it already exists.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9d0e1f2a3b4'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade():
    if 'entry_audit' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'entry_audit',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('entry_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(10), nullable=False),
        sa.Column('op_date', sa.Date(), nullable=True),
        sa.Column('reason', sa.String(300)),
        sa.Column('before_json', sa.Text()),
        sa.Column('after_json', sa.Text()),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('actor_role', sa.String(20)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('restored_at', sa.DateTime(), nullable=True),
        sa.Column('restored_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )
    op.create_index('ix_entry_audit_op_date', 'entry_audit', ['op_date'])


def downgrade():
    op.drop_index('ix_entry_audit_op_date', table_name='entry_audit')
    op.drop_table('entry_audit')
