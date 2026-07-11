"""make payments_received.invoice_id nullable

Revision ID: c3d4e5f6a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-07-11

Manager-recorded payments (Stage 1 "Record payment received" flow) are
customer-scoped, not invoice-scoped — invoices are a Stage 2 owner-side
workflow (Generate Invoice, ReportLab). Requiring invoice_id on every
PaymentReceived row would force a fake/placeholder invoice for every manager
cash/cheque/bank-transfer entry, which is wrong. Relax the column to nullable
so manager payments can be recorded with invoice_id = NULL; invoice-linked
payments (owner side, credit/owner.py record_payment) continue to set it.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a1b2'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payments_received') as batch_op:
        batch_op.alter_column(
            'invoice_id',
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade():
    with op.batch_alter_table('payments_received') as batch_op:
        batch_op.alter_column(
            'invoice_id',
            existing_type=sa.Integer(),
            nullable=False,
        )
