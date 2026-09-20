"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, FIELD_LABELS, SEVEN_FIELDS } from "@/lib/api";
import { Badge, Button, Card, Confidence, Empty, RESULT_STYLES, StatusBadge, Toast } from "@/components/ui";

type FieldStat = { field: string; label: string; match: number; mismatch: number; review: number; examples: { case_id: string; si: string; bl: string }[] };

/** Seven-field verification overview: where each of the seven fields is compared, how often it fails, and every case per field. */
export default function VerificationPage() {
  const [stats, setStats] = useState<{ compared_cases: number; fields: FieldStat[] } | null>(null);
  const [field, setField] = useState<string>("container_count");
  const [result, setResult] = useState<string>("MISMATCH");
  const [rows, setRows] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const pageSize = 10;

  useEffect(() => { api("/dashboard/fields").then(setStats).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { setRows(null); setPage(0); api(`/dashboard/field/${field}${result ? `?result=${result}` : ""}`).then((d) => setRows(d.items)).catch((e) => setErr(e.message)); }, [field, result]);

  if (err) return <Toast msg={err} kind="err" />;
  const total = stats?.compared_cases || 0;
  const visibleRows = rows?.slice(page * pageSize, (page + 1) * pageSize) || [];
  const totalPages = Math.max(1, Math.ceil((rows?.length || 0) / pageSize));
  return (
    <div className="space-y-5 [font-family:Georgia,'Times_New_Roman',serif]">
      <header>
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-bold text-accent-fg transition hover:-translate-x-1 hover:text-accent">← <span>Back to inbox</span></Link>
        <div className="mt-6 flex flex-wrap items-end justify-between gap-5">
          <div>
            <h1 className="dashboard-number text-4xl font-bold tracking-[-.04em] text-[#583521] sm:text-5xl lg:text-6xl">Seven field verification</h1>
            <p className="mt-3 max-w-3xl text-base font-semibold leading-relaxed text-[#7d6251] sm:text-lg">The Shipping Instruction is the source of truth. Every case is checked across all seven fields independently, so you can see exactly what needs attention.</p>
          </div>
        <div className="rounded-2xl border border-orange-200 bg-accent-bg px-4 py-3 text-left sm:px-5 sm:text-right"><div className="text-3xl font-bold text-accent">{total}</div><div className="text-sm font-bold text-accent-fg">cases compared</div></div>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        {(stats?.fields || SEVEN_FIELDS.map((f) => ({ field: f, label: FIELD_LABELS[f], match: 0, mismatch: 0, review: 0, examples: [] }))).map((s, i) => {
          const sel = s.field === field;
          const pct = total ? Math.round((s.mismatch / total) * 100) : 0;
          return (
            <button key={s.field} onClick={() => setField(s.field)} aria-pressed={sel} className={`rounded-2xl border-2 bg-[#fffdf9] p-3.5 text-left shadow-sm transition duration-200 hover:-translate-y-1 hover:scale-[1.02] hover:shadow-glow active:scale-[0.98] ${sel ? "border-accent bg-accent-bg/30 ring-1 ring-accent" : "border-orange-200 hover:border-accent"}`}>
              <div className="text-xs font-semibold text-ink-500">Field {i + 1} of 7</div>
              <div className="mt-1 text-lg font-bold text-accent-fg">{s.label}</div>
              <div className="mt-3 flex items-center justify-between gap-2"><span className="text-3xl font-bold tabular-nums text-accent">{s.mismatch}</span><span className="rounded-full bg-accent px-2.5 py-1 text-xs font-bold text-white">{pct}% mismatch</span></div>
              <div className="mt-3 flex h-1.5 w-full overflow-hidden rounded border border-orange-100 bg-white" aria-hidden>
                <span className="bg-[#7b4c35]" style={{ width: `${total ? (s.match / total) * 100 : 0}%` }} /><span className="bg-white" style={{ width: `${total ? (s.mismatch / total) * 100 : 0}%` }} /><span className="bg-[#c79a76]" style={{ width: `${total ? (s.review / total) * 100 : 0}%` }} />
              </div>
              <div className="mt-2 text-xs font-semibold text-ink-500">{s.match} match, {s.review} review</div>
            </button>
          );
        })}
      </div>

      <div className="rounded-2xl transition duration-200 hover:-translate-y-1 hover:shadow-glow">
      <Card title={<span className="text-xl font-bold text-accent">{FIELD_LABELS[field]}: cases</span>} right={
        <div className="flex gap-1">
          {["MISMATCH", "MATCH", "MISSING_IN_SI", "MISSING_IN_BL", "LOW_CONFIDENCE_REVIEW", ""].map((r) => <Button key={r || "all"} kind={result === r ? "primary" : "ghost"} onClick={() => setResult(r)}>{r ? RESULT_STYLES[r]?.label || r : "All"}</Button>)}
        </div>}>
        {rows === null ? <SkeletonRows /> : rows.length === 0 ? <Empty text={`No cases with ${FIELD_LABELS[field]} = ${result || "any result"}.`} /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1080px] text-xs">
              <thead className="whitespace-nowrap text-[10px] uppercase tracking-[.04em] text-ink-500"><tr><th className="px-2 py-2 text-left">Case</th><th className="px-2 text-left">Subject</th><th className="px-2 text-left">SI value (truth)</th><th className="px-2 text-left">Draft BL value</th><th className="px-2 text-left">Labels resolved</th><th className="px-2 text-left">Result</th><th className="px-2 text-left">Confidence</th><th className="px-2 text-left">Status</th></tr></thead>
              <tbody>{visibleRows.map((r) => (
                <tr key={r.case_id} className={`border-t border-ink-100 ${RESULT_STYLES[r.result]?.row || ""}`}>
                  <td className="px-2 py-1.5 font-mono"><Link href={`/cases/${r.case_id}?tab=compare`} className="text-accent hover:underline">{r.case_id.replace("case_", "")}</Link></td>
                  <td className="max-w-[320px] truncate px-2 py-1.5" title={r.subject}>{r.subject}</td>
                  <td className="px-2 py-1.5 font-mono">{r.si ?? <span className="italic text-review-fg">blank</span>}</td>
                  <td className="px-2 py-1.5 font-mono">{r.bl ?? <span className="italic text-review-fg">blank</span>}</td>
                  <td className="px-2 py-1.5 text-ink-500">{r.si_label || "-"} / {r.bl_label || "-"}</td>
                  <td className="px-2 py-1.5"><Badge className={`border ${RESULT_STYLES[r.result]?.badge}`}>{RESULT_STYLES[r.result]?.label || r.result}</Badge></td>
                  <td className="px-2 py-1.5"><Confidence value={r.confidence} /></td>
                  <td className="px-2 py-1.5"><StatusBadge status={r.status} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="flex items-center justify-between border-t border-ink-100 px-4 py-3 text-sm text-ink-500"><span>Page {page + 1} of {totalPages}</span><div className="flex gap-2"><Button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button><Button disabled={page + 1 >= totalPages} onClick={() => setPage(page + 1)}>Next</Button></div></div>
      </div>

    </div>
  );
}

function SkeletonRows() {
  return <div className="space-y-2" aria-busy>{[0, 1, 2, 3, 4].map((i) => <div key={i} className="h-7 animate-pulse rounded bg-ink-100" />)}</div>;
}
