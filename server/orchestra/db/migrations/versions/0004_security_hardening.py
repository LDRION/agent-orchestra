"""security hardening: reauth, temporary roles, run origin, security permissions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""
import uuid

from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('origin', sa.String(length=20), server_default='', nullable=False))
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reauth_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.add_column(sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))

    # Права центра безопасности — системным ролям работающих установок (снимок на момент миграции).
    grants = {"security": ("security.view", "security.manage"), "auditor": ("security.view",)}
    bind = op.get_bind()
    roles = dict(bind.execute(sa.text("SELECT key, id FROM roles WHERE is_system = :t"), {"t": True}).all())
    existing = {(r, p) for r, p in bind.execute(sa.text("SELECT role_id, permission FROM role_grants")).all()}
    table = sa.table("role_grants", sa.column("id"), sa.column("role_id"), sa.column("permission"),
                     sa.column("effect"), sa.column("approver_role_id"))
    rows = [{"id": str(uuid.uuid4()), "role_id": roles[key], "permission": perm, "effect": "allow", "approver_role_id": None}
            for key, perms in grants.items() if key in roles for perm in perms if (roles[key], perm) not in existing]
    if rows:
        op.bulk_insert(table, rows)


def downgrade() -> None:
    op.execute("DELETE FROM role_grants WHERE permission IN ('security.view', 'security.manage')")
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.drop_column('expires_at')
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.drop_column('reauth_at')
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_column('origin')
