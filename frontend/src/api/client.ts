import { oidcConfigStore, refreshTokens, type TokenSet } from "../auth/oidc";
import type {
  AuthConfig,
  BridgeResponse,
  BridgeStatus,
  EntityType,
  Job,
  MappingProposal,
  MappingTemplate,
  Problem,
  ProfileSignals,
  TaskEnqueued,
  TaskResult,
  TokenResponse,
  User,
  VoucherPreview,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE || "/api";
const TOKEN_KEY = "tm_token";
const REFRESH_KEY = "tm_refresh_token";
const ID_TOKEN_KEY = "tm_id_token";

export class ApiError extends Error {
  problem: Problem;
  constructor(problem: Problem) {
    super(problem.detail || problem.title);
    this.problem = problem;
  }
}

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string | null) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY)),
  getRefresh: () => localStorage.getItem(REFRESH_KEY),
  getIdToken: () => localStorage.getItem(ID_TOKEN_KEY),
  /** Store an OIDC token set (Keycloak login/refresh). */
  setSession(tokens: TokenSet) {
    localStorage.setItem(TOKEN_KEY, tokens.access_token);
    if (tokens.refresh_token) localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
    if (tokens.id_token) localStorage.setItem(ID_TOKEN_KEY, tokens.id_token);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(ID_TOKEN_KEY);
  },
};

// Single in-flight refresh shared by concurrent 401s.
let refreshing: Promise<boolean> | null = null;

async function tryRefreshSession(): Promise<boolean> {
  const cfg = oidcConfigStore.get();
  const refresh = tokenStore.getRefresh();
  if (!cfg || !refresh) return false;
  refreshing ??= (async () => {
    try {
      tokenStore.setSession(await refreshTokens(cfg, refresh));
      return true;
    } catch {
      tokenStore.clear();
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

async function parseError(res: Response): Promise<never> {
  let problem: Problem;
  try {
    problem = (await res.json()) as Problem;
  } catch {
    problem = { type: "about:blank", title: res.statusText, status: res.status, code: "http_error", detail: null, errors: [] };
  }
  if (res.status === 401) tokenStore.clear();
  throw new ApiError(problem);
}

async function request<T>(method: string, path: string, body?: unknown, isRetry = false): Promise<T> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body; // browser sets multipart boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(`${BASE}${path}`, { method, headers, body: payload });
  // Expired OIDC access token → one silent refresh, then retry the call.
  // (FormData bodies are consumed by the first attempt, so uploads surface the 401 instead.)
  if (res.status === 401 && !isRetry && !(body instanceof FormData) && (await tryRefreshSession())) {
    return request<T>(method, path, body, true);
  }
  if (!res.ok) return parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---- auth ----
export const api = {
  authConfig: () => request<AuthConfig>("GET", "/auth/config"),
  signup: (email: string, password: string, full_name?: string, org_name?: string) =>
    request<TokenResponse>("POST", "/auth/signup", { email, password, full_name, org_name }),
  login: (email: string, password: string) =>
    request<TokenResponse>("POST", "/auth/login", { email, password }),
  me: () => request<User>("GET", "/auth/me"),

  // ---- jobs ----
  createJob: (entity_type: EntityType) => request<Job>("POST", "/jobs", { entity_type }),
  getJob: (id: string) => request<Job>("GET", `/jobs/${id}`),
  uploadFile: (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Job>("POST", `/jobs/${id}/file`, fd);
  },
  getProfile: (id: string) => request<ProfileSignals>("GET", `/jobs/${id}/profile`),
  getSuggestions: (id: string) => request<MappingProposal>("GET", `/jobs/${id}/mapping/suggestions`),
  getVoucherPreview: (id: string) => request<VoucherPreview>("GET", `/jobs/${id}/vouchers/preview`),
  postMapping: (id: string, mapping: Record<string, string | null>, constants: Record<string, string> = {}, template?: string) =>
    request<Job>("POST", `/jobs/${id}/mapping`, { mapping, constants, template: template || null }),

  // long stages return a task; poll with pollTask
  enqueueValidate: (id: string, known_groups?: string[]) =>
    request<TaskEnqueued>("POST", `/jobs/${id}/validate`, { known_groups: known_groups ?? null }),
  enqueueGenerate: (id: string, company?: string, cutover_date?: string, opts?: { b2c_summary?: boolean; settlement_mode?: boolean }) =>
    request<TaskEnqueued>("POST", `/jobs/${id}/generate`, {
      company: company ?? null,
      cutover_date: cutover_date ?? null,
      b2c_summary: opts?.b2c_summary ?? false,
      settlement_mode: opts?.settlement_mode ?? false,
    }),
  enqueuePush: (id: string) => request<TaskEnqueued>("POST", `/jobs/${id}/push`),
  getTask: <T>(id: string, taskId: string) => request<TaskResult<T>>("GET", `/jobs/${id}/tasks/${taskId}`),
  artifactUrl: (id: string) => `${BASE}/jobs/${id}/artifact`,

  // ---- bridge ----
  createBridge: (name?: string) => request<BridgeResponse>("POST", "/bridges", { name }),
  bridgeStatus: () => request<BridgeStatus>("GET", "/bridge/status"),

  // ---- saved mapping templates ----
  listTemplates: (entity_type: EntityType) =>
    request<MappingTemplate[]>("GET", `/templates?entity_type=${encodeURIComponent(entity_type)}`),
  saveTemplate: (t: {
    name: string;
    entity_type: EntityType;
    mapping: Record<string, string | null>;
    constants: Record<string, string>;
    source_columns: string[];
  }) => request<MappingTemplate>("POST", "/templates", t),
  deleteTemplate: (id: string) => request<void>("DELETE", `/templates/${id}`),
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Poll a background task to completion. */
export async function pollTask<T>(jobId: string, taskId: string, opts?: { intervalMs?: number; tries?: number }): Promise<TaskResult<T>> {
  const interval = opts?.intervalMs ?? 700;
  // ~7 min — must comfortably outlast the server-side stage timeouts (bridge dispatch is 120 s),
  // otherwise the UI fabricates a timeout while the task is still running server-side.
  const tries = opts?.tries ?? 600;
  for (let i = 0; i < tries; i++) {
    const r = await api.getTask<T>(jobId, taskId);
    if (r.state !== "pending") return r;
    await sleep(interval);
  }
  return { state: "error", problem: { type: "about:blank", title: "Timed out", status: 504, code: "task_timeout", detail: "The task did not finish in time.", errors: [] } };
}

/** Authenticated download of the generated XML artifact. Uses the server's
 * Content-Disposition filename (Company_Timestamp_Entity.xml); `fallbackName` is only a safety net. */
export async function downloadArtifact(jobId: string, fallbackName: string): Promise<void> {
  const token = tokenStore.get();
  const res = await fetch(api.artifactUrl(jobId), { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) await parseError(res);
  const cd = res.headers.get("Content-Disposition") || "";
  const match = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/i);
  const filename = match ? decodeURIComponent(match[1]) : fallbackName;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
