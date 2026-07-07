# Authentication — Design & Rationale

*Why MigrationEase authenticates the way it does, in concrete terms. The operational how-to (setup,
realm config, launch checklist) is in [AUTH-KEYCLOAK.md](AUTH-KEYCLOAK.md); this document is the
decision record. Every claim below points at the specific code, config value, or file that backs it.*

## TL;DR

Users authenticate through **Keycloak 26** (self-hosted, open-source, Apache-2.0) using **OpenID
Connect / OAuth 2.0 Authorization Code + PKCE**. The MigrationEase backend is a pure **OAuth2 resource
server**: it never sees or stores a password — it only cryptographically verifies the signed access
token Keycloak issues. Production runs in `keycloak`-only mode (`TM_AUTH_MODE=keycloak`,
`backend/app/api/deps.py:79`), where the app's own password endpoints return **HTTP 403**.

We chose Keycloak over building our own auth and over a managed cloud IdP (Auth0/Okta/Cognito) for three
measurable reasons: **(1) $0 per-user cost** (vs. per-MAU billing), **(2) customer identity data stays on
our own servers** (DPDP / data-residency), and **(3) enterprise SSO — Google, Microsoft Entra, SAML,
LDAP/AD — is realm configuration, not backend code.**

## 1. Exactly how a login works

```
Browser (SPA)                     Keycloak (:8080)                 Backend (:8000)
    |   click "Continue with SSO"      |                                |
    |--- GET /protocol/openid-connect/auth ------------------------->   |   (frontend/src/auth/oidc.ts:80)
    |     client_id=tallymigration-web response_type=code             |
    |     scope="openid profile email" code_challenge=<S256>          |
    |                                   |  user enters password (+ OTP if MFA on)
    |<-- 302 /auth/callback?code=... ---|                                |
    |--- POST /protocol/openid-connect/token ---------------------->   |   (oidc.ts:116, PKCE verifier)
    |<-- access_token + refresh_token + id_token ---------------------|
    |--- GET /auth/me  (Authorization: Bearer <access_token>) ------------------------------->|
    |                                   |     backend verifies token OFFLINE, provisions user  |  (deps.py:80, idp_service.py)
    |<-- { id, email, orgs[] } ---------------------------------------------------------------|
```

No password ever reaches MigrationEase. The SPA holds no client secret (PKCE S256 replaces it —
`oidc.ts:86`). The backend calls Keycloak's login endpoints **never**; it only fetches the realm's
public keys to check signatures.

## 2. Exactly what the backend verifies (`backend/app/core/oidc.py`)

`jwt.decode(...)` with, verbatim:
- **`algorithms=["RS256","ES256"]`** — asymmetric only. The legacy HS256 secret is *never* accepted on
  the OIDC path, so there is no algorithm-confusion forgery (`oidc.py:23`).
- **`issuer=TM_OIDC_ISSUER`** and **`audience="tallymigration-api"`** — a token minted for another app or
  realm is rejected (`oidc.py:48-49`).
- **`options={"require": ["exp","sub","iss","aud"]}`** — a token missing any of these is rejected.
- Keys come from the realm JWKS via `PyJWKClient(cache_keys=True, lifespan=300)` — cached 5 min, and
  auto-refreshed on an unknown `kid`, so key rotation needs no restart (`oidc.py:26-29`).

Hybrid mode routes by the token's `alg` header, and each validator re-enforces its own algorithm
allow-list — there is no path where an HS256 token is checked with the OIDC verifier (`deps.py:82-85`).

## 3. Exactly how tenancy is decided (never from the token)

A caller's organization is **not** read from any token claim. `deps.py:_oidc_principal` resolves the
user, then `auth_service.resolve_org_for_user` looks up membership in *our* database
(`deps.py:61-63`). Postgres Row-Level Security (`set_tenant`, migration `0002`) is the defense-in-depth
backstop on tenant tables. Result: even a perfectly valid token cannot act in an org the user isn't a
member of.

## 4. Exactly what the production realm enforces (`infra/keycloak/realm-tallymigration-prod.json`)

| Control | Value | Effect |
|---|---|---|
| Password policy | `length(12) upperCase lowerCase digits specialChars notUsername passwordHistory(3)` | No weak passwords; no reuse of last 3 |
| Brute-force lockout | `bruteForceProtected`, `failureFactor: 5` | Account locks after 5 failed attempts |
| MFA | `otpPolicyType: totp` (+ set browser flow OTP = Required) | Time-based one-time codes (Google Authenticator/Authy) |
| Access token lifespan | `accessTokenLifespan: 300` (5 min) | A stolen access token is useless in minutes |
| Refresh rotation | `revokeRefreshToken: true`, `refreshTokenMaxReuse: 0` | A reused/leaked refresh token is detected and revoked |
| Session idle | `ssoSessionIdleTimeout: 1800` (30 min) | Idle sessions expire |
| Transport | `sslRequired: all` | Keycloak refuses non-HTTPS |
| Self-signup | `registrationAllowed: false` | Only an admin creates accounts (our launch model) |
| Shipped secrets | none | No demo user, no hardcoded M2M secret in the prod realm |

## 5. Machine-to-machine (partners/integrations)

OAuth2 **client-credentials** service accounts. A client is authorized to exactly one org only after an
org owner/admin registers its `clientId` via `POST /auth/service-accounts`
(`idp_service.register_service_account`, `idp_service.py:119`); the token's own claims are never trusted
for tenancy. The public browser client (`tallymigration-web`) **cannot** be registered as a service
account — it is rejected with `code="public_client_forbidden"` (`idp_service.py:135`) — otherwise every
end-user token (all carry `azp=tallymigration-web`) would map onto one org.

## 6. Why Keycloak — with the concrete alternative it replaces

| We needed | Keycloak gives us | The alternative if we didn't use it |
|---|---|---|
| MFA / email verify / lockout | Built in, toggled in the realm (§4) | Build TOTP, email flows, and a lockout store ourselves and keep them secure forever |
| No password liability | Passwords hashed in Keycloak (PBKDF2/Argon2), never in our DB | Own the hashing, salting, reset flows, and breach response for financial-app credentials |
| Enterprise SSO | Add Google/Microsoft Entra/SAML/LDAP as a realm Identity Provider — backend still sees the same `iss`/`aud`, **zero code change** | Write and maintain a SAML/OIDC federation layer per customer |
| Data residency (DPDP) | Runs on our own server, in-region | Customer identity data leaves our control |
| Cost | $0 license; runs in ~0.5–1 GB RAM | A recurring per-user bill (§7) |
| No lock-in | Standard OIDC — swapping IdPs barely touches our resource-server code | Rewrite against a proprietary SDK |

## 7. Alternatives we rejected — with the specific reason

- **Build our own (email/password only).** We keep this as `legacy` mode for internal pilots, but it
  ships with **no MFA, no email verification, and no account lockout beyond a rate limiter**
  (`backend/app/core/security.py`, `ratelimit.py`). For a product that writes into customers' Tally
  books and GST data, owning all of that security surface — and its future CVEs — is an unjustified
  risk when a mature server does it correctly.
- **Managed cloud IdP — Auth0 / Okta / AWS Cognito / Firebase Auth.** All bill **per monthly active
  user** (Cognito per-MAU tiers; Auth0/Okta per-MAU subscriptions with MFA and enterprise SSO gated
  behind higher paid tiers). Two hard blockers for us: (1) that cost compounds as we grow — at tens of
  thousands of users it is a recurring four-to-five-figure monthly bill, versus Keycloak's fixed cost of
  one small VM; (2) **customer identity data is stored on a third-party (often overseas) platform**,
  which is a data-residency/DPDP problem for Indian financial customers. Verify exact pricing at
  purchase time, but the *model* (per-MAU + feature-gating) is the disqualifier, not a specific number.
- **Other self-hosted IdPs — Ory, Authentik, Zitadel.** Technically viable and free too. We picked
  Keycloak specifically for the **broadest enterprise federation** (SAML **and** LDAP/Active Directory,
  which large Tally customers ask for), the **largest community + Red Hat backing** (long-term support),
  and its maturity — it is the lowest-risk choice for a security-critical layer.

## 8. Production bugs we found and fixed while hardening this

These were real, caught in review; each is a concrete fix, not a "best effort":

1. **M2M auth was broken under production RLS.** `service_accounts` is a *cross-tenant* lookup
   (clientId → org) but had been placed behind tenant `FORCE ROW LEVEL SECURITY`, so under the non-owner
   prod DB role every service-account token resolved to zero rows. **Fix:** migration
   `0006_service_accounts_no_rls` removes RLS from that one lookup table (SQLite tests couldn't catch
   this — it only appears on Postgres).
2. **Public-client hijack.** The SPA's public client could be registered as a service account, mapping
   every user token onto one org. **Fix:** `register_service_account` rejects `TM_OIDC_WEB_CLIENT_ID`.
3. **Unverified-email account provisioning.** **Fix:** JIT provisioning is gated on `email_verified`
   when `TM_OIDC_REQUIRE_VERIFIED_EMAIL=true` (`idp_service.py`).
4. **Rate limiter blind behind a proxy.** It keyed on the proxy IP, collapsing all clients into one
   bucket. **Fix:** `TM_TRUST_FORWARDED_FOR` keys on the real client IP from `X-Forwarded-For`
   (`ratelimit.py:_client_ip`).

## 9. Honest trade-offs (stated plainly, not hidden)

- **We operate one extra service (Keycloak).** Real cost — but it replaces MFA, email verification,
  lockout, and enterprise federation we would otherwise build and defend ourselves.
- **The SPA stores its token in `localStorage`** (standard for SPAs). The 5-minute access token + rotating
  refresh token limit exposure, but the stronger posture is an **httpOnly-cookie / BFF session** so no
  browser script can read the token. This is a **tracked, not-yet-shipped** hardening step.
- **Launch runs without email verification** (`verifyEmail: false`) because access is **admin-only** — an
  admin vouches for each user, so no mail server is required. Enabling self-signup is one realm setting
  (`verifyEmail: true`) plus SMTP.

## 10. One-line pitch (for the slide)

> We didn't build a login system and we didn't rent one. MigrationEase runs **Keycloak** — the open,
> standards-based identity server enterprises already trust — so customers get **MFA, email verification,
> and single sign-on with their own Google/Microsoft accounts**, their **identity data stays on our
> servers** (DPDP-friendly), it costs **$0 per user**, and our application code carries **zero password
> risk** because it only ever verifies a signed token.
