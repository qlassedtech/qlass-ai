# Qlass AI OS

WhatsApp-first AI tutoring platform for Qlass Edtech — a multi-tenant SaaS
sold to schools, with a self-serve direct-to-parent option, built around a
Wati (WhatsApp Business API) integration and a FastAPI backend. A single
React/Vite web portal serves every non-WhatsApp surface: school admin,
teacher, parent, and student.

## What it does, by stakeholder

**Student (WhatsApp — primary channel, plus a web app alternative)**
Conversational AI tutoring in English/Hindi/Bhojpuri/Magahi/Maithili, voice
notes in and out, homework photo OCR, PDF/Word document Q&A, AI-generated
diagrams, YouTube suggestions, structured quizzes and timed mock tests, a
"my progress" report (accuracy, weak topics, streak, fuzzy NCERT chapter
coverage), a referral program, safety guardrails, escalation to a human
teacher, and a per-student credit wallet with a self-serve top-up or a
₹2499/yr auto-renewing unlimited plan (see app/business_rules.py for all
pricing/limit constants).

**Parent** — OTP web login, child's progress view, an automated weekly
WhatsApp digest, consent management, a data-deletion request flow, and a
WhatsApp-shared payment link to pay for their child directly.

**Teacher** — Student roster + bulk CSV upload, quiz assignment (free-topic
or linked to the real NCERT chapter list), a workbook/practice-set PDF
generator, an AI presentation generator (Gamma), and their own personal "My
AI Tutor" profile with its own ₹3500/mo subscription.

**School admin / Qlass staff (super_admin)** — School analytics, branding,
a shared teacher-tool credit ledger, PDF billing statements, teacher
account management, sales-pipeline tracking with auto-flagged churn risk, a
bounded school pilot program (atomic credit + feature grant, capped
duration/student count, with an outcome report), and data-deletion
fulfillment.

## Repo Layout
```
qlass-ai/
  backend/      FastAPI app: routers, agents, models, services, tests
  frontend/     Single React/Vite web portal (admin/teacher/parent/student)
  android/      Native Android student app (Kotlin/Compose) — see "Android App" below
  database/     schema.sql (full current schema) + migrations/ (ordered, numbered)
  scripts/      Cron jobs, one-off admin scripts, deploy preflight check, NCERT ingestion
  docker/       docker-compose.yml (local dev), docker-compose.ovh.yml (production)
  docs/         Architecture notes (some historical/aspirational — verify against code)
```
`agents/`, `knowledge/`, `prompts/` are empty, unused scaffolding from an
earlier plan — the real tutor logic lives in `backend/app/agents/tutor_agent.py`.

**RAG is real and live**, not aspirational: `backend/app/services/retrieval.py`
does Postgres full-text retrieval (no vector DB/embeddings provider —
`rag/`'s `CHROMA_PERSIST_DIR` setting is unused legacy config) against NCERT
textbook content ingested via `scripts/ingest_document.py`, scoped to each
student's class/board, with a code-generated citation footer on every
grounded reply. Relevance is judged by the same LLM call that already runs
per message for intent classification (see `app/services/intent_classifier.py`'s
`relevant_excerpts` field) rather than a separate call or a keyword
heuristic — a full-text keyword match alone was confirmed live to produce
citations that shared a word with the message but nothing else (e.g. a
student replying "Don't know" got cited against unrelated Calculus/Biology
chapters purely because "know" is a common word). See "Populating the RAG
corpus" below for how to get real content into a fresh database.

## Local Development Setup

```bash
git clone <your-repo-url> qlass-ai
cd qlass-ai

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt
cp .env.example .env             # then fill in real keys/tokens
```

### Postgres + Redis (Docker)
```bash
cd docker
docker compose up -d postgres redis
```

### Create the schema
```bash
psql "$DATABASE_URL" -f database/schema.sql
# Then apply any migrations added after schema.sql was last regenerated —
# check database/migrations/ for numbers higher than what's already in schema.sql.
```

### Run the API
```bash
cd backend
uvicorn app.main:app --reload
```
Visit http://localhost:8000/health — should return `{"status": "ok"}`.

### Run the frontend
```bash
cd frontend
npm install
cp .env.example .env.local       # VITE_API_BASE defaults to http://localhost:8000
npm run dev
```

### Run the tests
```bash
cd backend && pytest              # some tests need the pg_db_session fixture — see tests/conftest.py
cd frontend && npm run test
```
Redis-backed tests run against database index 15 of whatever `REDIS_URL`
points at (conftest.py forces it and flushes that index), never index 0.

## Populating the RAG corpus

A fresh database has the `documents`/`document_chunks` tables (see migration
`0038_add_document_chunks_fulltext_search.sql`) but zero rows — retrieval
degrades to "no grounding, tutor answers from general knowledge" until real
textbook content is ingested. There is no bulk NCERT downloader in this repo
(NCERT's own site blocks sustained automated scraping); ingest chapters you
already have as PDF/DOCX files one at a time:

```bash
cd backend
python scripts/ingest_document.py <file.pdf> --class 9 --subject Science --chapter "Cell: The Building Block of Life" --board CBSE
```

This extracts text (pymupdf/python-docx), chunks it (~1000 chars, 150 char
overlap), and replaces any existing row for the same (class, subject,
chapter, board) — safe to re-run on a corrected file. There's no bulk/watch-
folder mode; script it yourself in a loop if you have many files with a
consistent naming convention. Verify ingestion worked with a real question
through `/student-app/chat/send` or WhatsApp and confirm a `📖 Source:` line
appears in the reply.

## Android App

`android/` is a native Kotlin/Jetpack Compose app (OTP login, chat with
citations, quizzes, progress/credit history, photo/voice/PDF upload, push
notifications) hitting the same `backend/app/routers/student_app.py`
endpoints the web app uses — feature parity across WhatsApp/web/Android is
maintained deliberately (see `app/services/student_chat.py`'s and
`app/routers/whatsapp.py`'s shared helpers).

### Local build
```bash
cd android
echo "sdk.dir=/path/to/your/Android/sdk" > local.properties
./gradlew assembleDebug
```
Debug builds point at `http://10.0.2.2:8000` (the Android emulator's alias
for the host machine) — a real device needs a build pointed at your actual
deployed API domain (see `app/build.gradle.kts`'s `API_BASE_URL` per build
type).

### Push notifications (optional)
Inert until configured — no crash, no google-services.json/Gradle plugin
required. To enable:
1. Create a Firebase project, register an Android app in it with package
   name `com.qlass.tutor`, and add these four values to `local.properties`
   (never commit this file — it's gitignored):
   ```
   fcm.apiKey=...
   fcm.appId=...
   fcm.projectId=...
   fcm.senderId=...
   ```
   (Get them from Firebase console → Project settings → General, once the
   Android app is registered.)
2. Backend side: Firebase console → Project settings → Service accounts →
   Generate new private key, save the downloaded JSON somewhere on your
   server (never commit it — it's equivalent to a password), and set
   `FIREBASE_CREDENTIALS_PATH` to its path in `.env`/`.env.production`.
   `firebase-admin` is already in `requirements.txt`.
3. Restart the backend so it picks up the credentials
   (`app/services/push_client.py` initializes lazily on first use).

Once both sides are set, `scripts/send_habit_nudges.py` sends push
notifications to any student who's opened the Android app (registered a
device token via `POST /student-app/device-token`) alongside their existing
WhatsApp nudge — see the script for the exact behavior.

### Release build / distribution
There's no CI/signing pipeline set up yet — `assembleDebug` only produces a
debug-signed APK. Before distributing to real users you still need to: set
up a real release signing key (`keytool -genkey` + configure
`signingConfigs` in `app/build.gradle.kts`), decide on a distribution
channel (Play Store listing, or direct APK sideload — Play Store requires
its own separate developer account/review process, not covered here), and
point the release build's `API_BASE_URL` at your real production domain.

## Deploying to OVH

This assumes one OVH VM for staging and a separate one for production —
never run the database on a disposable test server. Everything below uses
`docker/docker-compose.ovh.yml`, which binds the backend to `127.0.0.1:8000`
only; a reverse proxy you put in front is the only thing publicly exposed
on 80/443.

### 1. Provision and configure

```bash
git clone <your-repo-url> qlass-ai
cd qlass-ai
cp .env.production.example .env.production
cp frontend/.env.production.example frontend/.env.production
```
Fill in every value in `.env.production` — the file itself documents what
each one needs (a real 32+ char `SECRET_KEY`, matching `POSTGRES_*` and
`DATABASE_URL` credentials, your real HTTPS `PORTAL_BASE_URL` and exact
`ALLOWED_ORIGINS`, live Anthropic/Wati/Razorpay keys, etc.). Also fill in
`frontend/.env.production`'s `VITE_API_BASE` — the backend's own public
HTTPS URL (this is the piece most often skipped: without it, the deployed
portal silently falls back to `http://localhost:8000` and every request
from it fails). `PORTAL_BASE_URL` and `VITE_API_BASE` point at each other's
domains — the backend uses the former to text students a real payment
link, the frontend is built against the latter to know where its own API
is.

### 2. Validate before deploying — every time

```bash
python scripts/release_preflight.py --env-file .env.production
```
This must print `PASS` before you continue. It catches the mistakes that
otherwise surface as a 500 in production: a default/short `SECRET_KEY`, a
non-HTTPS `PORTAL_BASE_URL`, a wildcard `ALLOWED_ORIGINS`, mismatched
Postgres credentials, or an unfilled template placeholder.

### 3. Bring the stack up

```bash
docker compose -f docker/docker-compose.ovh.yml up -d --build
```
Then apply the schema/migrations to the **production** database the same
way as local dev (`psql` against `DATABASE_URL`, then every file in
`database/migrations/` newer than what's in `schema.sql`) — run this against
staging first, confirm it's clean, then run the identical ordered set
against production and record which migration you're on.

### 4. Build the frontend

```bash
cd frontend
npm ci
npm run build   # reads frontend/.env.production, outputs static files to frontend/dist/
cd ..
```
There's no frontend service in `docker-compose.ovh.yml` on purpose — `dist/`
is a plain static site, so it's served by the same reverse proxy from the
next step rather than running its own container/process.

### 5. Put a reverse proxy in front

Two public HTTPS hosts, terminated at nginx/Caddy/Traefik on the same VM:
- The domain in `PORTAL_BASE_URL` (e.g. `app.<your-domain>`) serves
  `frontend/dist/` as static files, with unknown paths falling back to
  `index.html` (it's a client-side-routed SPA — without this fallback,
  refreshing on any page but `/` 404s).
- The domain baked into `frontend/.env.production`'s `VITE_API_BASE`
  (e.g. `api.<your-domain>`) proxies to `127.0.0.1:8000`.

Confirm `https://api.<your-domain>/health` and `/ready` both return 200,
and `https://app.<your-domain>/` loads the portal's login page, before
doing anything else.

### 6. Register the real webhooks

Only now — once you have a real public HTTPS URL — register:
- **Wati**: generate a 32+ character secret (`python -c "import secrets; print(secrets.token_urlsafe(48))"`), set it as `WATI_WEBHOOK_SECRET`, then in the Wati dashboard point the WhatsApp webhook at `https://<your-domain>/whatsapp/webhook` and enter the **same value in the webhook's custom `Authorization` header field**. That header is the only accepted credential — a `?secret=` in the webhook URL is ignored (it would land in the proxy's access log) and gets an empty 403.
- **Razorpay**: point the subscriptions webhook at `https://<your-domain>/webhook/razorpay/subscriptions`, then set `RAZORPAY_WEBHOOK_SECRET`.

Restart the backend after adding either secret so it picks them up.

### 7. Smoke test before telling anyone it's live

- One real WhatsApp message in and a real reply out.
- Log into the portal at `https://app.<your-domain>/` and confirm it can actually reach the API (open the network tab — requests should hit `https://api.<your-domain>`, not `localhost`).
- Tap "top up credits" on WhatsApp and confirm the link it sends actually opens the live portal, not `localhost`.
- One real payment verification (top-up or subscription).
- One quiz, one workbook generation, one presentation generation.
- Take a database backup, restore it into a disposable database, and confirm row counts match.

### 8. Scale gradually, not all at once

- Start with **one backend replica**. The webhook-processing/retry logic
  uses an atomic database claim specifically so it's safe to run more than
  one replica (fixed in this codebase — see `_claim_webhook_job` in
  `backend/app/routers/whatsapp.py` and its tests), but still load-test
  before adding a second one rather than assuming it'll be fine.
- Ramp onboarding in waves (e.g. 1 school → 10 → 50), watching p95 response
  time, error rate, Postgres connection count, and AI cost per active
  learner at each step. Roll back a wave rather than pushing forward if
  error rate climbs or webhook jobs start aging past a few minutes — check
  `processed_webhook_messages` for rows stuck in `pending`/`processing`.
- Only set `WEB_CONCURRENCY` (in `backend/Dockerfile`) from an actual load
  test, never a guess.
- Never send a broadcast (`POST /broadcast/send`) to an entire cohort in
  one call — it now hard-caps at 5,000 recipients per call specifically to
  force large campaigns into reviewed batches, not because that number is
  itself known to be safe at your actual Wati account's throughput.
- Get your Wati and Anthropic account's real rate/throughput limits in
  writing before planning capacity around them — nothing in this codebase
  can raise those ceilings.

### What's still a real gap at large scale

Fixed this session: the webhook-claim race condition (safe to run multiple
backend replicas now) and several unbounded queries. Still true:

- No real task queue — Celery/Redis are in `requirements.txt` but
  background work is in-process `asyncio`, fine at moderate volume, a real
  constraint at high message-per-second throughput.
- No centralized logging/error tracking (Sentry or similar) or uptime
  monitoring configured — you're relying on container logs alone.
- Backups are only as good as their off-box copy and their last restore
  drill — see "Backups" below; the nightly job warns on every run until
  `BACKUP_PASSPHRASE` and `BACKUP_RCLONE_REMOTE` are set.

## Deploying a release to the VPS (`scripts/deploy.sh`)

The live host runs the backend as a systemd unit (`aitutor`, see
`scripts/server_hardening.sh`) from a plain git checkout, not the compose
file above. Every release goes through one script, run from the checkout
as the app user:

```bash
cd /usr/share/nginx/aitutor.qlass.in/public_python_aios && bash scripts/deploy.sh
```

It is strictly ordered and stops at the first failure, before anything
user-visible changes:

1. `git pull --ff-only`
2. `pip install` (`backend/requirements.lock` when present, else `requirements.txt`)
3. the test suite (`pytest backend/tests`, minus the five files that need
   the Postgres test DB — `test_habit/nudges/referral/retrieval/sales.py`);
   a red suite aborts the deploy with nothing migrated or restarted.
   The suite is safe to run on the live box with no extra env: the
   database is in-memory SQLite, and `backend/tests/conftest.py` rewrites
   `REDIS_URL` to database index `15` of the same Redis server (flushed at
   session start and end) before the app is imported, so rate-limit/OTP/
   alert state from tests never touches the production index `0`.
4. `scripts/migrate.py`
5. `scripts/check_schema_drift.py` — a **warning**, not a gate (see below)
6. frontend build into `frontend/dist.next`, then an atomic `mv` swap into
   `dist/` — a failed `vite build` leaves the previous site being served
   (the old in-place build emptied `dist/` first, i.e. a blank site)
7. `systemctl restart aitutor`, then `/ready` polled up to 30 s
8. `scripts/install_crontab.sh` — merges `scripts/crontab` into the app
   user's crontab, replacing our own stale lines (a changed schedule
   applies) and leaving other apps' entries alone
9. a one-screen summary

`SKIP_TESTS=1` / `SKIP_FRONTEND=1` exist for emergencies and backend-only
hotfixes; say why in the commit if you use them. The same crontab merge and
a logrotate policy for `logs/*.log` (daily, 14 kept, compressed —
`scripts/logrotate-skoolgpt.conf` → `/etc/logrotate.d/skoolgpt`) are
installed by `sudo bash scripts/server_hardening.sh`, which is idempotent.

### Schema drift check

`python scripts/check_schema_drift.py` reflects the live database
(`DATABASE_URL` from `.env`) and compares it column-by-column against the
SQLAlchemy models, printing one row per table and exiting non-zero on any
table/column that exists on one side only. It exists because production
had accumulated hand-added columns (`students.email`, `tutor_level`, …,
the whole `audit_logs` table) with no migration file, so the repo could no
longer rebuild production — migration
`0054_backfill_untracked_columns.sql` closes that gap idempotently. The
check runs on every deploy (warning) and in CI against a database built
purely from `database/schema.sql` + `database/migrations/` (gate), so the
next hand-edit shows up in the deploy log the same day.

### CI

`.github/workflows/ci.yml` runs on every push/PR: Postgres 16 with pgvector
(migration 0041 needs the extension), `schema.sql` + the full migration
chain applied from scratch (a second `migrate.py` run must report nothing
pending), the drift check against that rebuilt database, the whole backend
suite including the Postgres-only files, `bash -n`/`py_compile` on
`scripts/`, and `npm ci && npm run build` for the frontend. No secrets are
needed — the workflow's only credentials are the throwaway CI database's.

## Backups

`scripts/backup_db.sh` runs nightly at 02:30 IST (see `scripts/crontab`)
and writes to `/var/backups/skoolgpt` (`BACKUP_DIR`), 14 days kept locally:

| artefact | what |
|---|---|
| `skoolgpt-YYYY-MM-DD.dump.gz` | `pg_dump -Fc` of the database, gzipped |
| `skoolgpt-uploads-YYYY-MM-DD.tar.gz` | `backend/static/uploads` — student photos, generated diagrams, school logos; not in Postgres |
| `*.sha256` | checksum next to each artefact (`sha256sum -c FILE.sha256` in the backup dir) |

Two optional settings, read from the repo-root `.env` (the same file the
script already takes `DATABASE_URL` from) or the environment. **Both are
unset today** — the script prints a one-line `WARNING:` for each on every
run until they are:

- `BACKUP_PASSPHRASE` — when set, both artefacts are encrypted with
  `gpg --symmetric --cipher-algo AES256` (→ `.gpg`) and the plaintext is
  deleted before anything leaves the box. Pick a long random string, store
  it in the team password manager: **a backup encrypted with a lost
  passphrase is gone**. Decrypt with
  `gpg --batch --passphrase-fd 3 -d FILE.gpg 3<<<"$BACKUP_PASSPHRASE" > FILE`.
- `BACKUP_RCLONE_REMOTE` — an rclone remote + bucket, e.g.
  `b2:skoolgpt-backups`. When set, the day's artefacts (and their
  `.sha256`) are `rclone copy`'d there and the copy is verified with
  `rclone lsl` — a missing or short remote file fails the run. Configure the
  remote once as the app user (`rclone config`), give it a write-only key
  if the provider supports it, and set a lifecycle rule on the bucket for
  remote retention (the script only prunes locally).

Any failure exits non-zero and appends a `BACKUP FAILED` line to
`logs/cron-backup.log` (`grep 'BACKUP FAILED\|BACKUP OK\|RESTORE CHECK'
logs/cron-backup.log` is the quickest health check).

### Restore drill

`bash scripts/backup_db.sh --restore-check` (cron: 04:00 IST on the 1st of
every month) takes the newest dump, verifies its checksum, decrypts it if
needed, restores it into a scratch database `skoolgpt_restore_check`
(created and dropped by the script, same credentials — the app role needs
`CREATEDB`), and runs `SELECT count(*) FROM students`. Zero rows or any
step failing counts as a failed backup. `pg_restore` warnings about roles/
extensions it can't recreate are printed but don't fail the drill; the
row count is the verdict.

### Restoring for real

```bash
cd /var/backups/skoolgpt                                   # or rclone copy the day's files down first
sha256sum -c skoolgpt-YYYY-MM-DD.dump.gz.gpg.sha256
gpg --batch --passphrase-fd 3 -d skoolgpt-YYYY-MM-DD.dump.gz.gpg 3<<<"$BACKUP_PASSPHRASE" | gunzip > restore.dump
pg_restore --no-owner --no-privileges --dbname "$DATABASE_URL" restore.dump   # into an EMPTY database
tar -xzf skoolgpt-uploads-YYYY-MM-DD.tar.gz -C /path/to/app/backend/static/   # (decrypt the same way first if .gpg)
```

## Log hygiene

- The backend's own `logs/app.log*` files are written through
  `app/logging_config.py`'s `RedactingFilter` (phone numbers and
  key/token/secret values masked) since commit `2052fce` (2026-09-11).
  Files rotated out before that date can still hold raw student phone
  numbers. `bash scripts/purge_old_logs.sh --dry-run` lists which of
  `logs/app.log*` were last written before that cutoff; without
  `--dry-run` it truncates them in place (copytruncate-style, so the
  running workers and `RotatingFileHandler`'s numbering are unaffected)
  and prints each file it touched. A file that straddles the cutoff is
  reported as `MIXED` and left alone. It is a one-off, run by hand — no
  cron or deploy step calls it. Override `APP_DIR` / `CUTOFF` if needed.
- nginx access logs for both Skoolgpt vhosts use the `skoolgpt_noquery`
  format installed by `scripts/server_hardening.sh` (`$uri` instead of
  `$request`), so query strings — OTP links, voice-call `?ticket=`s, the
  old webhook `?secret=` — never reach `/var/log/nginx/*.access.log`.

## Scheduled Jobs
No Celery/scheduler is wired up as an application-level dependency, but if
you're deploying with `docker/docker-compose.ovh.yml`, cron is already
handled for you: it defines a `cron` service (see `docker/cron/`) that
builds a small image containing both `backend/` and `scripts/`, installs
`docker/cron/crontab`, and runs it via real `cron(8)` inside the container
— `docker compose -f docker/docker-compose.ovh.yml up -d --build` is all
you need, nothing to configure on the host itself. Logs land in the
`qlass_cron_logs` volume (`docker compose exec cron tail -f /app/logs/*.log`).
`scripts/send_teacher_digest.py` is intentionally excluded from that
crontab (it takes required `--to`/`--students` args for a specific
recipient, so it stays a manual command — see below).

If you're instead running the backend some other way (not this compose
file), fall back to a real host crontab (a laptop cron won't fire
reliably):
```
# Weekly student progress digest to a teacher/parent — pick your own day/recipients
0 8 * * MON  cd /path/to/qlass-ai && venv/bin/python3 scripts/send_teacher_digest.py --to <phone> --students <phone1> [<phone2> ...] >> logs/teacher_digest.log 2>&1

# Daily nudge for students inside a 21-day habit milestone window who haven't engaged yet today
0 9  * * *   cd /path/to/qlass-ai && venv/bin/python3 scripts/send_habit_nudges.py >> logs/habit_nudges.log 2>&1

# Daily nudge for students who built a real habit (4+ active days ever) but have
# gone quiet for 3-10 days — personalized with their last topic, opposite
# targeting from the referral nudge below (which only targets currently-active
# students). Redis-cooldown gated so the same student isn't pinged more than
# once every 5 days.
0 17 * * *   cd /path/to/qlass-ai && venv/bin/python3 scripts/send_reengagement_nudges.py >> logs/reengagement_nudges.log 2>&1

# Weekly referral nudge to active + over-engaged students — Sunday: more free time to actually message a friend
0 10 * * SUN cd /path/to/qlass-ai && venv/bin/python3 scripts/send_referral_nudges.py >> logs/referral_nudges.log 2>&1

# Weekly Razorpay reconciliation report — read-only, flags any captured
# payment with no matching internal ledger credit for manual review
0 7  * * MON cd /path/to/qlass-ai && venv/bin/python3 scripts/reconcile_razorpay.py --days 7 >> logs/reconcile_razorpay.log 2>&1

# Weekly progress digest sent directly to every linked parent's own WhatsApp
0 18 * * FRI cd /path/to/qlass-ai && venv/bin/python3 scripts/send_parent_digests.py >> logs/parent_digests.log 2>&1

# Daily check for unlimited-plan subscriptions expiring within a week — reminds the
# student/parent (or the teacher, for a personal "My AI Tutor" plan) to renew,
# since activation is still a manual one-time flag with no auto-renewal yet
0 9  * * *   cd /path/to/qlass-ai && venv/bin/python3 scripts/send_subscription_expiry_reminders.py >> logs/subscription_reminders.log 2>&1
```
Each script supports `--dry-run` (except `send_teacher_digest.py` and
`reconcile_razorpay.py`, which are already read-only/reporting-only) to
preview without sending. `logs/` is gitignored — create it on the server
(`mkdir -p logs`) before the first cron run.
