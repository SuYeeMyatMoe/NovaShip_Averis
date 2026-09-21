"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, post } from "@/lib/api";
import { Badge, Button, Card, Empty, StatusBadge, Toast } from "@/components/ui";

const GATE_LABEL: Record<string, string> = { archived: "archived", reviewing: "sent to review", no_action: "no action", blocked: "blocked sender" };
const GATE_TONE: Record<string, string> = { archived: "bg-ink-100 text-ink-700", reviewing: "bg-review-bg text-review-fg", no_action: "bg-ink-100 text-ink-600", blocked: "bg-mismatch-bg text-mismatch-fg" };
const TONE: Record<string, string> = { SECURITY_REVIEW: "bg-mismatch text-white", SPAM: "bg-mismatch-bg text-mismatch-fg", SUSPICIOUS: "bg-review-bg text-review-fg", SAFE: "bg-match-bg text-match-fg" };

/** Security agent queue: everything the precheck + security agent flagged, with evidence and one-click actions. */
export default function SecurityPage() {
  const [rows, setRows] = useState<any[] | null>(null);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const say = (msg: string, kind: "ok" | "err" = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 3500); };
  const [handled, setHandled] = useState<any[] | null>(null);
  const [handledCount, setHandledCount] = useState(0);
  // open = still waiting at the gate (the working queue); handled = archived / sent to review / no action / blocked sender
  const load = () => Promise.all([api("/security/queue?state=open"), api("/security/queue?state=handled")])
    .then(([open, done]) => { setRows(open.items); setHandled(done.items); setHandledCount(open.counts?.handled ?? done.items.length); })
    .catch((e) => say(e.message, "err"));
  useEffect(() => { load(); }, []);
  const act = async (id: string, path: string) => { try { await post(`/cases/${id}${path}`); say("Done"); load(); } catch (e: any) { say(e.message, "err"); } };
  const isOperator = (r: any) => (r.anomalies || []).some((a: any) => String(a.signal || "").startsWith("OPERATOR_") || String(a.signal || "").startsWith("AUTO_DRAFT"));
  const shown = (filter === "HANDLED" ? handled || [] : rows || []).filter((r) => {
    if (!filter || filter === "HANDLED") return true;
    if (filter === "OPERATOR") return isOperator(r);
    return r.outcome === filter;
  });
  const counts: Record<string, number> = (rows || []).reduce((m: Record<string, number>, r) => ({ ...m, [r.outcome]: (m[r.outcome] || 0) + 1 }), {} as Record<string, number>);
  counts.OPERATOR = (rows || []).filter(isOperator).length;
  const pageSize = 10;
  const totalPages = Math.max(1, Math.ceil(shown.length / pageSize));
  const visibleRows = shown.slice((page - 1) * pageSize, page * pageSize);

  return (
    <div className="space-y-5">
      {toast && <Toast {...toast} />}
      <header className="space-y-4">
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-bold text-accent-fg transition hover:-translate-x-1 hover:text-accent">← <span>Back to inbox</span></Link>
        <div className="max-w-4xl">
          <h1 className="dashboard-number text-4xl font-bold tracking-[-.04em] text-[#583521] sm:text-5xl lg:text-6xl">Security agent</h1>
          <p className="mt-3 max-w-3xl text-base font-semibold leading-relaxed text-[#7d6251] sm:text-lg">Review security signals, suspicious activity, and spam before cases move through the workflow. Automated checks can flag a case, while your team remains in control of the final action.</p>
        </div>
        <div className="inline-flex max-w-full flex-wrap gap-1 rounded-2xl border border-orange-200 bg-[#fffaf5] p-1.5 shadow-sm">
          {["", "SECURITY_REVIEW", "SUSPICIOUS", "SPAM", "OPERATOR", "HANDLED"].map((k) => (
            <button key={k || "all"} type="button" onClick={() => { setFilter(k); setPage(1); }} className={`rounded-xl px-3 py-2 text-xs font-bold tracking-wide transition duration-200 hover:-translate-y-0.5 sm:text-sm ${filter === k ? "bg-accent text-white shadow-sm" : "text-[#76503a] hover:bg-orange-100 hover:text-[#a44d13]"}`}>
              {k === "HANDLED" ? `Handled (${handledCount})` : k === "OPERATOR" ? `Unusual operator signals (${counts.OPERATOR || 0})` : k ? `${k.replace(/_/g, " ")} (${counts[k] || 0})` : `Open (${rows?.length || 0})`}
            </button>
          ))}
        </div>
      </header>
      {rows === null ? <div className="space-y-2" aria-busy>{[0, 1, 2].map((i) => <div key={i} className="h-24 animate-pulse rounded-xl bg-ink-100" />)}</div>
        : shown.length === 0 ? <Empty text="Nothing flagged. The inbox is clean for this filter." />
        : <Card title={<span className="text-xl font-bold text-accent [font-family:Georgia,'Times_New_Roman',serif]">Security cases</span>} right={<span className="text-sm font-semibold text-[#8b654b]">{shown.length} cases</span>}>
            <div className="grid gap-3">
              {visibleRows.map((r) => (
                <article key={r.case_id} className="min-w-0 overflow-hidden rounded-xl border border-orange-200 bg-[#fffdf9] p-3 shadow-sm transition duration-200 hover:-translate-y-1 hover:border-orange-300 hover:shadow-md">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge className={TONE[r.outcome]}>{r.outcome.replace(/_/g, " ")}</Badge>
                        <Link href={`/cases/${r.case_id}`} className="font-mono text-xs text-accent hover:underline">{r.case_id}</Link>
                        <span className="min-w-0 break-words text-base font-bold text-[#503426]">{r.subject}</span>
                      </div>
                      <div className="mt-2 break-all text-xs text-ink-500">from <span className="font-mono">{r.sender}</span></div>
                    </div>
                    <span className="flex shrink-0 items-center gap-2 text-xs">{r.gate_state && r.gate_state !== "open" && <Badge className={GATE_TONE[r.gate_state] || "bg-ink-100 text-ink-700"}>{GATE_LABEL[r.gate_state] || r.gate_state}</Badge>}<StatusBadge status={r.status} /><span className="text-ink-500">score {r.score}</span></span>
                  </div>
                  <ul className="mt-3 grid gap-2 text-xs md:grid-cols-2">
                    {r.signals.map((s: any, i: number) => <li key={i} className="rounded-lg bg-[#fff7ee] p-2.5"><b>{s.signal}</b> <span className="text-ink-500">({s.severity})</span><div className="mt-1 text-ink-700">{s.evidence}</div><div className="text-ink-500">Recommended: {s.recommended_action}</div></li>)}
                    {r.anomalies.map((s: any, i: number) => <li key={`a${i}`} className="rounded-lg bg-review-bg/50 p-2.5"><b>{s.signal}</b> <span className="text-ink-500">({s.severity})</span><div className="mt-1 text-ink-700">{s.evidence}</div><div className="text-ink-500">Recommended: {s.recommended_action}</div></li>)}
                  </ul>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <Link href={`/cases/${r.case_id}`}><Button kind="primary">Open case</Button></Link>
                    {(!r.gate_state || r.gate_state === "open") && <>
                      <Button onClick={() => act(r.case_id, "/request-review")}>Send to human review</Button>
                      <Button onClick={() => act(r.case_id, "/no-action")}>Mark no action</Button>
                      <Button onClick={() => act(r.case_id, "/complete")} title="Remove from the queue. Three archived mails from one sender teach the desk to block it (see Policies)">Archive</Button>
                    </>}
                  </div>
                </article>
              ))}
            </div>
            {totalPages > 1 && <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-orange-100 pt-4">
              <span className="text-sm text-ink-500">Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, shown.length)} of {shown.length}</span>
              <div className="flex gap-2">
                <Button onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page === 1}>Previous</Button>
                <Button kind="primary" onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={page === totalPages}>Next</Button>
              </div>
            </div>}
          </Card>}
    </div>
  );
}
