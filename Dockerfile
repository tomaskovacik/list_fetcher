FROM ghcr.io/astral-sh/uv:0.5.9@sha256:ba36ea627a75e2a879b7f36efe01db5a24038f8d577bd7214a6c99d5d4f4b20c AS uv

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-cache

COPY docker /app/docker
RUN chmod +x /app/docker/export.sh /app/docker/restore.sh /app/docker/cert_tool.sh

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --home-dir /app --shell /usr/sbin/nologin appuser \
    && mkdir -p /certs \
    && chown -R appuser:appuser /app /certs

USER appuser
