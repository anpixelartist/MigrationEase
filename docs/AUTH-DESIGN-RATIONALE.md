# Authentication — Design & Rationale

*Why MigrationEase authenticates the way it does. This is the "why we chose this" document; the
operational how-to (setup, realm config, launch checklist) lives in [AUTH-KEYCLOAK.md](AUTH-KEYCLOAK.md).*

## TL;DR

MigrationEase authenticates users through **Keycloak** — a self-hosted, open-source identity server —
using the industry-standard **OpenID Connect (OAuth 2.0)** protocol. **The application never stores or
checks customer passwords itself.** Keycloak does, and it also provides email verification, multi-factor
authentication (MFA), and brute-force lockout out of the box.

We chose this over (a) building our own login system or (b) renting a cloud identity provider because it
gives us **enterprise-grade security and features at zero per-user cost, while keeping our customers'
identity data inside our own infrastructure** — which matters for Indian data-protection (DPDP) and for
enterprise customers who expect their own single sign-on.

---

## 1. What authentication has to do for this product

MigrationEase writes into customers' **accounting books in Tally** — financial, GST, and party data. So
the login layer isn't a formality; it is a security boundary. Our requirements were:

1. **Strong verification** — we must be sure the person logging in is who they claim to be (not just
   "email + password"): support for MFA, email confirmation, and account lockout.
2. **No password-breach liability** — storing and defending customer passwords ourselves is a large,
   ongoing risk we would rather not own.
3. **Multi-tenant isolation** — one customer must never see another's data.
4. **Enterprise-ready** — larger Tally customers often want to log in with *their* corporate identity
   (Google Workspace, Microsoft Entra/Azure AD, SAML). We need a path to that without re-engineering.
5. **Machine-to-machine access** — partners/integrations calling our API without a human.
6. **Data residency / compliance (DPDP)** — customer identity data should stay under our control.
7. **Low, predictable cost** — auth cost should not scale painfully with every new user.

## 2. What we built

- **Three configurable modes** (env `TM_AUTH_MODE`): `legacy` (built-in email/password), `keycloak`
  (**SSO only** — the production default), and `hybrid` (both, used only to migrate existing users).
  In `keycloak` mode the app's own password endpoints are **disabled** (return 403).
- **Standard OIDC login flow** — Authorization Code + **PKCE (S256)**; no client secret in the browser.
- **The backend is a pure OAuth2 resource server.** It never handles passwords; it only *verifies*
  signed tokens Keycloak issued — offline, against the realm's public keys (JWKS), enforcing issuer +
  audience + expiry, and accepting only asymmetric signatures (RS256/ES256). This design means an
  attacker cannot forge a token, and there is no "algorithm-confusion" path.
- **Just-in-time provisioning + verified-email account linking** — a first SSO login creates the user
  and their workspace; existing accounts link by *verified* email only (so an unverified address can
  never take over an account).
- **Multi-tenant safety** — a user's organization is resolved from a **server-side membership check**,
  never from claims in the token. Postgres Row-Level Security is the defense-in-depth backstop.
- **Machine-to-machine** — OAuth2 client-credentials "service accounts", each explicitly authorized to
  an org by an admin (the token's own claims are never trusted for tenancy).
- **Access control we chose for launch** — **admin-authorizes users** (self-signup off; you create
  accounts in the Keycloak console), **MFA-capable**, and **brute-force lockout** — all enforced by
  Keycloak, not hand-rolled by us.

## 3. Why Keycloak (benefits, mapped to the requirements)

| Requirement | How Keycloak delivers it |
|---|---|
| Strong verification (MFA, email, lockout) | Built in — TOTP/WebAuthn MFA, email verification, configurable brute-force lockout. We toggle, not build. |
| No password-breach liability | Passwords live in Keycloak (hashed, salted, industry-standard), never in our app DB. We removed a whole class of risk from our codebase. |
| Enterprise SSO readiness | Keycloak federates to Google, Microsoft Entra, SAML, and LDAP/Active Directory as **configuration** — our backend keeps seeing the same issuer/token, so *no code change* is needed to onboard an enterprise customer. |
| Machine-to-machine | First-class OAuth2 client-credentials support. |
| Data residency / DPDP | **Self-hosted** — identity data stays in infrastructure we control, in-region. |
| Low cost | **Open-source, no per-user fee.** Cost is the server we already run, not a bill that grows with every signup. |
| Standards, not lock-in | Pure OIDC/OAuth2. If we ever replace Keycloak, our backend (a standard resource server) barely changes. |
| Maturity / trust | Red Hat-backed, used by governments and large enterprises for years — not something we should reinvent. |

## 4. Alternatives we considered — and why we didn't pick them

| Option | Why not |
|---|---|
| **Build our own auth** (only email/password in the app) | We keep `legacy` mode for pilots, but for production it means *we* own password security, MFA, email verification, lockout, and enterprise SSO — a large surface to build, secure, and maintain forever. Getting any of it wrong on a product that touches financial books is unacceptable. Reinventing a solved, security-critical problem. |
| **Managed cloud IdP** — Auth0 / Okta / AWS Cognito / Firebase Auth | Excellent products, but: (1) **cost scales with monthly active users** — painful as we grow; (2) **customer identity data is hosted by a third party, often abroad** — a data-residency/DPDP concern for Indian financial customers; (3) **vendor lock-in** to proprietary APIs; (4) less control over login flows and federation. |
| **Other self-hosted IdPs** — Ory, Authentik, Zitadel | All viable, but **Keycloak is the most mature and widely adopted**, with the strongest enterprise story (SAML + LDAP/AD federation, Organizations), the largest community, and Red Hat backing. For a product that must satisfy enterprise Tally customers, that maturity and SSO breadth was the deciding factor. |
| **Keep tokens in an httpOnly cookie / BFF from day one** | The right long-term hardening, but a larger architectural change; we shipped the standard SPA token flow first and flagged the cookie/BFF upgrade as a tracked next step (see §6). |

## 5. Security decisions & hardening we did

Beyond wiring Keycloak, we hardened the integration (several of these fixed real, production-breaking issues found in review):

- **Offline token validation** — issuer + audience + expiry enforced; asymmetric algorithms only; the
  legacy symmetric secret is never accepted on the OIDC path (no algorithm-confusion attack).
- **Tenant isolation is server-authoritative** — org membership is looked up in our DB; token claims are
  never trusted to decide *which* org a caller acts in.
- **Fixed: machine-to-machine under Row-Level Security** — the `service_accounts` lookup is cross-tenant
  by nature; it was mistakenly placed behind tenant RLS, which broke M2M auth under the production DB
  role. Corrected so RLS protects tenant data without breaking the auth lookup.
- **Fixed: public-client hijack** — the browser (public) client can no longer be registered as a service
  account; otherwise every user's token could have been mapped onto one org.
- **Verified-email gate on provisioning** — a brand-new identity is only auto-provisioned with a verified
  email (configurable), so nobody can mint an account for an address they don't own.
- **Proxy-aware rate limiting** — the login rate limiter keys on the real client IP behind our TLS proxy,
  not the proxy's IP.
- **Hardened production realm** — email verification, a strong password policy, brute-force lockout, TOTP
  policy, TLS required, real-domain redirect URIs, and no shipped demo secret
  (`infra/keycloak/realm-tallymigration-prod.json`); the server runs `start --optimized` on Postgres
  behind TLS.
- **Admin-only access for launch** — self-registration is off; only an authorized admin creates users, so
  no one reaches the product unless we let them in.

## 6. Trade-offs & what's next (honest)

- **We run one more service (Keycloak).** That is real operational cost — but it buys MFA, email
  verification, lockout, and enterprise SSO that we would otherwise build and maintain ourselves. Net
  positive for a security-critical financial product.
- **Browser token storage.** Today the SPA stores its session token in the browser's `localStorage`
  (standard for SPAs). Short-lived access tokens + rotating refresh tokens limit the exposure, but the
  stronger posture is an **httpOnly-cookie / BFF session** so a browser-side script can never read the
  token. This is the recommended next hardening step, tracked, not yet shipped.
- **Email verification vs. simplicity.** For the initial admin-authorized rollout we run *without* email
  verification (no mail server to operate) — safe because only admins create accounts. When we open
  self-signup, we turn on email verification (one realm setting + SMTP).

## 7. The one-line pitch (for a stakeholder)

> We didn't build our own login and we didn't rent one. We run **Keycloak** — the same open, standards-based
> identity server enterprises trust — so customers get **MFA, email verification, and single sign-on**, their
> **identity data stays in our infrastructure** (DPDP-friendly), and it costs us **no per-user fee**. Our app
> just verifies a signed token, which keeps password risk out of our code entirely.
