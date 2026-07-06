import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { tokenStore } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { completeLogin, oidcConfigStore } from "../auth/oidc";
import { Spinner } from "../components/ui";
import { T } from "../theme";

/** OIDC redirect target: exchanges the authorization code for tokens, then enters the app. */
export default function AuthCallback() {
  const { reload } = useAuth();
  const nav = useNavigate();
  const [err, setErr] = useState<string | null>(null);
  const ran = useRef(false); // React 18 StrictMode double-mount guard — the code is single-use

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    (async () => {
      try {
        const cfg = oidcConfigStore.get();
        if (!cfg) throw new Error("SSO is not configured.");
        tokenStore.setSession(await completeLogin(cfg));
        await reload(); // backend JIT-provisions / links the account on this first call
        nav("/", { replace: true });
      } catch (ex) {
        setErr(ex instanceof Error ? ex.message : "Sign-in failed.");
      }
    })();
  }, [nav, reload]);

  return (
    <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 14 }}>
      {err ? (
        <>
          <div style={{ color: T.err, fontSize: 14 }}>{err}</div>
          <a href="/" style={{ color: T.accent, fontSize: 13.5 }}>Back to sign-in</a>
        </>
      ) : (
        <>
          <Spinner size={26} />
          <div style={{ color: T.muted, fontSize: 13.5 }}>Completing sign-in…</div>
        </>
      )}
    </div>
  );
}
