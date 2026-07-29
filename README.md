# Qlass AI OS

WhatsApp-first AI education platform for Qlass Edtech. Android/Web interfaces
reuse the same backend APIs — the AI layer stays independent of the interface.

## Layers
1. **Core AI Platform** — RAG, agents, student memory, analytics (`backend/app/agents`, `rag/`)
2. **Business APIs** — students, teachers, attendance, homework, fees, reports (`backend/app/routers`)
3. **Interfaces** — WhatsApp (live), Android (later), Web (later)

## Phase 1 — Local Setup

```bash
git clone <your-repo-url> qlass-ai
cd qlass-ai

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env            # then fill in real keys/tokens
```

### Run Postgres + Redis (Docker)
```bash
cd docker
docker compose up -d postgres redis
```

### Create the schema
```bash
psql "$DATABASE_URL" -f database/schema.sql
```

### Run the API
```bash
cd backend
uvicorn app.main:app --reload
```
Visit http://localhost:8000/health — should return `{"status": "ok"}`.

## Repo Layout
```
qlass-ai/
  backend/      FastAPI app: routers, agents, models, services
  frontend/     Web portal (Phase 20)
  android/      Android app (Phase 14)
  rag/          Ingestion + retrieval pipeline (Phase 5-6)
  database/     schema.sql, migrations
  agents/       (reserved — agent prompt configs live in backend/app/agents + prompts/)
  knowledge/    local staging area mirroring the Google Drive knowledge base
  prompts/      versioned system prompts per agent
  scripts/      one-off / maintenance scripts
  tests/        pytest suite
  docs/         architecture notes, ADRs
  docker/       docker-compose.yml, Dockerfiles
```

## Scheduled Jobs
No Celery/scheduler is wired up yet — these are meant to be run via cron
once the backend is deployed on a real server (not a local dev machine,
since a laptop cron won't fire reliably). Add to that server's crontab:
```
# Weekly student progress digest to a teacher/parent — pick your own day/recipients
0 8 * * MON  cd /path/to/qlass-ai && venv/bin/python3 scripts/send_teacher_digest.py --to <phone> --students <phone1> [<phone2> ...] >> logs/teacher_digest.log 2>&1

# Daily nudge for students inside a 21-day habit milestone window who haven't engaged yet today
0 9  * * *   cd /path/to/qlass-ai && venv/bin/python3 scripts/send_habit_nudges.py >> logs/habit_nudges.log 2>&1

# Weekly referral nudge to active + over-engaged students — Sunday: more free time to actually message a friend
0 10 * * SUN cd /path/to/qlass-ai && venv/bin/python3 scripts/send_referral_nudges.py >> logs/referral_nudges.log 2>&1
```
Each script supports `--dry-run` (except send_teacher_digest.py) to preview without sending. `logs/` is gitignored — create it on the server (`mkdir -p logs`) before the first cron run.

## Roadmap
See `docs/roadmap.md` for the full 20-phase plan. Currently scaffolded:
Phase 1 (Foundation), skeletons for Phase 2 (WhatsApp webhook) and
Phase 4 (Students API), and Phase 5 (initial schema).

## Next concrete steps
- [ ] Fill in `.env` with real Postgres URL + WhatsApp Cloud API creds
- [ ] Wire `whatsapp.py` webhook to Meta's Cloud API (send + receive)
- [ ] Add SQLAlchemy models mirroring `database/schema.sql`
- [ ] Build the Google Drive sync job (Phase 5) into `rag/`
- [ ] Build the chunk + embed pipeline (Phase 6) into `rag/`
