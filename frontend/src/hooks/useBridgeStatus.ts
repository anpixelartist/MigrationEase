import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BridgeStatus } from "../api/types";

/**
 * Live status of the Tally bridge (the local agent that relays imports to Tally
 * Prime's gateway on port 9000). Polls every `intervalMs` and is resilient to
 * transient errors — the UI just keeps showing the last known value.
 */
export function useBridgeStatus(intervalMs = 5000): BridgeStatus | null {
  const [status, setStatus] = useState<BridgeStatus | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.bridgeStatus().then((s) => alive && setStatus(s)).catch(() => {});
    load();
    const iv = setInterval(load, intervalMs);
    return () => { alive = false; clearInterval(iv); };
  }, [intervalMs]);
  return status;
}
