/**
 * Minimal OIDC Authorization Code + PKCE client for Keycloak (no extra dependencies).
 *
 * The backend advertises the issuer + public client id via GET /auth/config; this module owns the
 * redirect to Keycloak, the code→token exchange, refresh, and RP-initiated logout. Endpoint paths
 * follow the Keycloak realm layout (`/protocol/openid-connect/...`).
 *
 * Security notes: PKCE (S256) + `state` for the front channel; the code_verifier/state live in
 * sessionStorage only for the duration of the redirect. No client secret exists in the SPA.
 */

export interface OidcConfig {
  issuer: string;
  clientId: string;
}

export interface TokenSet {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  expires_in?: number;
}

const VERIFIER_KEY = "tm_pkce_verifier";
const STATE_KEY = "tm_oidc_state";
const CONFIG_KEY = "tm_oidc_config"; // persisted so refresh/logout work after a full reload

const endpoints = (issuer: string) => {
  const base = issuer.replace(/\/$/, "");
  return {
    authorize: `${base}/protocol/openid-connect/auth`,
    token: `${base}/protocol/openid-connect/token`,
    logout: `${base}/protocol/openid-connect/logout`,
  };
};

const redirectUri = () => `${window.location.origin}/auth/callback`;

// ---- config persistence (survives the redirect round-trip and reloads) ----
export const oidcConfigStore = {
  get(): OidcConfig | null {
    try {
      const raw = localStorage.getItem(CONFIG_KEY);
      return raw ? (JSON.parse(raw) as OidcConfig) : null;
    } catch {
      return null;
    }
  },
  set(cfg: OidcConfig | null) {
    if (cfg) localStorage.setItem(CONFIG_KEY, JSON.stringify(cfg));
    else localStorage.removeItem(CONFIG_KEY);
  },
};

// ---- PKCE helpers ----
function base64url(bytes: Uint8Array): string {
  let s = "";
  bytes.forEach((b) => (s += String.fromCharCode(b)));
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomToken(bytes = 32): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return base64url(buf);
}

async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

/** Redirect the browser to Keycloak's login page (Authorization Code + PKCE). */
export async function beginLogin(cfg: OidcConfig): Promise<void> {
  const verifier = randomToken();
  const state = randomToken(16);
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  oidcConfigStore.set(cfg);
  const params = new URLSearchParams({
    client_id: cfg.clientId,
    response_type: "code",
    scope: "openid profile email",
    redirect_uri: redirectUri(),
    state,
    code_challenge: await pkceChallenge(verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(`${endpoints(cfg.issuer).authorize}?${params}`);
}

async function tokenRequest(cfg: OidcConfig, body: URLSearchParams): Promise<TokenSet> {
  const res = await fetch(endpoints(cfg.issuer).token, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) throw new Error(`Token request failed (${res.status})`);
  return (await res.json()) as TokenSet;
}

/** Handle /auth/callback: validate state, exchange the code for tokens. */
export async function completeLogin(cfg: OidcConfig): Promise<TokenSet> {
  const qs = new URLSearchParams(window.location.search);
  const err = qs.get("error");
  if (err) throw new Error(qs.get("error_description") || err);
  const code = qs.get("code");
  const state = qs.get("state");
  const expectedState = sessionStorage.getItem(STATE_KEY);
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
  if (!code || !verifier || !state || state !== expectedState) {
    throw new Error("Sign-in was interrupted. Please try again.");
  }
  return tokenRequest(
    cfg,
    new URLSearchParams({
      grant_type: "authorization_code",
      client_id: cfg.clientId,
      code,
      redirect_uri: redirectUri(),
      code_verifier: verifier,
    })
  );
}

/** Exchange a refresh token for a new token set. */
export async function refreshTokens(cfg: OidcConfig, refreshToken: string): Promise<TokenSet> {
  return tokenRequest(
    cfg,
    new URLSearchParams({
      grant_type: "refresh_token",
      client_id: cfg.clientId,
      refresh_token: refreshToken,
    })
  );
}

/** RP-initiated logout URL (ends the Keycloak SSO session, then returns to the app). */
export function logoutUrl(cfg: OidcConfig, idToken: string | null): string {
  const params = new URLSearchParams({ post_logout_redirect_uri: window.location.origin });
  if (idToken) params.set("id_token_hint", idToken);
  else params.set("client_id", cfg.clientId);
  return `${endpoints(cfg.issuer).logout}?${params}`;
}
