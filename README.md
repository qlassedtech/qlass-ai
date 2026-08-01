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
₹1800/yr auto-renewing unlimited plan.

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
  database/     schema.sql (full current schema) + migrations/ (ordered, numbered)
  scripts/      Cron jobs, one-off admin scripts, deploy preflight check
  docker/       docker-compose.yml (local dev), docker-compose.ovh.yml (production)
  docs/         Architecture notes (some historical/aspirational — verify against code)
```
`agents/`, `rag/`, `knowledge/`, `prompts/`, `android/` are empty, unused
scaffolding from an earlier plan — the real tutor logic lives in
`backend/app/agents/tutor_agent.py`; there is no RAG/embeddings pipeline or
Android app in the current product.

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
```
Fill in every value in `.env.production` — the file itself documents what
each one needs (a real 32+ char `SECRET_KEY`, matching `POSTGRES_*` and
`DATABASE_URL` credentials, your real HTTPS `PORTAL_BASE_URL` and exact
`ALLOWED_ORIGINS`, live Anthropic/Wati/Razorpay keys, etc.).

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

### 4. Put a reverse proxy in front

Terminate TLS at nginx/Caddy/Traefik, proxying to `127.0.0.1:8000`. Confirm
`https://<your-domain>/health` and `/ready` both return 200 before doing
anything else.

### 5. Register the real webhooks

Only now — once you have a real public HTTPS URL — register:
- **Wati**: point the WhatsApp webhook at `https://<your-domain>/whatsapp/webhook`, then set `WATI_WEBHOOK_SECRET` from what Wati gives you.
- **Razorpay**: point the subscriptions webhook at `https://<your-domain>/webhook/razorpay/subscriptions`, then set `RAZORPAY_WEBHOOK_SECRET`.

Restart the backend after adding either secret so it picks them up.

### 6. Smoke test before telling anyone it's live

- One real WhatsApp message in and a real reply out.
- One real payment verification (top-up or subscription).
- One quiz, one workbook generation, one presentation generation.
- Take a database backup, restore it into a disposable database, and confirm row counts match.

### 7. Scale gradually, not all at once

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
- Postgres backup/restore should be tested (see step 6) but there's no
  automated backup job — set one up on the OVH volume or via a managed
  Postgres offering.

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
