# TallyMigration ("Ledgerbridge")

Migrate accounting data from user-uploaded **CSV/Excel** into **Tally** (Indian desktop accounting
software) via Tally's XML import format — with auto column-mapping, validation, and a guided wizard.

- **Backend** — Python 3.12 / FastAPI: the 7-stage pipeline (parse → profile → map → validate →
  resolve → generate XML → push), auth + multi-tenant orgs, async workers, object storage.
- **Frontend** — React + TypeScript (Vite): the import wizard.
- **Bridge** — a Go agent that runs on the user's machine and relays XML to Tally's `localhost:9000`.

## Repository layout

| Path | What |
|---|---|
| `backend/` | FastAPI service + Alembic migrations + taskiq workers ([backend/README.md](backend/README.md)) |
| `frontend/` | React + TS (Vite) app ([frontend/README.md](frontend/README.md)); `design-reference/` holds the original prototype |
| `bridge/` | Go bridge agent ([bridge/README.md](bridge/README.md)) |
| `infra/` | `docker-compose.yml` for Postgres / Redis / MinIO |
| `shared/` | canonical Tally field catalog (JSON) — read by backend and frontend |
| `docs/` | architecture + open-source-stack plan |

---

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| **Python** | 3.12+ | backend |
| **Node.js** | 20+ | frontend |
| **Go** | 1.22+ | bridge (optional until you need it) |
| **Docker** | any | Postgres/Redis/MinIO (optional — dev runs on SQLite + local FS) |
| **TallyPrime** | — | only to actually push data; enable the gateway (F1 → Settings → Connectivity → "Act as Server", port 9000) |

Either conda **or** a plain venv works for the backend. Examples below show both.

---

## Quick start (dev — SQLite, no Docker)

### 1) Backend → http://127.0.0.1:8000

```bash
cd backend

# --- option A: conda ---
conda create -n tally python=3.12 -y
conda run -n tally pip install -e .[dev]
conda run -n tally uvicorn app.main:app --reload

# --- option B: venv ---
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .[dev]
uvicorn app.main:app --reload
```

Dev defaults: SQLite (`./tallymigration.db`, auto-created), local-filesystem blob storage, and an
in-process task broker — **no external services required**. API docs at http://127.0.0.1:8000/docs.

> **Push to Tally in dev:** set `TM_DIRECT_TALLY_PUSH=true` to POST straight to a local Tally gateway
> (bypasses the bridge). Otherwise pushes go through a connected bridge (see below).

### 2) Frontend → http://localhost:5173

```bash
cd frontend
npm install
npm run dev
```

The dev server **proxies `/api` → the backend on :8000** (override with `TM_BACKEND=http://host:port npm run dev`).
Open http://localhost:5173, **sign up** (creates your workspace), and walk the wizard.

### 3) Try it
1. Sign up.
2. **Import** → choose *Ledgers* → upload a CSV like:
   ```csv
   Ledger Name,Under,Opening Bal
   Acme Traders,Sundry Debtors,15000
   Beta Corp,Sundry Debtors,(500)
   ```
3. Preview → confirm the auto-mapping → Validate → enter your Tally company on the Plan step →
   **Download XML** or **Push to Tally**.

---

## Connecting Tally via the bridge (instead of direct push)

In production the backend never reaches Tally directly — a local **bridge agent** does. To use it in dev:

```bash
# build the bridge (needs Go on the machine running Tally)
cd bridge
go build -o bin/bridge ./cmd/bridge
./bin/bridge probe                 # verify the local Tally gateway is reachable
```

Then in the app: **Bridge** tab → *Generate key* → copy the `bridge run ...` command it shows and run
it on the Tally machine. The bridge dials out to the backend's `/bridge/ws`, and a **Push to Tally**
now relays through it. (Leave `TM_DIRECT_TALLY_PUSH` unset when using the bridge.)

---

## Running with real services (Postgres + Redis + MinIO)

```bash
docker compose -f infra/docker-compose.yml up -d        # postgres:5432, redis:6379, minio:9100
cp backend/.env.example backend/.env                    # then edit TM_JWT_SECRET, etc.

cd backend
conda run -n tally alembic upgrade head                 # create schema (+ Postgres RLS)
conda run -n tally uvicorn app.main:app                 # API (reads backend/.env)
conda run -n tally taskiq worker app.workers.broker:broker app.workers.tasks   # background worker
```

With `TM_BROKER_URL` set to Redis, validate/generate/push run on the worker (off the request path).
`.env` also switches storage to MinIO and the DB to Postgres. See [backend/.env.example](backend/.env.example).

---

## Tests

```bash
# backend (139 tests)
conda run -n tally --cwd backend python -m pytest -q

# bridge
cd bridge && go test ./...

# frontend (type-check + production build)
cd frontend && npm run build
```

---

## Authentication (built-in or Keycloak)

Out of the box the app uses its built-in email/password auth (`TM_AUTH_MODE=legacy`). For SaaS
deployments it integrates **self-hosted Keycloak** (OAuth2/OIDC): browser login via Authorization
Code + PKCE, machine-to-machine API access via client credentials, and org-scoped tenant isolation
enforced server-side.

```bash
docker compose -f infra/docker-compose.yml up -d keycloak   # dev realm auto-imported
# backend/.env:
#   TM_AUTH_MODE=hybrid            # accept both token types while migrating; 'keycloak' = OIDC only
#   TM_OIDC_ISSUER=http://localhost:8080/realms/tallymigration
cd backend && alembic upgrade head                          # adds idp_sub / service_accounts
```

The login page then offers **Continue with SSO**. Existing accounts are linked automatically on
first SSO login by verified email. Details — realm bootstrap, M2M setup, migration steps, prod
checklist, and the tenant-isolation trust model: [docs/AUTH-KEYCLOAK.md](docs/AUTH-KEYCLOAK.md).

## Configuration

All backend settings are env vars prefixed `TM_` (e.g. `TM_DATABASE_URL`, `TM_BROKER_URL`,
`TM_STORAGE_BACKEND`, `TM_JWT_SECRET`, `TM_AUTH_MODE`, `TM_OIDC_ISSUER`, `TM_DIRECT_TALLY_PUSH`).
Full list + dev/prod values: [backend/.env.example](backend/.env.example). The frontend reads
`VITE_API_BASE` (defaults to `/api`).

## Troubleshooting
- **Frontend can't reach the API** → make sure the backend is on :8000, or run `TM_BACKEND=... npm run dev`.
- **"No bridge connected" on push** → either connect a bridge (Bridge tab) or set `TM_DIRECT_TALLY_PUSH=true`.
- **Push error "company does not exist"** → the Plan-step company must match the company currently open in Tally.
- **Stock items rejected** → enable inventory in the Tally company (F11 → Maintain Inventory).
