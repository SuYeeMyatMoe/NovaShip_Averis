"use client";
import React from "react";

export const STATUS_COLORS: Record<string, string> = {
  RECEIVED: "bg-ink-100 text-ink-700", SECURITY_CHECK: "bg-ink-100 text-ink-700", SECURITY_REVIEW: "bg-mismatch-bg text-mismatch-fg",
  CLASSIFIED: "bg-accent-bg text-accent-fg", NO_ACTION_INFO: "bg-ink-100 text-ink-600", DOCUMENTS_DETECTED: "bg-accent-bg text-accent-fg",
  WAITING_DOCUMENTS: "bg-review-bg text-review-fg", EXTRACTING: "bg-accent-bg text-accent-fg", COMPARING: "bg-accent-bg text-accent-fg",
  NO_MISMATCH_DETECTED: "bg-match-bg text-match-fg", MISMATCH_DETECTED: "bg-mismatch-bg text-mismatch-fg", HUMAN_REVIEW: "bg-review-bg text-review-fg",
  DRAFT_READY: "bg-accent-bg text-accent-fg", NOTIFY_PARTY: "bg-accent-soft text-accent-fg", AWAITING_RESPONSE: "bg-accent-soft text-accent-fg",
  ASSIGNED: "bg-accent-bg text-accent-fg", COMPLETED: "bg-match-bg text-match-fg", ERROR: "bg-mismatch-bg text-mismatch-fg",
};
export const PRIORITY_COLORS: Record<string, string> = { LOW: "text-ink-500", MEDIUM: "text-accent", HIGH: "text-review", CRITICAL: "text-mismatch font-semibold" };
export const RESULT_STYLES: Record<string, { badge: string; row: string; label: string }> = {
  MATCH: { badge: "bg-match-bg text-match-fg border-match/30", row: "opacity-80", label: "Match" },
  MISMATCH: { badge: "bg-mismatch text-white border-mismatch", row: "bg-mismatch-bg/60 ring-1 ring-mismatch/40", label: "Mismatch" },
  MISSING_IN_SI: { badge: "bg-review-bg text-review-fg border-review/40", row: "bg-review-bg/50", label: "Missing in SI" },
  MISSING_IN_BL: { badge: "bg-review-bg text-review-fg border-review/40", row: "bg-review-bg/50", label: "Missing in BL" },
  LOW_CONFIDENCE_REVIEW: { badge: "bg-review-bg text-review-fg border-review/40", row: "bg-review-bg/50", label: "Low confidence" },
};

export function Badge({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <span className={`inline-flex items-center rounded-full border border-transparent px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${className}`}>{children}</span>;
}
export function StatusBadge({ status }: { status: string }) {
  return <Badge className={STATUS_COLORS[status] || "bg-ink-100 text-ink-700"}>{status.replace(/_/g, " ")}</Badge>;
}
export function Confidence({ value, label = "confidence" }: { value: number; label?: string }) {
  const pct = Math.min(100, Math.max(0, Math.round(value * 100)));
  const color = value >= 0.85 ? "bg-match" : value >= 0.6 ? "bg-review" : "bg-mismatch";
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-xs text-ink-500" title={`${label}: ${value.toFixed(2)}`}>
      <span className="h-1.5 w-12 shrink-0 overflow-hidden rounded-full bg-ink-100"><span className={`block h-full max-w-full rounded-full ${color}`} style={{ width: `${pct}%` }} /></span>
      <span className="font-mono">{value.toFixed(2)}</span>
    </span>
  );
}
export function Card({ title, children, className = "", right }: { title?: React.ReactNode; children: React.ReactNode; className?: string; right?: React.ReactNode }) {
  return (
    <section className={`w-full min-w-0 max-w-full rounded-2xl border border-ink-200 bg-white/95 shadow-card ${className}`}>
      {title && <header className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-ink-100 px-4 py-3 sm:px-5"><h3 className="min-w-0 break-words text-sm font-semibold text-ink-800">{title}</h3><div className="max-w-full">{right}</div></header>}
      <div className="min-w-0 p-3 sm:p-4">{children}</div>
    </section>
  );
}
export function Button({ children, onClick, kind = "default", disabled, type = "button", title, className = "" }: { children: React.ReactNode; onClick?: () => void; kind?: "default" | "primary" | "danger" | "ghost" | "success"; disabled?: boolean; type?: "button" | "submit"; title?: string; className?: string }) {
  const styles = {
    default: "border-ink-200 bg-white text-ink-800 hover:bg-ink-50", primary: "border-accent bg-accent text-white hover:bg-accent-hover",
    danger: "border-mismatch bg-white text-mismatch hover:bg-mismatch-bg", ghost: "border-transparent bg-transparent text-ink-600 hover:bg-ink-100",
    success: "border-match bg-match text-white hover:bg-green-700",
  }[kind];
  return <button type={type} title={title} disabled={disabled} onClick={onClick} className={`rounded-lg border px-3 py-2 text-xs font-semibold transition duration-200 hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}>{children}</button>;
}
export function KV({ k, v, mono }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return <div className="flex gap-2 text-sm"><span className="w-24 shrink-0 text-ink-500 sm:w-40">{k}</span><span className={`min-w-0 break-words text-ink-900 ${mono ? "font-mono text-xs" : ""}`}>{v ?? "—"}</span></div>;
}
export function Empty({ text }: { text: string }) { return <div className="rounded-xl border border-dashed border-ink-200 bg-ink-50/50 p-6 text-center text-sm text-ink-500">{text}</div>; }
export function fmtDate(s?: string | null) { if (!s) return "—"; const d = new Date(s); return isNaN(d.getTime()) ? s : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); }
export function Toast({ msg, kind }: { msg: string; kind: "ok" | "err" }) {
  return <div className={`fixed bottom-4 right-4 z-50 rounded-xl px-4 py-3 text-sm shadow-lg ${kind === "ok" ? "bg-ink-900 text-white" : "bg-mismatch text-white"}`}>{msg}</div>;
}

/** Operator signal raised by a mutation (`operator_warning` in API responses / `detail.operator_warning` on errors). */
export type OperatorWarning = { signal: string; severity: string; evidence: string; recommended_action: string; dialog?: boolean };

/** Modal warning box for unusual operator behaviour. Warnings never block: the only action is to acknowledge. */
export function WarningDialog({ warning, onClose }: { warning: OperatorWarning | null; onClose: () => void }) {
  const ref = React.useRef<HTMLButtonElement>(null);
  React.useEffect(() => {
    if (!warning) return;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [warning, onClose]);
  if (!warning) return null;
  const auto = warning.signal === "AUTO_DRAFT_AFTER_REPEATED_ACTIONS";
  const tone = auto ? "border-accent bg-accent-bg text-accent-fg" : warning.severity === "LOW" ? "border-review bg-review-bg text-review-fg" : "border-mismatch bg-mismatch-bg text-mismatch-fg";
  return (
    <div role="presentation" className="fixed inset-0 z-[60] flex items-center justify-center bg-ink-900/40 p-4" onClick={onClose}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="warning-title" aria-describedby="warning-body" onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-2xl border border-ink-200 bg-white p-5 shadow-xl">
        <div className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide ${tone}`}>
          <span aria-hidden>{auto ? "✎" : "⚠"}</span>{auto ? "Draft saved" : `Operator warning · ${warning.severity}`}
        </div>
        <h2 id="warning-title" className="mt-3 text-base font-semibold text-ink-900">{warning.signal.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase())}</h2>
        <p id="warning-body" className="mt-2 text-sm text-ink-700">{warning.evidence}</p>
        <p className="mt-2 rounded-lg bg-ink-50 px-3 py-2 text-xs text-ink-600"><b>What to do:</b> {warning.recommended_action}</p>
        <p className="mt-2 text-[11px] text-ink-500">This is a warning only. Nothing was sent and your account is not locked. It is recorded in the audit trail.</p>
        <div className="mt-4 flex justify-end">
          <button ref={ref} type="button" onClick={onClose} className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-white shadow-glow transition hover:bg-accent-hover">I understand</button>
        </div>
      </div>
    </div>
  );
}
