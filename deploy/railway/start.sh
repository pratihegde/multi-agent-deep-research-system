#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${PORT:-}" ]]; then
  echo "PORT is not set; defaulting to 8080"
  export PORT=8080
fi

# Render nginx config with Railway-injected PORT.
envsubst '$PORT' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf

# Start API on loopback; nginx handles the public port.
uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# Run nginx in foreground (PID 1).
nginx -g "daemon off;"
