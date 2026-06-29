import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, tokenStore } from "../api/client";
import type { User } from "../api/types";

interface AuthValue {
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, full_name?: string, org_name?: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (tokenStore.get()) {
      api
        .me()
        .then(setUser)
        .catch(() => tokenStore.set(null))
        .finally(() => setReady(true));
    } else {
      setReady(true);
    }
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
  const logout = () => {
    tokenStore.set(null);
    setUser(null);
  };

  return <Ctx.Provider value={{ user, ready, login, signup, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthValue {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth must be used within AuthProvider");
  return c;
}
