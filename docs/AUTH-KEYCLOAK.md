# Keycloak IAM integration (OAuth2 / OIDC)

Self-hosted [Keycloak](https://www.keycloak.org/) is the identity provider for TallyMigration.
The FastAPI backend is a pure OAuth2 **resource server**; the React SPA logs in at Keycloak with
**Authorization Code + PKCE**; external services call the API with **client credentials**.

## Why this shape (and not realm-per-tenant)

Tenants stay **rows in the app database** (`organizations` / `memberships`), and Keycloak runs a
**single realm** (`tallymigration`):

- The product already has a correct, tested server-side tenancy model (membership checks +
  `org_id` scoping + Postgres RLS). Reusing it means zero changes to any data-path code.
- Realm-per-tenant conflicts with self-signup SaaS: every new workspace would need realm
  provisioning, per-realm OIDC endpoints, and per-realm client config; token validation would need
  dynamic issuer allow-lists.
- A single realm keeps one issuer + one JWKS, and leaves **Keycloak Organizations** (per-org
  identity providers / enterprise SSO, invitations, delegated admin) as an additive later step —
  the token's `sub` keeps meaning "global user", so nothing in the backend changes when orgs are
  introduced on the Keycloak side.

## Trust model — how tenant isolation is enforced

**Keycloak proves *who* is calling; the app database decides *which org* they may act in.**

1. Every request's bearer token is verified offline against the realm JWKS
   (`app/core/oidc.py`): signature (RS256/ES256 allow-list), `iss`, `aud=tallymigration-api`,
   `exp`. HS* is never accepted on this path, and the legacy HS256 validator never accepts RS*,
   so hybrid mode has no algorithm-confusion route.
2. **End-user tokens** (`app/services/idp_service.py`): the validated `sub` is looked up in
   `users.idp_sub`. First login either links an existing account **by verified email only**
   (`email_verified` claim required — an unverified email can never take over an account) or
   JIT-provisions user + personal org + owner membership, exactly like legacy signup.
3. **Tenant context** is resolved by `auth_service.resolve_org_for_user`: the optional `X-Org-Id`
   header is only honoured if a `memberships` row exists for (user, org). Token claims and
   frontend input never carry tenant authority.
4. **Machine tokens** (client credentials): the token's `azp` (client id) must exist in the
   `service_accounts` table, which only an **owner/admin of the target org** can populate
   (`POST /auth/service-accounts`). No row → 401, revoked → 403. Each service account gets a
   synthetic `users` row + membership so every downstream FK, repository filter, and RLS policy
   applies to machines exactly as to humans.
5. Below the principal, nothing changed: repository-layer `org_id` filtering on every query,
   cross-tenant reads return 404, and Postgres RLS (`app.current_org` GUC) remains the
   defense-in-depth backstop — `service_accounts` ships with the same RLS policy.

## Auth modes (migration path)

`TM_AUTH_MODE` selects what the API accepts:

| mode | accepts | use |
|---|---|---|
| `legacy` (default) | first-party HS256 tokens only | current behaviour, no Keycloak required |
| `hybrid` | HS256 **and** Keycloak OIDC | migration window |
| `keycloak` | OIDC only; `/auth/login`, `/auth/signup` return 403 | end state |

### Migrating existing users

1. Deploy Keycloak, import the realm, set `TM_AUTH_MODE=hybrid` + `TM_OIDC_*` env vars.
2. Existing sessions keep working (HS256). Users who click "Continue with SSO" and register at
   Keycloak **with the same email** (verified) are linked automatically to their existing account
   (`users.idp_sub` set; workspace, jobs, bridges untouched). Optionally bulk-import users into
   Keycloak instead (Admin API `POST /admin/realms/tallymigration/users`) — Argon2 hashes are not
   portable, so imported users reset their password once.
3. Watch adoption (`users.idp_sub IS NULL`), then flip `TM_AUTH_MODE=keycloak`. Password
   endpoints turn off; `TM_JWT_SECRET` can be retired.

## Local development

```bash
docker compose -f infra/docker-compose.yml up -d keycloak   # http://localhost:8080 (admin/admin — DEV ONLY)
```

The compose service auto-imports `infra/keycloak/realm-tallymigration.json`:

- realm `tallymigration` — self-registration on, email-as-username, brute-force protection on,
  5-minute access tokens, refresh-token rotation (`revokeRefreshToken`).
- client `tallymigration-web` — public SPA client, Authorization Code + **PKCE S256 enforced**,
  no implicit/password grants, redirect/origins pinned to `http://localhost:5173`.
- client scope `tallymigration-api` — audience mapper that stamps `aud=tallymigration-api` into
  access tokens (the backend requires it).
- client `example-m2m` — confidential client-credentials template for backend-to-backend callers.

Backend (`backend/.env`):

```bash
TM_AUTH_MODE=hybrid            # or keycloak
TM_OIDC_ISSUER=http://localhost:8080/realms/tallymigration
TM_OIDC_AUDIENCE=tallymigration-api
TM_OIDC_WEB_CLIENT_ID=tallymigration-web
TM_AUTH_RATE_LIMIT=20/minute   # throttle password endpoints while they exist
```

Run migrations (`alembic upgrade head` — adds `users.idp_sub`, nullable `password_hash`,
`service_accounts`), start the backend and `npm run dev`. The login page now shows
**Continue with SSO**; the SPA discovers the issuer/client via `GET /auth/config`, handles
`/auth/callback`, silently refreshes on 401, and ends the Keycloak session on logout.

### Machine-to-machine access

```bash
# 1) An org owner/admin authorizes the client for their org (once):
curl -X POST http://localhost:8000/auth/service-accounts \
  -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
  -d '{"client_id": "example-m2m", "name": "ERP sync", "role": "member"}'

# 2) The external service gets a token with client credentials:
curl -X POST http://localhost:8080/realms/tallymigration/protocol/openid-connect/token \
  -d grant_type=client_credentials -d client_id=example-m2m \
  -d client_secret=dev-only-example-m2m-secret-change-me

# 3) ... and calls the API like any user (scoped to the registered org):
curl http://localhost:8000/jobs -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{"entity_type": "ledger"}'
```

Create one Keycloak client per consuming service (copy `example-m2m`, regenerate the secret).
Revoke access by deleting/disabling the client in Keycloak *or* setting the `service_accounts`
row's status to `revoked`.

## Production deployment

Run Keycloak in the same environment as the app, **never** `start-dev`:

```yaml
keycloak:
  image: quay.io/keycloak/keycloak:26.0
  command: ["start", "--optimized"]
  environment:
    KC_DB: postgres
    KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak    # dedicated DB/schema
    KC_DB_USERNAME: keycloak
    KC_DB_PASSWORD: ${KEYCLOAK_DB_PASSWORD}
    KC_HOSTNAME: https://auth.example.com                  # public URL == token issuer
    KC_PROXY_HEADERS: xforwarded                           # behind TLS-terminating proxy
    KC_BOOTSTRAP_ADMIN_USERNAME: ${KEYCLOAK_ADMIN}
    KC_BOOTSTRAP_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
```

Checklist:

- Import the realm once (`kc.sh import --file realm-tallymigration.json`), then change the
  `example-m2m` secret and update `tallymigration-web`'s redirect URIs / web origins to the real
  frontend origin.
- `TM_OIDC_ISSUER` must match `KC_HOSTNAME` exactly (it is string-compared against `iss`).
- Enable email (SMTP) in the realm and turn on **verify email** — email linking requires it.
- Keep access tokens short (5 min) — the SPA refreshes silently; revocation takes effect quickly.
- The SPA stores tokens in `localStorage` (unchanged from the legacy client). The short access
  token + rotating refresh token materially shrink the exposure of the old 24 h static JWT;
  moving to a BFF/cookie session is a compatible later hardening step.
- Future: social login / OTP / enterprise SSO are realm-level additions (Identity Providers,
  Authentication flows, Organizations) — no backend or frontend changes required, because the
  backend only ever sees the same issuer, audience, and `sub`.

---

## Production launch checklist (Keycloak-only mode)

For a customer-facing deployment the app runs in **`keycloak` mode** — the built-in email/password
endpoints are disabled and every login is verified by Keycloak. Steps:

1. **Backend:** set `TM_AUTH_MODE=keycloak` and `TM_OIDC_REQUIRE_VERIFIED_EMAIL=true`
   (see `backend/.env.example`). `TM_JWT_SECRET` is not needed in this mode.
2. **Realm:** import the hardened template `infra/keycloak/realm-tallymigration-prod.json` (NOT the
   dev `realm-tallymigration.json`). It ships with `verifyEmail=true`, a 12-char password policy,
   brute-force lockout (5 failures), TOTP policy, `sslRequired=all`, and **no** hardcoded M2M secret.
   Then in the admin console:
   - Replace `https://app.example.com` in `tallymigration-web` with your real frontend origin.
   - **Email/SMTP:** Realm Settings → Email — configure a real SMTP server (required for verify-email
     and password reset).
   - **MFA:** Authentication → `browser` flow → set the **OTP Form** execution to *Required* (or add a
     conditional-OTP sub-flow) so users must set up an authenticator. TOTP policy is pre-set.
   - **M2M:** create a confidential client per external consumer (Client authentication ON, Service
     accounts ON, Standard flow OFF), give it the `tallymigration-api` scope, and register its
     clientId with an org via `POST /auth/service-accounts`. The public `tallymigration-web` client
     is rejected for service-account registration by design.
3. **Keycloak server:** run `start --optimized` with `KC_DB=postgres` behind TLS (see
   `infra/docker-compose.keycloak-prod.yml`); inject the bootstrap admin from a secret store and
   rotate it after first login.
4. **Verify:** `GET /auth/config` returns `{"mode":"keycloak"}`; the app login screen shows only
   *Continue with SSO*; `POST /auth/login` and `/auth/signup` return **403** (password auth disabled).
