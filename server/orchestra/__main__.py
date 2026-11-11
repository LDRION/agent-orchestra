"""Командная строка:

    python -m orchestra serve                 запустить сервер (адрес и порт — из ORCHESTRA_HOST / ORCHESTRA_PORT)
    python -m orchestra migrate               применить миграции схемы
    python -m orchestra verify-audit          проверить целостность журнала аудита
    python -m orchestra rotate-master-key     перешифровать секреты новым мастер-ключом (сервер остановлен)
    python -m orchestra recover LOGIN         аварийно: временный пароль (+ --reset-2fa, --make-owner)
    python -m orchestra backup FILE           зашифрованная копия базы лаунчера (пароль — ORCHESTRA_BACKUP_PASSPHRASE или ввод)
    python -m orchestra restore FILE          восстановить базу из копии (приложение остановлено)
    python -m orchestra reanchor              после восстановления PostgreSQL из pg_dump — подписать конец журнала
"""
import argparse
import asyncio
import logging
import sys


def main() -> None:
    from orchestra.settings import get_settings

    parser = argparse.ArgumentParser(prog="orchestra", description="Оркестр · AI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.add_argument("--reload", action="store_true")
    sub.add_parser("migrate")
    sub.add_parser("verify-audit")
    rp = sub.add_parser("rotate-master-key", help="перешифровать все секреты новым мастер-ключом")
    rp.add_argument("--out", help="куда записать новый ключ (для ORCHESTRA_MASTER_KEY_FILE)")
    rc = sub.add_parser("recover", help="аварийно вернуть доступ: временный пароль, сброс 2FA, владелец")
    rc.add_argument("login")
    rc.add_argument("--reset-2fa", action="store_true", help="сбросить второй фактор (телефон и коды потеряны)")
    rc.add_argument("--make-owner", action="store_true", help="сделать владельцем платформы")
    for name in ("backup", "restore"):
        sub.add_parser(name).add_argument("file")
    sub.add_parser("reanchor").add_argument("--reason", default="база восстановлена из резервной копии")
    args = parser.parse_args()
    s = get_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from orchestra.core.redact import guard_logging
    guard_logging()

    if args.cmd == "serve":
        import uvicorn
        host = args.host or s.host
        if s.mode == "desktop" and host not in ("127.0.0.1", "localhost", "::1"):
            sys.exit("Режим desktop слушает только 127.0.0.1. Для сети — ORCHESTRA_MODE=server за прокси с TLS.")
        uvicorn.run("orchestra.app:create_app", factory=True, host=host, port=args.port or s.port, reload=args.reload,
                    proxy_headers=s.mode == "server", forwarded_allow_ips=s.trusted_proxies, server_header=False,
                    log_level=s.log_level.lower(), log_config=None)      # логи uvicorn — через общий фильтр секретов
    elif args.cmd == "migrate":
        from orchestra.db.migrate import upgrade
        upgrade(s.db_url)
        print("схема обновлена")
    elif args.cmd == "verify-audit":
        from orchestra.audit.service import verify
        from orchestra.db.session import init_engine
        init_engine()
        print(asyncio.run(verify()))
    elif args.cmd == "rotate-master-key":
        from orchestra.core import rotate
        print(asyncio.run(rotate.run(args.out)))
    elif args.cmd in ("backup", "restore"):
        import getpass
        import os
        from pathlib import Path

        from orchestra.core import backup
        phrase = os.environ.get("ORCHESTRA_BACKUP_PASSPHRASE") or getpass.getpass("Пароль копии: ")
        if args.cmd == "backup":
            print(backup.create(Path(args.file), phrase))
        else:
            print(backup.restore(Path(args.file), phrase))
            from orchestra.audit import anchor
            print(asyncio.run(anchor.reanchor("база лаунчера восстановлена из резервной копии")))
    elif args.cmd == "reanchor":
        from orchestra.audit import anchor
        print(asyncio.run(anchor.reanchor(args.reason)))
    elif args.cmd == "recover":
        from orchestra.iam import recovery
        print(asyncio.run(recovery.run(args.login, reset_2fa=args.reset_2fa, make_owner=args.make_owner)))


if __name__ == "__main__":
    main()
