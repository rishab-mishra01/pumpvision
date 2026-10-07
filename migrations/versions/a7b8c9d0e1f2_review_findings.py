"""review findings (4-hourly review agent + developer dashboard)

Revision ID: a7b8c9d0e1f2
Revises: f6a1b2c3d4e5
Create Date: 2026-10-07

Idempotent: boot may have run db.create_all() first.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7b8c9d0e1f2'
down_revision = 'f6a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    tables = sa.inspect(op.get_bind()).get_table_names()
    if 'review_status' not in tables:
        op.create_table(
            'review_status',
            sa.Column('component', sa.String(length=60), primary_key=True),
            sa.Column('grp', sa.String(length=20), nullable=False),
            sa.Column('status', sa.String(length=8), nullable=False),
            sa.Column('detail', sa.String(length=300), nullable=True),
            sa.Column('checked_at', sa.DateTime(), nullable=False),
        )
    if 'review_findings' in tables:
        return
    op.create_table(
        'review_findings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(length=120), nullable=False, unique=True),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('audience', sa.String(length=10), nullable=False, server_default='dev'),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('suggestion', sa.Text(), nullable=True),
        sa.Column('first_seen', sa.DateTime(), nullable=False),
        sa.Column('last_seen', sa.DateTime(), nullable=False),
        sa.Column('occurrences', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.Column('notification_id', sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_table('review_findings')
    op.drop_table('review_status')
