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
