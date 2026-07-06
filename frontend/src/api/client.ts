import type {
  BridgeResponse,
  BridgeStatus,
  EntityType,
  Job,
  MappingProposal,
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
};

async function parseError(res: Response): Promise<never> {
  let problem: Problem;
  try {
    problem = (await res.json()) as Problem;
  } catch {
    problem = { type: "about:blank", title: res.statusText, status: res.status, code: "http_error", detail: null, errors: [] };
  }
  if (res.status === 401) tokenStore.set(null);
  throw new ApiError(problem);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
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
  if (!res.ok) return parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---- auth ----
export const api = {
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
  enqueueGenerate: (id: string, company?: string, cutover_date?: string) =>
    request<TaskEnqueued>("POST", `/jobs/${id}/generate`, { company: company ?? null, cutover_date: cutover_date ?? null }),
  enqueuePush: (id: string) => request<TaskEnqueued>("POST", `/jobs/${id}/push`),
  getTask: <T>(id: string, taskId: string) => request<TaskResult<T>>("GET", `/jobs/${id}/tasks/${taskId}`),
  artifactUrl: (id: string) => `${BASE}/jobs/${id}/artifact`,

  // ---- bridge ----
  createBridge: (name?: string) => request<BridgeResponse>("POST", "/bridges", { name }),
  bridgeStatus: () => request<BridgeStatus>("GET", "/bridge/status"),
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Poll a background task to completion. */
export async function pollTask<T>(jobId: string, taskId: string, opts?: { intervalMs?: number; tries?: number }): Promise<TaskResult<T>> {
  const interval = opts?.intervalMs ?? 700;
  const tries = opts?.tries ?? 120;
  for (let i = 0; i < tries; i++) {
    const r = await api.getTask<T>(jobId, taskId);
    if (r.state !== "pending") return r;
    await sleep(interval);
  }
  return { state: "error", problem: { type: "about:blank", title: "Timed out", status: 504, code: "task_timeout", detail: "The task did not finish in time.", errors: [] } };
}

/** Authenticated download of the generated XML artifact. */
export async function downloadArtifact(jobId: string, filename: string): Promise<void> {
  const token = tokenStore.get();
  const res = await fetch(api.artifactUrl(jobId), { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) await parseError(res);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
