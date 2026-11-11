"""audit append-only on SQLite too

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-28
"""
from alembic import op


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # На PostgreSQL запрет — с 0001 (триггер-функция). На SQLite (лаунчер) — свои триггеры:
    # ни приложение, ни случайный скрипт не изменят и не удалят запись журнала.
    if op.get_bind().dialect.name != "sqlite":
        return
    for verb in ("update", "delete"):
        op.execute(f"""
            CREATE TRIGGER IF NOT EXISTS audit_events_no_{verb} BEFORE {verb.upper()} ON audit_events
            BEGIN SELECT RAISE(ABORT, 'audit_events: только добавление'); END
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for verb in ("update", "delete"):
        op.execute(f"DROP TRIGGER IF EXISTS audit_events_no_{verb}")
