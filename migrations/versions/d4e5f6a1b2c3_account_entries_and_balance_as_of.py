"""account entries, balance as-of date, lube purchase rate

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-10-04

Credit cutover from the pump's accounting software (balances as of 30 Sep 2026).

- account_entries: non-fuel lines on a customer account. OPENING is a cutover
  balance; CHARGE is money the pump pays on the party's behalf (the tanker's EMI,
  salaries, insurance, interest); ADJUSTMENT is a signed correction. amount is
  signed the same way as the accounting software: positive = the party owes more.
- customers.balance_as_of / balance_source: when the stored balance was last
  verified and where it came from.
- lube_products.purchase_rate: cost price, so a sale below cost can be flagged.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a1b2c3'
down_revision = 'c3d4e5f6a1b2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'account_entries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.customer_id'), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('entry_type', sa.String(length=20), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=True),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=200), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_account_entries_customer_date', 'account_entries', ['customer_id', 'entry_date'])

    with op.batch_alter_table('customers') as batch_op:
        batch_op.add_column(sa.Column('balance_as_of', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('balance_source', sa.String(length=120), nullable=True))

    with op.batch_alter_table('lube_products') as batch_op:
        batch_op.add_column(sa.Column('purchase_rate', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('lube_products') as batch_op:
        batch_op.drop_column('purchase_rate')
    with op.batch_alter_table('customers') as batch_op:
        batch_op.drop_column('balance_source')
        batch_op.drop_column('balance_as_of')
    op.drop_index('ix_account_entries_customer_date', table_name='account_entries')
    op.drop_table('account_entries')
