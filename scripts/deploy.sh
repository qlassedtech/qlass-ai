#!/usr/bin/env bash
# Production deploy: pull, install, migrate, build frontend, restart, verify.
set -euo pipefail

APP_DIR="${APP_DIR:-/usr/share/nginx/aitutor.qlass.in/public_python_aios}"
READY_URL="${READY_URL:-http://127.0.0.1:8096/ready}"
cd "$APP_DIR"

echo "==> git pull"
git pull --ff-only

echo "==> pip install"
if [ -s backend/requirements.lock ] && grep -qv '^#' backend/requirements.lock; then
  venv/bin/pip install -q -r backend/requirements.lock
else
  venv/bin/pip install -q -r backend/requirements.txt
fi

echo "==> migrations"
venv/bin/python scripts/migrate.py

echo "==> frontend build"
(cd frontend && npm ci --silent && npm run build)

echo "==> restart"
sudo -n /usr/bin/systemctl restart aitutor

echo "==> readiness"
for i in $(seq 1 15); do
  sleep 2
  code="$(curl -s -o /dev/null -w '%{http_code}' "$READY_URL" || true)"
  if [ "$code" = "200" ]; then
    echo "ready (attempt $i)"
    exit 0
  fi
done
echo "DEPLOY FAILED: $READY_URL did not return 200 (last: ${code:-none})" >&2
exit 1
