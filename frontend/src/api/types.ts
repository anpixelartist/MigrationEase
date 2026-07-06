// Mirrors the FastAPI backend responses.

export type EntityType = "ledger" | "group" | "stock_item" | "unit" | "voucher";

export interface OrgMembership {
  org_id: string;
  name: string;
  role: string;
}
export interface User {
  id: string;
  email: string;
  full_name: string | null;
  orgs: OrgMembership[];
}
export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}
export interface AuthConfig {
  mode: "legacy" | "hybrid" | "keycloak";
  issuer: string | null;
  client_id: string | null;
}

export interface Job {
  id: string;
  entity_type: EntityType;
  status: string;
  filename: string | null;
  company: string | null;
  rows: number | null;
  columns: number | null;
  notes: string[];
  created_at: string;
  updated_at: string;
}

export interface ColumnProfile {
  name: string;
  inferred_type: string;
  count: number;
  null_pct: number;
  distinct: number;
  distinct_pct: number;
  samples: string[];
  is_candidate_key: boolean;
}
export interface ProfileSignals {
  row_count: number;
  column_count: number;
  columns: ColumnProfile[];
}

export interface FieldSuggestion {
  target_field: string;
  label: string;
  required: boolean;
  source_column: string | null;
  confidence: number;
  status: "auto_accept" | "needs_confirm" | "unmapped";
  method: string;
  alternatives: [string, number][];
}
export interface MappingProposal {
  entity: EntityType;
  suggestions: FieldSuggestion[];
  unmapped_sources: string[];
  unmapped_required: string[];
}

export interface ErrorEnvelope {
  code: string;
  severity: "error" | "warning" | "info";
  stage: string;
  entity: string | null;
  source_row: number | null;
  source_column: string | null;
  target_field: string | null;
  message: string;
  raw: string | null;
  suggestion: string | null;
}
export interface ValidationResult {
  ok: boolean;
  stats: Record<string, number>;
  errors: ErrorEnvelope[];
}

export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail: string | null;
  errors: unknown[];
}

export interface TaskEnqueued {
  task_id: string;
  state: "pending";
}
export interface TaskResult<T = unknown> {
  state: "pending" | "done" | "error";
  result?: T;
  problem?: Problem;
}

export interface GenerateSummary {
  status: string;
  generated: number;
  held_conflicts: number;
  skipped_rows: number;
  bytes: number;
  debit_total?: string;
  credit_total?: string;
  convert_errors: ErrorEnvelope[];
}
export interface PushResult {
  status: string;
  created: number;
  altered: number;
  ignored: number;
  errors: number;
  exceptions: number;
  line_errors: string[];
  is_success: boolean;
}

export interface VoucherLinePreview {
  ledger_name: string;
  dr_cr: "Dr" | "Cr";
  amount: string;
  stock_item: string | null;
  quantity: string | null;
  unit: string | null;
}
export interface VoucherPreviewItem {
  voucher_number: string | null;
  date: string;
  voucher_type: string;
  party_ledger: string | null;
  debit_total: string;
  credit_total: string;
  balanced: boolean;
  lines: VoucherLinePreview[];
}
export interface VoucherPreview {
  count: number;
  error_count: number;
  warning_count: number;
  vouchers: VoucherPreviewItem[];
}

export interface BridgeResponse {
  bridge_id: string;
  name: string | null;
  api_key: string;
  online: boolean;
}
export interface BridgeStatus {
  online: boolean;
  company_guid: string | null;
}
