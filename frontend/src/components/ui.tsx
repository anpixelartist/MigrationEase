import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type ReactNode,
} from "react";
import { T } from "../theme";

export function Spinner({ size = 16, color = T.accent }: { size?: number; color?: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: size,
        height: size,
        border: `2px solid ${color}33`,
        borderTopColor: color,
        borderRadius: "50%",
        animation: "spin .7s linear infinite",
      }}
    />
  );
}

type BtnVariant = "primary" | "secondary" | "ghost";
export function Button({
  variant = "secondary",
  loading,
  children,
  disabled,
  style,
  ...rest
}: { variant?: BtnVariant; loading?: boolean } & ButtonHTMLAttributes<HTMLButtonElement>) {
  const isDisabled = disabled || loading;
  const base: CSSProperties = {
    borderRadius: 10,
    fontSize: 13.5,
    fontWeight: 500,
    padding: "9px 18px",
    whiteSpace: "nowrap",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    border: "1px solid transparent",
    cursor: isDisabled ? "not-allowed" : "pointer",
  };
  const variants: Record<BtnVariant, CSSProperties> = {
    primary: { background: isDisabled ? "#eaeae7" : T.accent, color: isDisabled ? "#b4b4ad" : "#fff" },
    secondary: { background: "#fff", border: "1px solid rgba(0,0,0,.14)", color: T.text },
    ghost: { background: "transparent", color: T.muted },
  };
  return (
    <button disabled={isDisabled} style={{ ...base, ...variants[variant], ...style }} {...rest}>
      {loading && <Spinner size={14} color={variant === "primary" ? "#fff" : T.accent} />}
      {children}
    </button>
  );
}

// ---- Toast ----
interface ToastValue {
  show: (text: string) => void;
}
const ToastCtx = createContext<ToastValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ id: number; text: string } | null>(null);
  const show = useCallback((text: string) => {
    const id = Date.now();
    setToast({ id, text });
    setTimeout(() => setToast((t) => (t && t.id === id ? null : t)), 2800);
  }, []);
  return (
    <ToastCtx.Provider value={{ show }}>
      {children}
      {toast && (
        <div style={{ position: "fixed", bottom: 84, left: "50%", zIndex: 50, animation: "toastIn .35s cubic-bezier(.22,1.3,.4,1)" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              background: "#1c1c1a",
              color: "#fff",
              padding: "11px 16px",
              borderRadius: 11,
              boxShadow: "0 10px 30px rgba(0,0,0,.22)",
              fontSize: 13,
              fontWeight: 500,
            }}
          >
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#4ade80" }} />
            {toast.text}
          </div>
        </div>
      )}
    </ToastCtx.Provider>
  );
}
export function useToast(): ToastValue {
  const c = useContext(ToastCtx);
  if (!c) throw new Error("useToast must be used within ToastProvider");
  return c;
}
