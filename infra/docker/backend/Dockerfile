FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend

# Build context is the repository root. Install before copying everything else so
# dependency layers cache across api/worker/scheduler rebuilds.
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./

# [all] = base + db + queue + llm + orchestration + monitoring (+ ml for the
# learned-weight path). Dev/test tooling is intentionally not shipped.
RUN pip install --no-cache-dir .[all]

RUN addgroup --system --gid 1001 apprunner && \
    adduser --system --uid 1001 --ingroup apprunner apprunner && \
    chown -R apprunner:apprunner /app

USER apprunner

COPY --chmod=0755 infra/docker/backend/entrypoint.sh /usr/local/bin/app-entrypoint.sh

# api/worker/scheduler share this image; compose overrides `command`.
ENTRYPOINT ["app-entrypoint.sh"]