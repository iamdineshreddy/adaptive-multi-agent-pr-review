#!/bin/sh
set -eu

# Apply schema migrations before serving. Services that do not need the latest
# schema (worker, scheduler) set ADAPTIVE_SKIP_MIGRATIONS=true; the api service
# owns the migration step so concurrent startup is safe.
if [ "${ADAPTIVE_SKIP_MIGRATIONS:-}" != "true" ]; then
    echo "[entrypoint] applying database migrations (alembic upgrade head)"
    alembic upgrade head
fi

exec "$@"