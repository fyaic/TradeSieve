FROM ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded AS uv-bin
FROM mirror.gcr.io/library/python:3.14.6-slim-bookworm@sha256:4c92ffcde4dd6f1ff72a24518f49fd4990b27134987dfa31a733badde66df9f8

COPY --from=uv-bin /uv /uvx /usr/local/bin/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --locked --no-dev --no-editable \
    && groupadd --gid 10001 tradesieve \
    && useradd --uid 10001 --gid tradesieve --no-create-home --shell /usr/sbin/nologin tradesieve \
    && install -d -m 0700 /var/lib/tradesieve/raw \
    && chown tradesieve:tradesieve /var/lib/tradesieve/raw \
    && chown -R tradesieve:tradesieve /app

USER 10001:10001

EXPOSE 8080

CMD ["python", "-m", "tradesieve.http"]
