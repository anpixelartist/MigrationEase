# MigrationEase

**by Yavda Analytics** — migrate accounting data from user-uploaded **CSV/Excel** into **TallyPrime**
via Tally's XML import format, with auto column-mapping, validation, a GST/e-commerce voucher engine,
and Single Sign-On.

- **Backend** — Python 3.12 / FastAPI: the 7-stage pipeline (parse → profile → map → validate →
  resolve → generate XML → push), multi-tenant orgs, async workers, object storage.
- **Frontend** — React + TypeScript (Vite): the guided import wizard.
- **Bridge** — a Go agent on the user's machine that relays XML to Tally's local gateway (`:9000`).
- **Auth** — built-in email/password *or* **Keycloak SSO** (OIDC).

> **Just want to run/show the demo?** Use the **`run-demo` skill** (`.claude/skills/run-demo/`) or
> follow *Quick start* below. Agents/devs: see [AGENTS.md](AGENTS.md).

## What it does

| Area | Capabilities |
|---|---|
| **Masters** | Ledgers, Groups, Units, Stock Items — parse → auto-map → validate → generate → push. Create-vs-update resolution, opening balances, HSN codes. |
| **Vouchers** | Transactions grouped into balanced vouchers; triple-layer double-entry enforcement; cutover date → opening balances; Round-Off. |
| **GST / e-commerce engine** | Place-of-supply tax computed from a GST rate → **CGST/SGST** (intra-state) or **IGST** (inter-state); **B2B invoices vs B2C daily summaries**; **marketplace settlement** journals (Bank/Commission/Fees/TCS); **refunds → Credit Notes**. |
| **Mapping** | Fuzzy auto-mapping with confidence scoring; fixed-value constants; **saved templates** that auto-apply to future files with the same columns. |
| **Push** | Direct (same machine) or via the bridge; idempotent (a repeat push is blocked; status = pushed / pushed_partial / push_failed). |
| **Auth** | Email/password, or Keycloak SSO (Authorization Code + PKCE), org-scoped multi-tenant isolation. |

---

## Prerequisites

| Tool | Version | For |
|---|---|---|
| **Python** | 3.12+ | backend |
| **Node.js** | 20+ | frontend |
| **Docker** | any | Keycloak (SSO) / optional Postgres·Redis·MinIO |
| **Go** | 1.22+ | bridge (only to push to a networked Tally) |
| **TallyPrime** | — | only to actually push; enable the gateway (F1 → Settings → Connectivity → "Act as Server", port 9000) |

---

## Quick start (dev — SQLite, no external services)

### 1) Backend → http://127.0.0.1:8000
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # POSIX: source .venv/bin/activate
pip install -e .[dev]
uvicorn app.main:app --reload
```
Dev defaults: SQLite (`./tallymigration.db`, auto-created), local-filesystem storage, in-process
broker — no external services. API docs at http://127.0.0.1:8000/docs.

### 2) Frontend → http://localhost:5173
```bash
cd frontend
npm install
npm run dev
```
Proxies `/api` → the backend on :8000. Sign up (or use SSO), then walk the wizard.

### 3) Push to Tally (optional)
Set `TM_DIRECT_TALLY_PUSH=true` to POST straight to a local Tally gateway on :9000 (bypasses the
bridge). Otherwise pushes route through a connected bridge (Bridge tab → generate key → run the
`bridge run …` command on the Tally machine).

---

## Authentication (built-in or Keycloak SSO)

Set the mode with `TM_AUTH_MODE`:

- **`legacy`** — built-in email/password (app-issued JWT). Simplest.
- **`keycloak`** — **SSO only**: all logins go through Keycloak (email verification, MFA, lockout);
  the app's own password endpoints return 403. **Production default.**
- **`hybrid`** — accepts both (only for migrating existing password users).

Bring up Keycloak for a local SSO demo:
```bash
docker compose -f infra/docker-compose.yml up -d keycloak    # realm auto-imported
```
Then run the backend with:
```
TM_AUTH_MODE=keycloak
TM_OIDC_ISSUER=http://localhost:8080/realms/tallymigration
TM_OIDC_AUDIENCE=tallymigration-api
TM_OIDC_WEB_CLIENT_ID=tallymigration-web
```
The login page then shows **Continue with SSO**. The dev realm ships **admin-only** access (you create
users in the Keycloak console — no self-signup), no email verification, and a demo user
`testuser@yavda.local` / `Test@12345`. Console: http://localhost:8080 (`admin` / `admin`, DEV ONLY).

**Production**: import the hardened realm `infra/keycloak/realm-tallymigration-prod.json` and run
Keycloak per `infra/docker-compose.keycloak-prod.yml`. Full checklist (MFA, TLS, secrets, adding
users): [docs/AUTH-KEYCLOAK.md](docs/AUTH-KEYCLOAK.md).

---

## Running with real services (Postgres + Redis + MinIO)
```bash
docker compose -f infra/docker-compose.yml up -d      # postgres:5432, redis:6379, minio:9100, keycloak:8080
cp backend/.env.example backend/.env                  # then edit secrets
cd backend
alembic upgrade head                                  # schema (+ Postgres RLS)
uvicorn app.main:app                                  # API (reads backend/.env)
taskiq worker app.workers.broker:broker app.workers.tasks   # background worker
```

## Tests
```bash
cd backend && python -m pytest -q          # backend (231 tests) — run from backend/
cd backend && python -m ruff check .       # lint
cd frontend && npm run build               # type-check + prod build
cd bridge && go vet ./... && go test ./... && go build ./...
```

## Configuration
All backend settings are env vars prefixed `TM_` (e.g. `TM_DATABASE_URL`, `TM_BROKER_URL`,
`TM_STORAGE_BACKEND`, `TM_AUTH_MODE`, `TM_OIDC_ISSUER`, `TM_DIRECT_TALLY_PUSH`). Full list + dev/prod
values: [backend/.env.example](backend/.env.example). Frontend reads `VITE_API_BASE` (defaults `/api`).

## Troubleshooting
- **Frontend can't reach the API** → backend must be on :8000, or run `TM_BACKEND=... npm run dev`.
- **SSO "invalid scopes"** → the Keycloak realm is missing the `profile`/`email` client scopes; the
  committed realm includes them — recreate the container to re-import.
- **"No bridge connected" on push** → connect a bridge (Bridge tab) or set `TM_DIRECT_TALLY_PUSH=true`.
- **Push "company does not exist"** → the Plan-step company must match the company open in Tally.
- **Stock items rejected** → enable inventory in the Tally company (F11 → Maintain Inventory).
