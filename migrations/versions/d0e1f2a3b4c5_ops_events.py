"""Add the append-only live operations event stream.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""
from alembic import op
import sqlalchemy as sa


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ops_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=12), nullable=False),
        sa.Column("op_date", sa.Date(), nullable=True),
        sa.Column("business_at", sa.DateTime(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_role", sa.String(length=20), nullable=True),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.String(length=80), nullable=True),
        sa.Column("idempotency_key", sa.String(length=180), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_ops_events_event_type", "ops_events", ["event_type"])
    op.create_index("ix_ops_events_op_date", "ops_events", ["op_date"])
    op.create_index("ix_ops_events_recorded_at", "ops_events", ["recorded_at"])


def downgrade():
    op.drop_index("ix_ops_events_recorded_at", table_name="ops_events")
    op.drop_index("ix_ops_events_op_date", table_name="ops_events")
    op.drop_index("ix_ops_events_event_type", table_name="ops_events")
    op.drop_table("ops_events")
