# ZoNuLy (JobHunter) — the Python pipeline + API in one image.
#
# Why this exists: the same image runs unchanged on an always-on ₹0 VM, so the
# 08:00 cycle fires while the laptop sleeps, and a teammate gets an identical
# environment. On the 8 GB Mac itself, prefer `.venv` — Docker Desktop's VM costs
# ~2 GB and a daemon, which the project rules avoid (CLAUDE.md, FINAL-PLAN-V3 §10).
#
# No model runtime is installed, ever: every model call goes to OpenRouter.
#
#   docker compose build
#   docker compose run --rm cli doctor
#   docker compose up api           # FastAPI on :8000 (+ APScheduler)

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    # Playwright's browsers live outside the venv so a rebuild of deps keeps them
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

# lxml/dnspython/pypdf build cleanly on slim; curl is for health checks only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so a code change does not reinstall the world.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY jobhunter ./jobhunter
COPY scripts ./scripts
COPY config.yaml companies.yaml ./
COPY knowledge ./knowledge

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# A non-root user; the DB and secrets are volumes owned by it.
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /app/secrets /app/profile /app/data \
 && chown -R app:app /app /opt/venv
USER app

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# FastAPI + the scheduler. `cli` in compose overrides this with `python scripts/run.py ...`.
CMD ["uvicorn", "jobhunter.api:app", "--host", "0.0.0.0", "--port", "8000"]


# ---------------------------------------------------------------- optional: browser
# Only the Wellfound/Cutshort scrapers (off by default) and any future X intercept
# need Chromium. Build with `--target browser` when one of them is switched on;
# it adds ~400 MB and is deliberately not the default.
FROM base AS browser
USER root
RUN --mount=type=cache,target=/root/.cache/ms-playwright \
    playwright install --with-deps chromium \
 && chown -R app:app /opt/pw-browsers
USER app
