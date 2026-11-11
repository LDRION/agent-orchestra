"""inbound webhook fingerprints

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('hook_events',
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('integration_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('fingerprint', name=op.f('pk_hook_events'))
    )
    with op.batch_alter_table('hook_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hook_events_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_hook_events_integration_id'), ['integration_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('hook_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_hook_events_integration_id'))
        batch_op.drop_index(batch_op.f('ix_hook_events_created_at'))
    op.drop_table('hook_events')
