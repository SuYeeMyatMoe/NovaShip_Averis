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
export type Mailbox = { connected: boolean; provider?: string; address?: string; scopes?: string[]; status?: string; can_send?: boolean; connected_at?: string; last_polled_at?: string | null; last_error?: string | null };
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
/** Sign in / sign up with Google (also connects that Gmail). With a session, the grant links Gmail to the current account. */
export async function startGoogle(opts: { role?: string; next?: string } = {}): Promise<void> {
  const q = new URLSearchParams();
  if (opts.role) q.set("role", opts.role);
  if (opts.next) q.set("next", opts.next);
  const r = await api<{ url: string }>(`/auth/google/start?${q.toString()}`, {}, { redirectOn401: false });
  window.location.assign(r.url);
}
/** Session handed back by GET /auth/google/callback in the URL fragment; completes it by loading /auth/session. */
export async function adoptSessionFromFragment(fragment: string): Promise<{ session: Session; next: string; isNew: boolean; mailbox: string } | null> {
  const p = new URLSearchParams(fragment.replace(/^#/, ""));
  const token = p.get("token");
  if (!token) return null;
  const draft: Session = { token, expires_at: p.get("expires_at") || "", user: { id: "", email: "", display_name: "", roles: [], permissions: [] } };
  setSession(draft);
  const user = await api<SessionUser>("/auth/session", {}, { redirectOn401: false });
  const session = { ...draft, user };
  setSession(session);
  return { session, next: p.get("next") || "/welcome", isNew: p.get("new") === "1", mailbox: p.get("mailbox") || "" };
}
export const getMailbox = () => api<Mailbox>("/me/mailbox");
export const disconnectMailbox = () => api<{ ok: boolean; address: string; google_revoked: boolean }>("/me/mailbox", { method: "DELETE" });
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
  storage_failed: "Signed in with Google, but the mailbox could not be saved. Apply supabase/migrations/0007_user_mailboxes.sql and try again.",
};

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
export type Draft = { id: string; draft_type: string; to: string[]; cc: string[]; subject: string; body: string; status: string; version: number; requires_external_approval: boolean; generated_by: string; evidence_refs: string[] };
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
};
export type Metrics = Record<string, any>;
export type NotificationItem = { case_id: string; subject: string; status: string; priority: string; reason: string; updated_at: string };
