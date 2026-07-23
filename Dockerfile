# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.30 AS uv

FROM python:3.12-slim-trixie AS runtime

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN groupadd \
        --gid 10001 \
        clinicops \
    && useradd \
        --uid 10001 \
        --gid clinicops \
        --create-home \
        --shell /usr/sbin/nologin \
        clinicops

COPY pyproject.toml uv.lock README.md ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --locked \
        --no-dev \
        --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --locked \
        --no-dev

RUN chown -R clinicops:clinicops /app

USER clinicops

EXPOSE 8000

CMD ["uvicorn", "clinicops.main:app", "--host", "0.0.0.0", "--port", "8000"]