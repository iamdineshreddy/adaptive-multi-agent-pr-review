#!/bin/sh
# Prometheus does NOT expand environment variables inside prometheus.yml, so a
# `${ADAPTIVE_API_TOKEN}` in the scrape config is sent literally and every scrape
# gets a 401. Write the bearer token to a file the scrape config references via
# `credentials_file` instead.
set -eu

if [ -z "${ADAPTIVE_API_TOKEN:-}" ]; then
    echo "ADAPTIVE_API_TOKEN is empty: metrics scrapes will be rejected (401)." >&2
else
    printf '%s' "$ADAPTIVE_API_TOKEN" > /tmp/adaptive-api-token
    chmod 0400 /tmp/adaptive-api-token
fi

exec /bin/prometheus "$@"
