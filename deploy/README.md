# Развёртывание на сервере компании

Веб-версия: PostgreSQL + приложение + Caddy (HTTPS). Наружу открыты только 80/443 у Caddy;
приложение и БД — во внутренней сети Docker, БД без выхода в интернет.

## 1. Секреты (вне git)

```powershell
cd deploy
copy .env.example .env                       # ORCHESTRA_DOMAIN, DB_PASSWORD, ORCHESTRA_SETUP_TOKEN
mkdir secrets
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())" > secrets/master_key
```

`secrets/master_key` шифрует ключи моделей в БД. **Сделайте резервную копию отдельно от бэкапов БД**:
без него ключи не расшифровать (придётся ввести заново), а БД без него бесполезна для злоумышленника.

## 2. Запуск

```powershell
docker compose -f deploy/docker-compose.yml up -d --build
```

Откройте `https://<ORCHESTRA_DOMAIN>/#token=<ORCHESTRA_SETUP_TOKEN>` (токен во фрагменте не уходит в журналы) и создайте владельца платформы.
Внутренний домен без публичного DNS — раскомментируйте `tls internal` в `Caddyfile`.

## 3. Модели

- **Ollama на этом же сервере** — адрес `http://host.docker.internal:11434`, уровень доверия «Локально».
- **vLLM / свой GPU-сервер** — «Частный сервер».
- **Облака** (Claude, OpenAI, YandexGPT, Siberi) — ключ вводится в интерфейсе, хранится зашифрованным.

## Эксплуатация

| Что | Как |
|---|---|
| Обновление | `git pull` → `docker compose -f deploy/docker-compose.yml up -d --build` (миграции применяются при старте) |
| Бэкап | `docker compose exec db pg_dump -U orchestra -Fc orchestra > orchestra.dump` → зашифровать (`gpg -c orchestra.dump`) → вне сервера; `secrets/master_key` — отдельно |
| Восстановление | `pg_restore` в пустую базу → `docker compose exec app python -m orchestra reanchor` (иначе проверка журнала покажет «откат») |
| Владелец потерял доступ | `docker compose exec app python -m orchestra recover <логин> --reset-2fa` — временный пароль, всё в журнале |
| Сети | `data` — приложение ↔ БД, `front` — Caddy ↔ приложение (Caddy до БД не дотянется), `egress` — исходящие приложения, `public` — порты Caddy |
| Целостность аудита | `docker compose exec app python -m orchestra verify-audit` |
| Журнал | `docker compose logs -f app` |
