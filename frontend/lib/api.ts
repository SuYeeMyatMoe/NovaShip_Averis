"use client";

/**
 * Typed API client. Auth: the session token from POST /auth/login is sent as
 * `Authorization: Bearer`. A 401 clears the session and sends the person to /login.
 */
// Local Next.js development talks to standalone FastAPI on :8000.
 // Vercel Services routes production FastAPI under same-origin /api.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ||
  (process.env.NODE_ENV === "production" ? "/api" : "http://localhost:8000");

export const SEVEN_FIELDS = ["shipper", "consignee", "notify_party", "port_of_loading", "port_of_discharge", "container_count", "gross_weight_kg"] as const;
export const FIELD_LABELS: Record<string, string> = {
  shipper: "Shipper", consignee: "Consignee", notify_party: "Notify Party", port_of_loading: "Port of Loading",
  port_of_discharge: "Port of Discharge", container_count: "Container Count", gross_weight_kg: "Gross Weight (kg)",
};

// ------------------------------------------------------------------ session
export type MailboxProviders = { google: boolean; microsoft: boolean; shared_mailbox_configured: boolean; mailbox_storage_ready?: boolean };
export type Mailbox = { connected: boolean; provider?: string; address?: string; scopes?: string[]; status?: string; can_send?: boolean; can_read?: boolean; connected_at?: string; last_polled_at?: string | null; last_error?: string | null; providers?: MailboxProviders };
export type SessionUser = { id: string; email: string; display_name: string; roles: string[]; permissions: string[]; team_id?: string | null; mailbox?: Mailbox };
export type Session = { token: string; expires_at: string; user: SessionUser };
const SESSION_KEY = "novaship.session";
export const AUTH_PATHS = ["/login", "/register", "/auth/callback"];

export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    if (!s?.token || (s.expires_at && new Date(s.expires_at).getTime() < Date.now())) { localStorage.removeItem(SESSION_KEY); return null; }
    return s;
  } catch { return null; }
}
export function setSession(s: Session) { try { localStorage.setItem(SESSION_KEY, JSON.stringify(s)); } catch {} }
export function clearSession() { try { localStorage.removeItem(SESSION_KEY); localStorage.removeItem("novaship.user"); localStorage.removeItem("novaship.jwt"); } catch {} }

/** Send the browser to /login, remembering where it was. No-op on the auth pages themselves. */
export function redirectToLogin() {
  if (typeof window === "undefined") return;
  const here = window.location.pathname + window.location.search;
  if (AUTH_PATHS.some((p) => window.location.pathname.startsWith(p))) return;
  window.location.assign(`/login?next=${encodeURIComponent(here)}`);
}

export async function login(email: string, password: string): Promise<Session> {
  const s = await api<Session>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }, { auth: false });
  setSession(s); return s;
}
export async function register(body: { email: string; password: string; display_name: string; role?: string }): Promise<Session> {
  const s = await api<Session>("/auth/register", { method: "POST", body: JSON.stringify(body) }, { auth: false });
  setSession(s); return s;
}
/** Sign in / sign up with Google (also connects that Gmail). With a session, the grant links Gmail to the current account. Outlook: see startMicrosoft. */
export async function startGoogle(opts: { role?: string; next?: string } = {}): Promise<void> {
  const q = new URLSearchParams();
  if (opts.role) q.set("role", opts.role);
  if (opts.next) q.set("next", opts.next);
  const r = await api<{ url: string }>(`/auth/google/start?${q.toString()}`, {}, { redirectOn401: false });
  window.location.assign(r.url);
}
/**
 * Sign in / sign up with Microsoft (identity only), or with a session connect the Outlook mailbox
 * (`intent: "connect"`, asks for Mail.Read + Mail.Send in a second consent).
 */
export async function startMicrosoft(opts: { role?: string; next?: string; intent?: "login" | "connect" } = {}): Promise<void> {
  const q = new URLSearchParams({ intent: opts.intent || "login" });
  if (opts.role) q.set("role", opts.role);
  if (opts.next) q.set("next", opts.next);
  const r = await api<{ url: string }>(`/auth/microsoft/start?${q.toString()}`, {}, { redirectOn401: false });
  window.location.assign(r.url);
}
export type FragmentResult = { session: Session; next: string; isNew: boolean; mailbox: string; provider: string; connectedOnly: boolean };
/**
 * Result handed back by GET /auth/google|microsoft/callback in the URL fragment. A login carries a token
 * (completed by loading /auth/session); a mailbox connect carries only `connected=` and reuses the current session.
 */
export async function adoptSessionFromFragment(fragment: string): Promise<FragmentResult | null> {
  const p = new URLSearchParams(fragment.replace(/^#/, ""));
  const token = p.get("token");
  const next = p.get("next") || "/welcome";
  if (!token) {
    const existing = getSession();
    if (!p.get("connected") || !existing) return null;
    const user = await api<SessionUser>("/auth/session", {}, { redirectOn401: false });
    const session = { ...existing, user };
    setSession(session);
    return { session, next, isNew: false, mailbox: p.get("mailbox") || "", provider: p.get("connected") || "", connectedOnly: true };
  }
  const draft: Session = { token, expires_at: p.get("expires_at") || "", user: { id: "", email: "", display_name: "", roles: [], permissions: [] } };
  setSession(draft);
  const user = await api<SessionUser>("/auth/session", {}, { redirectOn401: false });
  const session = { ...draft, user };
  setSession(session);
  return { session, next, isNew: p.get("new") === "1", mailbox: p.get("mailbox") || "", provider: p.get("provider") || "google", connectedOnly: false };
}
export const getMailbox = () => api<Mailbox>("/me/mailbox");
/** The original attachment bytes, fetched with the session (a plain link would arrive without the Authorization header). */
export async function fetchDocumentBlob(caseId: string, attachmentId: string): Promise<{ blob: Blob; filename: string; mediaType: string }> {
  const session = getSession();
  const headers: Record<string, string> = session ? { Authorization: `Bearer ${session.token}` } : {};
  const res = await fetch(`${API_BASE}/cases/${caseId}/documents/${attachmentId}/raw`, { headers, cache: "no-store" });
  if (res.status === 401) { clearSession(); redirectToLogin(); }
  if (!res.ok) {
    let detail: any = null;
    try { detail = (await res.json())?.detail; } catch {}
    throw new ApiError(res.status, detail || { error: `download failed (${res.status})` });
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  return { blob, filename: m ? decodeURIComponent(m[1]) : "attachment", mediaType: res.headers.get("Content-Type") || blob.type || "application/octet-stream" };
}
export const disconnectMailbox = () => api<{ ok: boolean; address: string; provider: string; google_revoked: boolean; note?: string | null }>("/me/mailbox", { method: "DELETE" });
export const GOOGLE_ERRORS: Record<string, string> = {
  google_denied: "Google sign-in was cancelled.",
  bad_state: "The sign-in link expired or was already used. Try again.",
  exchange_failed: "Google did not accept the sign-in. Check the OAuth client and redirect URI.",
  no_refresh_token: "Google did not grant offline access. Remove NovaShip at myaccount.google.com/permissions and try again.",
  userinfo_failed: "Could not read the Google account profile.",
  email_unverified: "That Google account has no verified email address.",
  registration_disabled: "Self-registration is disabled. Ask an Admin for an account.",
  role_not_allowed: "That role cannot be self-assigned.",
  registration_unavailable: "Registration is not available on this backend.",
  unknown_user: "The account to connect no longer exists.",
  google_disabled: "Google sign-in is disabled in this authentication mode.",
  storage_failed: "Signed in, but the mailbox could not be saved. Apply supabase/migrations/0007_user_mailboxes.sql and try again.",
  microsoft_denied: "Microsoft sign-in was cancelled.",
  microsoft_error: "Microsoft could not complete the sign-in (provider error). Try again; if it repeats, sign out at login.live.com and reconnect.",
  microsoft_disabled: "Microsoft sign-in is disabled in this authentication mode.",
  mail_permission_missing: "Outlook was not connected: the mail permissions were not granted. Try again and accept 'Read your mail' and 'Send mail as you'.",
  mailbox_table_missing: "Mailbox storage is not set up on the server (migration 0007). Run `python backend/scripts/apply_migrations.py` with SUPABASE_DB_URL set, then connect again.",
};
export const CONNECT_ERROR_CODES = new Set(["storage_failed", "mailbox_table_missing", "mail_permission_missing", "unknown_user", "exchange_failed", "userinfo_failed", "no_refresh_token", "bad_state", "microsoft_denied", "microsoft_error", "google_denied"]);

export async function logout(): Promise<void> {
  try { await api("/auth/logout", { method: "POST" }, { redirectOn401: false }); } catch {}
  clearSession();
}

export class ApiError extends Error {
  status: number; detail: any;
  constructor(status: number, detail: any) { super(typeof detail === "string" ? detail : detail?.error || `HTTP ${status}`); this.status = status; this.detail = detail; }
}

export async function api<T = any>(path: string, init: RequestInit = {}, opts: { auth?: boolean; redirectOn401?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as any) };
  const session = opts.auth === false ? null : getSession();
  if (session) headers["Authorization"] = `Bearer ${session.token}`;
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers, cache: "no-store" });
  const text = await res.text();
  let data: any = text;
  try { data = text ? JSON.parse(text) : null; } catch {}
  if (res.status === 401 && opts.auth !== false && opts.redirectOn401 !== false) { clearSession(); redirectToLogin(); }
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data);
  return data as T;
}

export const post = <T = any>(path: string, body?: any) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T = any>(path: string, body?: any) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });

// ------------------------------------------------------------------ types (mirror backend contracts)
export type Evidence = { document_id?: string | null; page?: number | null; snippet: string; label_found?: string | null; line?: number | null };
export type ComparisonField = {
  field: string; label: string; si_original: string | null; bl_original: string | null; si_normalized: any; bl_normalized: any;
  result: "MATCH" | "MISMATCH" | "MISSING_IN_SI" | "MISSING_IN_BL" | "LOW_CONFIDENCE_REVIEW"; confidence: number; reason: string; attention: string;
  si_evidence: Evidence; bl_evidence: Evidence;
};
export type Comparison = { comparison_status: string; mismatch_count: number; required_field_count: number; message: string; fields: ComparisonField[]; mismatch_fields: string[]; review_fields: string[]; review_reason: string | null; compared_at: string };
export type DeliveryInfo = { mode: "live" | "simulate"; provider: "outlook" | "gmail" | "shared" | "simulate"; from_address?: string | null; to: string[]; provider_id?: string | null; sent_at: string; mailbox_user_id?: string | null; verified?: boolean | null; verified_at?: string | null; sent_item_id?: string | null; internet_message_id?: string | null; bounce?: { subject?: string; received_at?: string; snippet?: string; from?: string } | null; note?: string | null };
export type Draft = { id: string; draft_type: string; to: string[]; cc: string[]; subject: string; body: string; status: string; version: number; requires_external_approval: boolean; generated_by: string; evidence_refs: string[]; delivery?: DeliveryInfo | null };
export type Attachment = { id: string; file_name: string; file_type: string; size_bytes: number; checksum: string; detected_type: string; detection_confidence: number; extraction_status: string; extraction_confidence: number; raw_text: string | null; page_count: number | null; is_duplicate_of: string | null; reader_note?: string | null; ocr?: boolean };
export type CaseView = {
  id: string; source_email_id: string; intent: string; hackathon_category: string; action_required: boolean; priority: string; status: string;
  security: { outcome: string; score: number; signals: { signal: string; severity: string; evidence: string; recommended_action: string }[]; rationale: string };
  classification: { intent: string; confidence: number; rationale: string; decided_by: string };
  mismatch_count: number; comparison_status: string | null; review_reason: string | null; confidence: number; si_available: boolean; bl_available: boolean;
  si_document_id: string | null; bl_document_id: string | null; assigned_user_id: string | null; assigned_team_id: string | null; shared_with: string[];
  summary: { text: string; generated_by: string; evidence_refs: string[] } | null;
  recommendation: { action_required: boolean; action_type: string; priority: string; reason: string; recommended_action: string; responsible_role: string; confidence: number } | null;
  comparison: Comparison | null; drafts: Draft[]; anomalies: { signal: string; severity: string; evidence: string; recommended_action: string }[];
  errors: { id: string; category: string; step: string; message: string; recovery: string; retryable: boolean; resolved: boolean }[];
  trace: { node: string; actor_type: string; started_at: string; finished_at: string; output: any }[]; processing_ms: number; created_at: string; updated_at: string;
  email: { id: string; sender: string; subject: string; body: string; received_at: string; language: string; attachments: Attachment[]; recipients: string[]; cc: string[] } | null;
};
export type CaseRow = {
  id: string; email_id: string; subject: string; sender: string; received_at: string | null; intent: string; category: string; security: string; action_required: boolean; priority: string;
  si_available: boolean; bl_available: boolean; attachments: number; mismatch_count: number; comparison_status: string | null; review_reason: string | null; confidence: number;
  assigned_user_id: string | null; shared_with: string[]; status: string; updated_at: string; summary: string; errors: number; drafts: number;
  mailbox_user_id?: string | null; mailbox?: string | null;
  agent_run?: AgentRunSummary | null;
};
/** Last AI-agent run on a case; absent = the agent has not run it yet ("pending"). */
export type AgentRunSummary = { result: "completed" | "paused" | "error"; last_run_at: string; last_run_by: string; runs: number; ms: number; mode: "single" | "batch"; status_after: string; decision?: string | null; error?: string | null };
export type HistoryRow = { run_at: string; case_id: string; subject: string; sender: string; mailbox: string; run_by: string; run_by_name: string; result: "done" | "paused" | "error"; status_after: string; status: string; mismatch_count: number; comparison_status: string; decision: string; ms: number; runs: number; mode: string; priority: string; error: string };
export const agentStateOf = (r: { agent_run?: AgentRunSummary | null }): "pending" | "paused" | "done" => !r.agent_run || r.agent_run.result === "error" ? "pending" : r.agent_run.result === "paused" ? "paused" : "done";
/** Download an authenticated file (Excel/CSV exports) with the session header; a plain link would arrive without it. */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const session = getSession();
  const headers: Record<string, string> = session ? { Authorization: `Bearer ${session.token}` } : {};
  const res = await fetch(`${API_BASE}${path}`, { headers, cache: "no-store" });
  if (res.status === 401) { clearSession(); redirectToLogin(); }
  if (!res.ok) {
    let detail: any = null;
    try { detail = (await res.json())?.detail; } catch {}
    throw new ApiError(res.status, detail || { error: `download failed (${res.status})` });
  }
  const blob = await res.blob();
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(res.headers.get("Content-Disposition") || "");
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = m ? decodeURIComponent(m[1]) : fallbackName; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
export type Metrics = Record<string, any>;
/** A policy change the desk learned from security-gate decisions; only an Admin can turn it into a policy version. */
export type PolicySuggestion = { id: string; recipe: string; bucket: string; count: number; needed: number; title: string; rationale: string; section: string; key: string; from_value: any; to_value: any; proposed_section: Record<string, any>; evidence: { case_id: string; subject: string; sender: string; outcome: string; status: string; when: string; by: string }[] };
export type PolicySuggestions = { items: PolicySuggestion[]; total: number; progress: { recipe: string; kind?: string; label?: string; bucket: string; count: number; needed: number }[]; settings: Record<string, any> };
export type NotificationItem = { kind?: "needs_person"; case_id: string; subject: string; status: string; priority: string; reason: string; updated_at: string };
/** A case that arrived through the signed-in user's own mailbox (Outlook/Gmail) in the last 48 h. */
export type NewMailItem = { kind: "new_mail"; case_id: string; email_id: string; mailbox?: string | null; subject: string; sender: string; status: string; priority: string; action_required: boolean; received_at: string; created_at: string };
/** A case a colleague shared with the signed-in user in the last 7 days. */
export type SharedItem = { kind: "shared"; share_id: string; case_id: string; shared_by: string; shared_by_name: string; subject: string; message: string; status: string; priority: string; shared_at: string };
export type NotificationFeed = { items: NotificationItem[]; total: number; new_mail?: NewMailItem[]; new_mail_total?: number; shared?: SharedItem[]; shared_total?: number };
