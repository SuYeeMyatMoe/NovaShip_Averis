"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Empty, fmtDate } from "@/components/ui";
import { CaseColHandle, caseColStyle, useCaseColWidth } from "@/components/col-resize";

const CASE_COL_KEY = "novaship.audit.caseColWidth";

/** Global append-only audit log (Supervisor, Admin, Auditor). */
export default function AuditPage() {
  const pageSize = 10;
  const [events, setEvents] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [limit, setLimit] = useState(200);
  const [page, setPage] = useState(0);
  const [caseColWidth, setCaseColWidth] = useCaseColWidth(CASE_COL_KEY);
  useEffect(() => {
    setPage(0);
    setEvents(null);
    const qs = new URLSearchParams({ limit: String(limit) }); if (action) qs.set("action", action); if (actor) qs.set("actor_type", actor);
    api(`/audit?${qs}`).then((d) => { setEvents(d.events); setErr(null); }).catch((e) => setErr(e.message));
  }, [action, actor, limit]);
  const pageCount = events ? Math.max(1, Math.ceil(events.length / pageSize)) : 1;
  const pageEvents = events?.slice(page * pageSize, (page + 1) * pageSize) || [];
  const colStyle = caseColStyle(caseColWidth);
  const caseLabels = pageEvents.map((e) => (e.case_id ? String(e.case_id).replace("case_", "") : ""));

  return (
    <div className="space-y-5">
      <header>
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-bold text-accent-fg transition hover:-translate-x-1 hover:text-accent">← <span>Back to inbox</span></Link>
        <h1 className="dashboard-number mt-6 text-4xl font-bold tracking-[-.04em] text-[#583521] sm:text-5xl lg:text-6xl">Audit history</h1>
        <p className="mt-3 max-w-3xl text-base font-semibold leading-relaxed text-[#7d6251] sm:text-lg">Every classification, field result, draft, approval, share and error across all cases. Append-only: the database trigger rejects updates and deletes.</p>
      </header>
      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-orange-100 bg-[#fffaf5] p-3 text-xs shadow-sm">
        <input placeholder="Filter by action, e.g. MISMATCH, SHARE, POLICY" value={action} onChange={(e) => setAction(e.target.value.toUpperCase())} className="w-full rounded-md border border-orange-200 bg-white px-2 py-1.5 sm:w-72" />
        {["", "USER", "AI", "SYSTEM"].map((a) => <Button key={a || "all"} kind={actor === a ? "primary" : "ghost"} onClick={() => setActor(a)}>{a || "All actors"}</Button>)}
        <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} className="rounded-md border border-ink-200 px-2 py-1.5">{[100, 200, 500, 1000].map((n) => <option key={n} value={n}>last {n}</option>)}</select>
      </div>
      {err ? <div className="rounded-xl border border-mismatch bg-mismatch-bg p-4 text-sm text-mismatch-fg">{err}. Global audit requires the Supervisor, Admin or Auditor role. Switch user in the header.</div>
        : events === null ? <div className="space-y-2" aria-busy>{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="h-8 animate-pulse rounded bg-ink-100" />)}</div>
        : events.length === 0 ? <Empty text="No events match." />
        : (
          <>
          <div className="overflow-auto rounded-2xl border border-orange-100 bg-white shadow-sm transition duration-200 hover:-translate-y-1 hover:border-orange-300 hover:shadow-md">
            <table className="w-full min-w-[1000px] text-xs">
              <colgroup>
                <col />
                <col style={{ width: caseColWidth }} />
                <col />
                <col />
                <col />
                <col />
              </colgroup>
              <thead className="bg-ink-50 text-[11px] uppercase text-ink-500"><tr><th className="px-2 py-1.5 text-left">Time</th><th className="relative overflow-hidden px-2 text-left" style={colStyle}><span className="block truncate pr-1">Case</span><CaseColHandle storageKey={CASE_COL_KEY} width={caseColWidth} onChange={setCaseColWidth} labels={caseLabels} /></th><th className="px-2 text-left">Actor</th><th className="px-2 text-left">Action</th><th className="px-2 text-left">After</th><th className="px-2 text-left">Policy</th></tr></thead>
              <tbody>{pageEvents.map((e) => (
                <tr key={e.event_id} className="border-t border-ink-100 align-top transition hover:bg-[#fffaf5]">
                  <td className="whitespace-nowrap px-2 py-1.5 font-mono text-[10px] text-ink-500">{fmtDate(e.timestamp)}</td>
                  <td className="overflow-hidden px-2 py-1.5 font-mono text-[10px]" style={colStyle}>{e.case_id ? <Link href={`/cases/${e.case_id}?tab=audit`} title={e.case_id} className="block truncate text-accent hover:underline">{e.case_id.replace("case_", "")}</Link> : "-"}</td>
                  <td className="px-2 py-1.5"><Badge className={{ USER: "bg-accent-bg text-accent-fg", AI: "bg-accent-soft text-accent-fg", SYSTEM: "bg-ink-100 text-ink-700" }[e.actor_type as string]}>{e.actor_type}</Badge> <span className="text-[10px] text-ink-500">{e.actor_id}</span></td>
                  <td className="px-2 py-1.5 font-medium">{e.action}</td>
                  <td className="max-w-[480px] px-2 py-1.5 font-mono text-[10px] text-ink-700">{e.after ? JSON.stringify(e.after).slice(0, 240) : ""}</td>
                  <td className="px-2 py-1.5 text-[10px] text-ink-500">{e.policy_version}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-ink-500">
            <span>Showing {page * pageSize + 1}–{Math.min((page + 1) * pageSize, events.length)} of {events.length} events</span>
            <div className="flex items-center gap-2"><Button disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>Previous</Button><span>Page {page + 1} / {pageCount}</span><Button disabled={page >= pageCount - 1} onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}>Next</Button></div>
          </div>
          </>
        )}
    </div>
  );
}
