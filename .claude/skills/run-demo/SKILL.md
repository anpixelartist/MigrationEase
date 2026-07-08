---
name: run-demo
description: Bring up the full MigrationEase demo end-to-end (Keycloak SSO login + backend + frontend), the way it is shown to stakeholders. Use when someone clones this repo and asks to "run the demo", "run the project", "start everything", "show the SSO login", or "set up Keycloak". Produces a running app at http://localhost:5173 with SSO-only login via a ready-made test user.
---

# Run the MigrationEase demo

MigrationEase migrates CSV/Excel accounting data into TallyPrime. This skill starts the three pieces of
the live demo — **Keycloak** (login server), the **backend** API, and the **frontend** — and hands back
the exact URLs + credentials to show.

Run the long-lived processes in the **background** and verify each before moving on. Do NOT block on
them. On Windows use PowerShell/Git-Bash; commands below are POSIX (Git-Bash) with Windows notes.

## 0. Prerequisites (check, don't assume)
- **Docker** running (`docker version`) — for Keycloak.
- **Python 3.12+** (`python --version` or `py --version`) — backend.
- **Node 20+** (`node --version`) — frontend.
- Ports free: **8080** (Keycloak), **8000** (backend), **5173** (frontend).
If a tool is missing, tell the user and stop — don't guess.

## 1. Start Keycloak (the login server)
```bash
docker compose -f infra/docker-compose.yml up -d keycloak
```
This imports `infra/keycloak/realm-tallymigration.json`, which already contains: the `profile`/`email`
client scopes, **admin-only** access (no self-signup), no email verification, and a **demo user**
`demo@migrationease.local` / `Demo@12345`.

Wait until the realm is live (poll, ~30–60s):
```bash
until curl -sf -o /dev/null http://localhost:8080/realms/tallymigration/.well-known/openid-configuration; do sleep 3; done
```
If the container exits with an H2 error, you're on an old compose with a named `keycloakdata` volume —
this repo removed it; `docker compose ... rm -sf keycloak && docker volume rm infra_keycloakdata` then retry.

## 2. Start the backend (SSO-only) → http://127.0.0.1:8000
```bash
cd backend
python -m venv .venv                                   # first run only
./.venv/Scripts/python -m pip install -e ".[dev]"      # Windows path; POSIX: .venv/bin/python
```
Run it in the background with the SSO env (keycloak mode = the app's own password login is OFF):
```bash
TM_AUTH_MODE=keycloak \
TM_OIDC_ISSUER=http://localhost:8080/realms/tallymigration \
TM_OIDC_AUDIENCE=tallymigration-api \
TM_OIDC_WEB_CLIENT_ID=tallymigration-web \
TM_OIDC_REQUIRE_VERIFIED_EMAIL=false \
TM_DIRECT_TALLY_PUSH=true TM_TALLY_URL=http://127.0.0.1:9000 \
./.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
(PowerShell: set each with `$env:TM_AUTH_MODE="keycloak"` etc. before uvicorn. `TM_DIRECT_TALLY_PUSH`
is only needed if a local TallyPrime gateway is running on :9000 — otherwise omit it.)

Verify: `curl -s http://127.0.0.1:8000/auth/config` returns `"mode":"keycloak"`.

## 3. Start the frontend → http://localhost:5173
```bash
cd frontend
npm install            # first run only
npm run dev
```
The dev server proxies `/api` → the backend on :8000. Verify `http://localhost:5173` returns 200.

## 4. Hand off the demo
Tell the user, verbatim:

- **App:** http://localhost:5173 — click **Continue with SSO** (there is no password box), sign in as
  **`demo@migrationease.local` / `Demo@12345`**, and walk the Import wizard (upload a CSV → map → validate
  → generate → download/push to Tally).
- **Add a user (the "authorize someone" flow):** Keycloak console **http://localhost:8080** →
  Administration Console → `admin` / `admin` → switch realm **master → tallymigration** → **Users** →
  **Add user** → **Credentials** tab → Set password (Temporary Off). That user can then log in via SSO.
- **Two-factor:** Keycloak → Authentication → Flows → **browser** → set **OTP Form** = Required.

## 5. Stop the demo
Kill the backend + frontend background processes; `docker compose -f infra/docker-compose.yml stop keycloak`.

## Notes / gotchas (real, learned the hard way)
- **Production auth is stricter than this demo:** `backend/.env.example` ships `TM_AUTH_MODE=keycloak`
  with the hardened realm `infra/keycloak/realm-tallymigration-prod.json` (MFA, TLS, real domain, no
  demo user/secret). The demo values above (`admin`/`admin`, the test user) are DEV ONLY.
- The backend needs Python 3.12 (a migration test uses alembic's relative `script_location`; run
  `pytest` from `backend/`). Some heavy deps (pandas/scipy/lxml) make the first install slow.
- If SSO shows "invalid scopes", the realm is missing the `profile`/`email` client scopes — the
  committed realm includes them; a stale imported realm won't. Re-import (recreate the container).
