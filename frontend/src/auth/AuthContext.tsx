import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, tokenStore } from "../api/client";
import type { AuthConfig, User } from "../api/types";
import { beginLogin, logoutUrl, oidcConfigStore } from "./oidc";

interface AuthValue {
  user: User | null;
  ready: boolean;
  /** Auth mode advertised by the backend (null while loading). */
  authConfig: AuthConfig | null;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, full_name?: string, org_name?: string) => Promise<void>;
  /** Redirect to Keycloak (Authorization Code + PKCE). */
  loginSso: () => Promise<void>;
  /** Re-fetch the current user (used after the OIDC callback stored tokens). */
  reload: () => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);

  useEffect(() => {
    // Bootstrap: learn the auth mode (public endpoint), then restore any existing session.
    (async () => {
      try {
        const cfg = await api.authConfig();
        setAuthConfig(cfg);
        if (cfg.issuer && cfg.client_id) oidcConfigStore.set({ issuer: cfg.issuer, clientId: cfg.client_id });
      } catch {
        setAuthConfig({ mode: "legacy", issuer: null, client_id: null }); // older backend: password auth
      }
      if (tokenStore.get()) {
        try {
          setUser(await api.me());
        } catch {
          tokenStore.clear();
        }
      }
      setReady(true);
    })();
  }, []);

  const login = async (email: string, password: string) => {
    const r = await api.login(email, password);
    tokenStore.set(r.access_token);
    setUser(r.user);
  };
  const signup = async (email: string, password: string, full_name?: string, org_name?: string) => {
    const r = await api.signup(email, password, full_name, org_name);
    tokenStore.set(r.access_token);
    setUser(r.user);
  };
  const loginSso = async () => {
    const cfg = oidcConfigStore.get();
    if (!cfg) throw new Error("SSO is not configured.");
    await beginLogin(cfg);
  };
  const reload = async () => {
    setUser(await api.me());
  };
  const logout = () => {
    const cfg = oidcConfigStore.get();
    const idToken = tokenStore.getIdToken();
    const hadOidcSession = Boolean(cfg && tokenStore.getRefresh());
    tokenStore.clear();
    setUser(null);
    // End the Keycloak SSO session too, otherwise the next login silently re-authenticates.
    if (cfg && hadOidcSession) window.location.assign(logoutUrl(cfg, idToken));
  };

  return (
    <Ctx.Provider value={{ user, ready, authConfig, login, signup, loginSso, reload, logout }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthValue {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth must be used within AuthProvider");
  return c;
}
