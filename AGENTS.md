# AGENTS.md — MigrationEase

Guidance for AI coding agents (and new developers) working in this repo. Product: **MigrationEase**,
by **Yavda Analytics** — migrates CSV/Excel accounting data into **TallyPrime** via Tally's XML import,
with auto column-mapping, validation, a GST/e-commerce voucher engine, and Keycloak SSO.

> To just **run/demo the whole thing**, use the **`run-demo` skill** (`.claude/skills/run-demo/`) or
> follow README.md. This file is the map + conventions.

## Layout
| Path | What |
|---|---|
| `backend/` | Python 3.12 / FastAPI. 7-stage pipeline (parse→profile→map→validate→resolve→generate→push), auth, taskiq workers, Alembic migrations. |
| `frontend/` | React + TS (Vite). Import wizard + bridge + login. |
| `bridge/` | Go agent that relays XML to a local Tally gateway (`:9000`). |
| `infra/` | docker-compose (Postgres/Redis/MinIO/**Keycloak**), Keycloak realms. |
| `shared/canonical_fields/` | JSON catalog of Tally fields per entity — read by backend mapping. |
| `docs/` | Architecture, mapping cheatsheet, `AUTH-KEYCLOAK.md`. |

## Run (dev)
- **Backend:** `cd backend && python -m venv .venv && ./.venv/Scripts/python -m pip install -e ".[dev]"` then
  `./.venv/Scripts/python -m uvicorn app.main:app --reload`. Dev defaults to SQLite + local storage + in-process broker — no external services needed. Docs at `/docs`.
- **Frontend:** `cd frontend && npm install && npm run dev` → http://localhost:5173 (proxies `/api` → :8000).
- **Bridge:** `cd bridge && go build ./cmd/bridge` (only needed to push to a networked Tally).
- **Keycloak (SSO):** `docker compose -f infra/docker-compose.yml up -d keycloak` (admin `admin`/`admin`, realm `tallymigration`).

## Test / verify (run before committing)
- Backend: **from `backend/`** run `python -m pytest -q` (a migration test needs the working dir) + `python -m ruff check .`.
- Frontend: `cd frontend && npm run build` (this runs `tsc --noEmit` then the prod build).
- Bridge: `cd bridge && go vet ./... && go test ./... && go build ./...`.

## Auth modes (env `TM_AUTH_MODE`)
- `legacy` — built-in email/password (app-issued JWT).
- `keycloak` — **SSO only**; app password endpoints return 403; users log in via Keycloak. Production default.
- `hybrid` — accepts both (migration only).
Config keys are `TM_`-prefixed (see `backend/.env.example`). OIDC needs `TM_OIDC_ISSUER` /
`TM_OIDC_AUDIENCE` / `TM_OIDC_WEB_CLIENT_ID`.

## Conventions
- **Tenant isolation:** every tenant-owned row carries `org_id`, filtered at the repo layer; Postgres RLS is the prod backstop (`set_tenant`). `service_accounts` is a cross-tenant lookup — NOT under RLS.
- **Money is `Decimal`.** The GST engine (`app/pipeline/gst.py`) rounds so components sum exactly. Never use floats for amounts.
- **Errors** are problem+json `AppError`s; the pipeline returns `ErrorEnvelope`s rather than raising into the request path.
- **XML** is built with lxml (auto-escaped); the bridge honors the backend-selected `SVCURRENTCOMPANY`.
- Match the surrounding code's style; keep changes minimal and tested.

## Gotchas
- Backend needs **Python 3.12** (target). First dep install is slow (pandas/scipy/lxml).
- Keycloak: the committed realm includes the `profile`/`email` client scopes — required or SSO errors "invalid scopes".
- Don't commit secrets. The demo `admin`/`admin` + realm test user are DEV ONLY; prod uses `realm-tallymigration-prod.json`.
