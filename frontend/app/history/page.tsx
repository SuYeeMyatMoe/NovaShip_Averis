"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api, downloadFile, getSession, type HistoryRow } from "@/lib/api";
import { Badge, Button, StatusBadge, Toast, fmtDate } from "@/components/ui";
import { CaseColHandle, caseColStyle, useCaseColWidth } from "@/components/col-resize";

const PAGE = 25;
const CASE_COL_KEY = "novaship.history.caseColWidth";
const RESULT_LABELS: Record<string, string> = { done: "Processed", paused: "Paused – waiting", error: "Failed", all: "All runs" };

/**
 * History = every case the AI agent has run or a person marked complete, newest first. Unlike Audit (an event log), each row is a
 * case you can open, and the whole list downloads as Excel/CSV.
 */
export default function HistoryPage() {
  return <Suspense fallback={<div className="h-40 animate-pulse rounded-2xl bg-ink-100" />}><HistoryInner /></Suspense>;
}

function HistoryInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const highlight = sp.get("case");
  const me = getSession()?.user;
  const canExport = me?.permissions.includes("export_data") ?? false;
  const [result, setResult] = useState("done");
  const [mine, setMine] = useState(false);
  const [q, setQ] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(0);
  const [rows, setRows] = useState<HistoryRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<{ done: number; paused: number; error: number } | null>(null);
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [caseColWidth, setCaseColWidth] = useCaseColWidth(CASE_COL_KEY);
  const say = (msg: string, kind: "ok" | "err" = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 4000); };
  const colStyle = caseColStyle(caseColWidth);
  const caseLabels = (rows || []).map((r) => r.case_id.replace("case_", ""));

  const query = useCallback(() => {
    const p = new URLSearchParams({ result, limit: String(PAGE), offset: String(page * PAGE) });
    if (mine) p.set("run_by", "me");
    if (q.trim()) p.set("q", q.trim());
    if (from) p.set("date_from", from);
    if (to) p.set("date_to", to);
    return p.toString();
  }, [result, mine, q, from, to, page]);

  const load = useCallback(() => {
    api<{ total: number; items: HistoryRow[]; counts: { done: number; paused: number; error: number } }>(`/history?${query()}`)
      .then((d) => { setRows(d.items); setTotal(d.total); setCounts(d.counts); })
      .catch((e) => { setRows([]); say(e.message, "err"); });
  }, [query]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(0); }, [result, mine, q, from, to]);

  const exportAs = async (kind: "xlsx" | "csv") => {
    setBusy(true);
    try {
      const p = new URLSearchParams(query()); p.delete("limit"); p.delete("offset");
      await downloadFile(`/export/history.${kind}?${p.toString()}`, `novaship-history.${kind}`);
      say(`History downloaded (${kind.toUpperCase()})`);
    } catch (e: any) { say(e.message, "err"); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-5">
      {toast && <Toast {...toast} />}
      <header>
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-bold text-accent-fg transition hover:-translate-x-1 hover:text-accent">← <span>Back to inbox</span></Link>
        <h1 className="dashboard-number mt-6 text-4xl font-bold tracking-[-.04em] text-[#583521] sm:text-5xl">History</h1>
        <p className="mt-3 max-w-3xl text-base font-semibold leading-relaxed text-[#7d6251]">Every case the AI agent has processed or a person marked complete, newest first. Open any row to see the case; download the whole history as Excel. The Inbox only shows cases that are still open.</p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        {(["done", "paused", "error"] as const).map((k) => (
          <button key={k} type="button" onClick={() => setResult(k)} className={`rounded-2xl border px-4 py-3 text-left transition hover:-translate-y-0.5 hover:shadow-md ${result === k ? "border-accent bg-accent-bg/40" : "border-ink-200 bg-white"}`}>
            <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-accent">{RESULT_LABELS[k]}</div>
            <div className="dashboard-number mt-1 text-3xl font-bold text-ink-900">{counts ? counts[k] : "…"}</div>
            <div className="text-[11px] text-ink-500">{k === "done" ? "agent finished; case left the Inbox" : k === "paused" ? "waiting for a human decision" : "run crashed; still in the Inbox"}</div>
          </button>
        ))}
      </div>

      <div className="overflow-hidden rounded-2xl border border-ink-200 bg-white shadow-card">
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-100 px-3 py-2">
          <select value={result} onChange={(e) => setResult(e.target.value)} className="rounded-md border border-ink-200 px-2 py-1.5 text-sm" aria-label="Result">
            {Object.entries(RESULT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <label className="flex items-center gap-1 text-xs text-ink-700"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Run by me</label>
          <input placeholder="Search case, subject, sender, operator" value={q} onChange={(e) => setQ(e.target.value)} className="w-full rounded-md border border-ink-200 px-2 py-1.5 text-sm sm:w-64" aria-label="Search" />
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-md border border-ink-200 px-2 py-1 text-sm" aria-label="Run from" title="Run from" />
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-md border border-ink-200 px-2 py-1 text-sm" aria-label="Run to" title="Run to" />
          <span className="ml-auto flex items-center gap-2 text-xs text-ink-500">
            {total} run(s)
            {canExport && <Button kind="primary" disabled={busy} onClick={() => exportAs("xlsx")}>Download history (Excel)</Button>}
            {canExport && <Button kind="ghost" disabled={busy} onClick={() => exportAs("csv")}>CSV</Button>}
          </span>
        </div>
        <div className="overflow-x-auto scrollbar-thin">
          <table className="w-full min-w-[1200px] text-left text-xs">
            <colgroup>
              <col />
              <col style={{ width: caseColWidth }} />
              <col /><col /><col /><col /><col /><col /><col /><col /><col /><col />
            </colgroup>
            <thead className="bg-ink-50 text-[11px] uppercase tracking-wide text-ink-500">
              <tr>
                <th className="px-2 py-2">Run time</th>
                <th className="relative overflow-hidden px-2 py-2" style={colStyle}><span className="block truncate pr-1">Case</span><CaseColHandle storageKey={CASE_COL_KEY} width={caseColWidth} onChange={setCaseColWidth} labels={caseLabels} /></th>
                <th className="px-2 py-2">Subject / sender</th><th className="px-2 py-2">Mailbox</th>
                <th className="px-2 py-2">Ran by</th><th className="px-2 py-2">Result</th><th className="px-2 py-2">Status after</th><th className="px-2 py-2">Mismatch</th>
                <th className="px-2 py-2">Decision</th><th className="px-2 py-2">Duration</th><th className="px-2 py-2">Runs</th><th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows === null && Array.from({ length: 5 }, (_, i) => <tr key={i} className="border-t border-ink-100"><td colSpan={12} className="px-2 py-2"><div className="h-6 animate-pulse rounded bg-ink-100" /></td></tr>)}
              {rows?.map((r) => (
                <tr key={r.case_id} onClick={(e) => { if ((e.target as HTMLElement).closest("button, a")) return; router.push(`/cases/${r.case_id}`); }} title="Open the case"
                    className={`cursor-pointer border-t border-ink-100 align-top transition hover:bg-accent-bg/30 ${highlight === r.case_id ? "bg-accent-bg/50" : ""}`}>
                  <td className="whitespace-nowrap px-2 py-2 text-ink-600">{fmtDate(r.run_at)}</td>
                  <td className="overflow-hidden px-2 py-2 font-mono text-[11px]" style={colStyle}><Link href={`/cases/${r.case_id}`} title={r.case_id} className="block truncate text-accent hover:underline">{r.case_id.replace("case_", "")}</Link></td>
                  <td className="max-w-[380px] px-2 py-2"><div className="line-clamp-1 font-medium text-ink-900">{r.subject || "(no subject)"}</div><div className="truncate text-[11px] text-ink-500">{r.sender}</div></td>
                  <td className="px-2 py-2 text-[11px] text-ink-600">{r.mailbox || <span className="text-ink-400">shared / webhook</span>}</td>
                  <td className="px-2 py-2 text-[11px]">{r.run_by_name}{r.mode === "batch" && <span className="ml-1 rounded-full bg-ink-100 px-1.5 text-[10px] text-ink-600">batch</span>}{r.mode === "human" && <span className="ml-1 rounded-full bg-accent-bg px-1.5 text-[10px] text-accent-fg" title="Marked complete by a person">by hand</span>}</td>
                  <td className="px-2 py-2">{r.result === "done" ? <Badge className="bg-match-bg text-match-fg">processed</Badge> : r.result === "paused" ? <Badge className="bg-review-bg text-review-fg">paused</Badge> : <span title={r.error}><Badge className="bg-mismatch text-white">failed</Badge></span>}</td>
                  <td className="px-2 py-2"><StatusBadge status={r.status_after} />{r.status !== r.status_after && <div className="mt-0.5 text-[10px] text-ink-500">now {r.status.replace(/_/g, " ").toLowerCase()}</div>}</td>
                  <td className="px-2 py-2">{r.comparison_status ? (r.mismatch_count > 0 ? <Badge className="bg-mismatch text-white">{r.mismatch_count} mismatch</Badge> : <Badge className="bg-match-bg text-match-fg">no mismatch</Badge>) : <span className="text-ink-400">-</span>}</td>
                  <td className="px-2 py-2 text-[11px]">{r.decision ? r.decision.replace(/_/g, " ") : <span className="text-ink-400">-</span>}</td>
                  <td className="px-2 py-2 font-mono text-[11px] text-ink-500">{r.mode === "human" ? "—" : `${r.ms} ms`}</td>
                  <td className="px-2 py-2 text-[11px] text-ink-500">{r.runs}</td>
                  <td className="px-2 py-2"><Link href={`/cases/${r.case_id}?tab=agent`}><Button kind="primary">Open</Button></Link></td>
                </tr>
              ))}
              {rows?.length === 0 && <tr><td colSpan={12} className="px-4 py-10 text-center text-sm text-ink-500">No agent runs match. Run the agent from the Inbox (<em>Run agent</em>) or the Workbench batch card.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-ink-100 px-3 py-2 text-xs text-ink-500">
          <span>Page {page + 1} of {Math.max(1, Math.ceil(total / PAGE))}</span>
          <div className="flex gap-1"><Button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button><Button disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</Button></div>
        </div>
      </div>
    </div>
  );
}
