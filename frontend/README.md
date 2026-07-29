# Qlass Learning — Web Frontend

React + TypeScript + Vite single-page app that serves **four different user
surfaces** off one codebase, gated by which auth token (if any) is present in
`localStorage`:

| Surface | Entry route | Who | Auth token key |
|---|---|---|---|
| Partner Console | `/students` (default) | Teacher / school admin / Qlass super_admin | `token` |
| Student App | `/chat` | Student (self-serve web chat) | `student_token` |
| Parent Dashboard | `/parent` | Parent (read-only progress view) | `parent_token` |
| Public Pay Page | `/pay` | Anyone with a link (no login) | — |

The AI tutoring product itself is WhatsApp-first — this portal is the
web-based management/oversight layer for schools, plus a couple of
self-serve student/parent surfaces. See the repo root `README.md` for the
backend and the overall system.

## Stack

- **React 19** + **React Router 7**, no state management library — each page
  fetches what it needs directly via `api.ts`.
- **Vite 8** for dev server and bundling; **TypeScript** (project-references
  build via `tsc -b`).
- **Oxlint** for linting (`npm run lint`).
- No CSS framework — a small hand-written design system in `src/index.css`
  (CSS variables for color/spacing/shadow tokens, plain class names like
  `.card`, `.data-table`, `.grid-2`). No component library.

## Getting started

```bash
npm install
npm run dev       # http://localhost:5173, proxies API calls to API_BASE
```

The backend must be running separately (see `../backend/README.md` or the
repo root README) — the frontend does not proxy or mock the API.

`API_BASE` (in `src/api.ts`) is currently hardcoded to
`http://localhost:8000`. There's no `.env`-driven API URL yet — update that
constant (and rebuild) when pointing at a deployed backend.

```bash
npm run build      # tsc -b && vite build -> dist/
npm run preview    # serve the production build locally
npm run lint       # oxlint
```

## Structure

```
src/
  api.ts                 All backend calls + auth token helpers, per surface:
                          api.*        — teacher/admin (Bearer `token`)
                          studentApi.* — student app (Bearer `student_token`)
                          parentApi.*  — parent app (Bearer `parent_token`)
                          payApi.*     — public pay page (no auth)
  App.tsx                 Route table + the three ProtectedRoute wrappers
  components/
    Layout.tsx             Sidebar shell for the Partner Console (nav items
                            are gated by teacher.role — see below)
    StudentLayout.tsx       Shell for the student chat app
    ParentLayout.tsx         Shell for the parent dashboard
    ChatWindow.tsx           Shared chat UI used by the student app
  pages/                   One file per route (see the table in App.tsx)
```

### Partner Console pages, by role

`Layout.tsx` shows/hides nav sections based on the logged-in teacher's role:

- **Everyone** (`teacher` / `admin` / `super_admin`): Student Roster, Bulk
  Enrollment, My AI Tutor, Billing & Credits.
- **`teacher` / `admin`** only: Practice Worksheets, Presentation Generator,
  Assign Quiz.
- **`admin` / `super_admin`**: Analytics, Teacher Accounts, School Profile.
- **`super_admin`** only: Schools & Sales (cross-school pipeline view).

A school's own `admin`/`teacher` only ever sees their own school's data —
`super_admin` (Qlass's own staff) is the only role that can see across
schools. This scoping is enforced server-side; the frontend nav just hides
links a role couldn't use anyway.

## Auth model

Three independent, parallel auth systems, each with its own token key,
`check`/`is*Authenticated` helper, and `request*` wrapper in `api.ts` that
attaches the right `Authorization: Bearer <token>` header. A 401 from any of
them clears that surface's token and redirects to `/login` — logging out of
one surface doesn't affect the others (e.g. a teacher and a student can be
signed in in the same browser via separate tabs, in principle).

`Login.tsx` is a single unified entry point: it POSTs the phone number to
the backend, which reports back whether this is a teacher (password),
parent (OTP), or student (OTP) login, and the form adapts accordingly.

## Known gaps

- No automated frontend tests (Vitest/RTL etc.) — correctness is currently
  verified by manually driving the app in a browser plus the backend's own
  test suite.
- `API_BASE` isn't environment-driven (see above).
- No dark mode / theming beyond the single light design system.
