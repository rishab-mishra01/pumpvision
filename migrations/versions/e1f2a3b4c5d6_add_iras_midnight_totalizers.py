"""Add IRAS midnight Shift Totalizer boundaries.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""
from alembic import op
import sqlalchemy as sa


revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "iras_midnight_totalizers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operational_date", sa.Date(), nullable=False),
        sa.Column("nozzle_no", sa.Integer(), nullable=False),
        sa.Column("totalizer_open", sa.Float(), nullable=True),
        sa.Column("totalizer_close", sa.Float(), nullable=False),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operational_date", "nozzle_no", name="uq_iras_midnight_totalizer"
        ),
    )
    op.create_index(
        "ix_iras_midnight_totalizers_operational_date",
        "iras_midnight_totalizers",
        ["operational_date"],
    )


def downgrade():
    op.drop_index(
        "ix_iras_midnight_totalizers_operational_date",
        table_name="iras_midnight_totalizers",
    )
    op.drop_table("iras_midnight_totalizers")
