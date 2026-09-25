#!/usr/bin/env bash
# Production deploy, run from the app checkout on the server AS THE APP USER:
#
#   cd /usr/share/nginx/aitutor.qlass.in/public_python_aios && bash scripts/deploy.sh
#
# Order matters and every step gates the next (set -e): pull -> pip install
# -> test suite -> migrations -> schema-drift report (warning only) ->
# frontend build into dist.next + atomic swap -> service restart -> /ready
# probe -> crontab merge. A failing test run or a failing `vite build`
# aborts BEFORE anything user-visible changes — the old frontend/dist stays
# served (the previous in-place `vite build` emptied dist/ first, so a
# broken build meant a blank site until someone noticed).
#
# Env overrides: APP_DIR, READY_URL, SKIP_TESTS=1 (emergency only — say why
# in the commit), SKIP_FRONTEND=1 (backend-only hotfix).
set -euo pipefail

APP_DIR="${APP_DIR:-/usr/share/nginx/aitutor.qlass.in/public_python_aios}"
READY_URL="${READY_URL:-http://127.0.0.1:8096/ready}"
cd "$APP_DIR"
mkdir -p logs

# Tests that need the dedicated Postgres test database (the pg_db_session
# fixture in backend/tests/conftest.py, localhost:5433) — there's no pytest
# marker for them, so they're excluded by file. Keep in sync with CI.
PG_ONLY_TESTS=(
  backend/tests/test_habit.py
  backend/tests/test_nudges.py
  backend/tests/test_referral.py
  backend/tests/test_retrieval.py
  backend/tests/test_sales.py
)

started="$(date '+%F %T')"
before_rev="$(git rev-parse --short HEAD)"
summary=()
step() { printf '\n==> %s\n' "$*"; }

step "git pull"
git pull --ff-only
after_rev="$(git rev-parse --short HEAD)"
summary+=("code: $before_rev -> $after_rev")

step "pip install"
if [ -s backend/requirements.lock ] && grep -qv '^#' backend/requirements.lock; then
  venv/bin/pip install -q -r backend/requirements.lock
else
  venv/bin/pip install -q -r backend/requirements.txt
fi

if [ "${SKIP_TESTS:-0}" = "1" ]; then
  step "tests SKIPPED (SKIP_TESTS=1)"
  summary+=("tests: SKIPPED")
else
  step "tests (sqlite suite; Postgres-only files excluded)"
  ignore_args=()
  for f in "${PG_ONLY_TESTS[@]}"; do ignore_args+=("--ignore=$f"); done
  if ! venv/bin/python -m pytest backend/tests -q -p no:cacheprovider "${ignore_args[@]}"; then
    echo "DEPLOY ABORTED: test suite failed — nothing was migrated, built or restarted (code is at $after_rev, service still runs the previous build)" >&2
    exit 1
  fi
  summary+=("tests: passed")
fi

step "migrations"
venv/bin/python scripts/migrate.py
summary+=("migrations: applied")

step "schema drift check (warning only)"
if venv/bin/python scripts/check_schema_drift.py; then
  summary+=("schema drift: none")
else
  echo "WARNING: live schema differs from the SQLAlchemy models — see the table above and add a migration under database/migrations/" >&2
  summary+=("schema drift: DETECTED (see output above)")
fi

if [ "${SKIP_FRONTEND:-0}" = "1" ]; then
  step "frontend build SKIPPED (SKIP_FRONTEND=1)"
  summary+=("frontend: skipped")
else
  step "frontend build (into dist.next, then atomic swap)"
  rm -rf frontend/dist.next
  # `npm run build` is `tsc -b && vite build`; npm forwards the extra args
  # to the last command in the script, i.e. to vite build.
  (cd frontend && npm ci --silent && npm run build -- --outDir dist.next --emptyOutDir)
  [ -f frontend/dist.next/index.html ] || { echo "DEPLOY ABORTED: build produced no frontend/dist.next/index.html" >&2; exit 1; }
  rm -rf frontend/dist.prev
  [ -d frontend/dist ] && mv frontend/dist frontend/dist.prev
  mv frontend/dist.next frontend/dist
  rm -rf frontend/dist.prev
  summary+=("frontend: rebuilt and swapped")
fi

step "restart"
sudo -n /usr/bin/systemctl restart aitutor

step "readiness"
ready=0
code=""
for i in $(seq 1 15); do
  sleep 2
  code="$(curl -s -o /dev/null -w '%{http_code}' "$READY_URL" || true)"
  if [ "$code" = "200" ]; then
    echo "ready (attempt $i)"
    ready=1
    break
  fi
done
if [ "$ready" != 1 ]; then
  echo "DEPLOY FAILED: $READY_URL did not return 200 (last: ${code:-none}) — check: sudo journalctl -u aitutor -n 100" >&2
  exit 1
fi
summary+=("service: restarted, $READY_URL -> 200")

step "crontab"
bash scripts/install_crontab.sh
summary+=("crontab: merged from scripts/crontab")

printf '\n==> deploy summary (started %s, finished %s)\n' "$started" "$(date '+%F %T')"
for line in "${summary[@]}"; do echo "  - $line"; done
