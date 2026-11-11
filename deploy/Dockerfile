# Веб-версия для сервера компании. Сборка из корня репозитория:
#   docker compose -f deploy/docker-compose.yml up -d --build

FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ORCHESTRA_MODE=server ORCHESTRA_HOST=0.0.0.0 ORCHESTRA_PORT=8710 ORCHESTRA_DATA_DIR=/data \
    ORCHESTRA_WEB_DIST=/app/web/dist ORCHESTRA_AGENTS_DIR=/app/agents
WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin orchestra \
    && mkdir /data && chown orchestra:orchestra /data
COPY server/pyproject.toml server/requirements.txt server/
COPY server/orchestra server/orchestra
# Версии — строго из requirements.txt: сборка повторяема, новая (возможно, подменённая) версия пакета не приедет сама
RUN pip install -c server/requirements.txt ./server
COPY agents agents
COPY --from=web /web/dist web/dist
USER orchestra
EXPOSE 8710
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8710/api/system/health', timeout=3)"
CMD ["python", "-m", "orchestra", "serve"]
