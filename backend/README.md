# TallyMigration Backend (FastAPI)

The cloud service. Wires the 7 pipeline stages (parse → profile → map → validate → resolve →
convert → push) behind a job-lifecycle REST API, with structured logging, a unified problem+json
error model, and thorough data edge-case handling.

## Run (dev)

```
conda run -n tally --cwd backend python -m pytest -q          # tests (121)
conda run -n tally --cwd backend uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for the interactive API.

### Env flags (prefix `TM_`)
| Var | Default | Notes |
|---|---|---|
| `TM_DATABASE_URL` | `sqlite:///./tallymigration.db` | dev SQLite; prod `postgresql+psycopg://...` |
| `TM_JWT_SECRET` | dev placeholder | **override in prod** (HMAC-SHA256, ≥32 bytes) |
| `TM_JWT_EXPIRE_MINUTES` | 1440 | access-token lifetime |
| `TM_MAX_UPLOAD_BYTES` | 25 MB | request/upload size cap (413 over) |
| `TM_MAX_ROWS` / `TM_MAX_COLUMNS` | 200000 / 1000 | data guards |
| `TM_DIRECT_TALLY_PUSH` | false | **dev only** — POST generated XML straight to `TM_TALLY_URL` (bypasses the bridge) |
| `TM_TALLY_URL` | http://127.0.0.1:9000 | local Tally gateway |
| `TM_LOG_JSON` | true | JSON logs (set false for plain console) |

### Auth (all `/jobs` endpoints require a bearer token)
```
POST /auth/signup  {email, password, full_name?, org_name?}  -> 201 {access_token, user}
POST /auth/login   {email, password}                          -> {access_token, user}
GET  /auth/me                                                 -> current user + orgs
```
Send `Authorization: Bearer <token>` (and optionally `X-Org-Id` to pick an org). Jobs are
**org-scoped** — a job from another org is a 404.

## Lifecycle (REST)

```
POST   /jobs                      {entity_type}                 -> 201 job
POST   /jobs/{id}/file            multipart file               -> parse + profile
GET    /jobs/{id}/profile
GET    /jobs/{id}/mapping/suggestions
POST   /jobs/{id}/mapping         {mapping, constants}
POST   /jobs/{id}/validate        {known_groups, known_units, known_states}
GET    /jobs/{id}/validation
POST   /jobs/{id}/resolve         {existing: [...]}             -> create/update/conflict buckets
POST   /jobs/{id}/generate        {company}                     -> Tally XML
GET    /jobs/{id}/artifact                                      -> download .xml
POST   /jobs/{id}/push                                          -> import into Tally (dev: direct)
```

End-to-end verified against a live TallyPrime (company "Test1"): a ledger CSV flows through every
stage and imports (`created=2, errors=0`).

## Robustness highlights
- **Errors:** every failure is an `AppError` → RFC 7807 `application/problem+json` with a stable
  `code`; unhandled exceptions are logged with a traceback and returned as a generic 500 (no leak).
- **Logging:** structured JSON on stdlib logging, request-id bound per request and propagated into
  the threadpool, plus per-stage events (`parse.ok`, `validate.done`, `push.done`, ...).
- **Data edge cases:** encoding/delimiter detection, single-column files, oversized/empty/corrupt
  uploads, duplicate/blank headers, messy numbers (`₹1,23,456` / `(500)` / `1000 Cr` / `1.5e3`),
  null-ish tokens, Dr/Cr inference, per-row conversion errors captured (not thrown).
- **State machine:** out-of-order calls return 409; re-upload safely resets downstream artefacts;
  per-job locks serialize concurrent mutations.

## Persistence + auth (done)
- **Auth:** Argon2id (argon2-cffi) password hashing + HS256 JWT (PyJWT); orgs / users / memberships;
  multi-tenant — every job is `org_id`-scoped (isolation enforced at the repository layer).
- **Persistence:** SQLAlchemy 2.0; jobs (metadata + upload bytes + mapping + validation + generated
  XML + push result) persist to the DB. The heavy working objects (parsed/mapped DataFrames) live in
  an LRU cache and are **rebuilt from the durable record on a cache miss** — jobs survive eviction/restart.

## Production infra (done)
- **Object storage** (`app/storage`): blobs (uploads + generated XML) live in storage — `local` (dev),
  `memory` (tests), or `s3`/MinIO (`TM_STORAGE_BACKEND`); the DB holds only keys.
- **Migrations + RLS** (`alembic/`): `alembic upgrade head` builds the schema; Postgres gets RLS on
  `jobs` (tenant GUC via `db.base.set_tenant`). Dev = SQLite (app-layer isolation).
- **Async workers** (`app/workers`, taskiq): validate/generate/push run as tasks → `202 {task_id}`,
  poll `GET /jobs/{id}/tasks/{task_id}`. Dev runs inline; prod = Redis + `taskiq worker app.workers.broker:broker app.workers.tasks`.
- **Cloud↔bridge WSS relay**: pair a bridge (`POST /bridges` → API key), the Go agent dials
  `/bridge/ws`, and `push` relays the XML over WSS to the bridge (when `TM_DIRECT_TALLY_PUSH` is off).

Run real services: `docker compose -f ../infra/docker-compose.yml up -d` and copy `.env.example` → `.env`.

## Not yet wired (next)
Multi-process relay (Redis pub/sub so a separate worker can reach the bridge socket — plan §10.8),
the device-pairing UX + Windows Credential Manager storage on the bridge, and the **React frontend**.
