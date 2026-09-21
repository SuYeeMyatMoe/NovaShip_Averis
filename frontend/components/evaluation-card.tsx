"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, downloadFile } from "@/lib/api";
import { Badge, Button, Card } from "@/components/ui";

type DiffRow = { email_id: string; link: string; desk?: any; truth?: any; desk_reason?: string | null; truth_reason?: string | null; kind?: string; desk_fields?: string[]; truth_fields?: string[]; extra?: string[]; missing?: string[] };
type Evaluation = {
  generated_at: string;
  counts: { bundle_emails: number; reference_emails: number; answered: number; missing: number };
  missing: string[];
  scoreboard: { final_score: number; stage1: { accuracy: number; macro_f1: number; per: Record<string, { tp: number; fp: number; fn: number }> }; stage3: { defect_precision: number; defect_recall: number; defect_f1: number; field_f1: number; exact_match_rate: number; doc_total: number }; reliability: { escalation_recall: number; escalation_precision: number; escalation_f1: number; gold_review: number; pred_review: number }; end_to_end: { success: number; total: number; rate: number } };
  diffs: Record<"category" | "status" | "mismatch_flag" | "defect_fields" | "escalation", DiffRow[]>;
  server?: { url: string; scoreboard?: { final_score: number }; error?: string };
  reference?: "uploaded" | "server file";
  report_md?: string;
};
const COLLAPSE_KEY = "novaship.eval.collapsed";
const DIFF_TITLES: Record<string, string> = { category: "Classification errors", status: "Status / review-reason errors", mismatch_flag: "Mismatch flag errors", defect_fields: "Defect-field errors", escalation: "Escalation errors" };

/** Scores the desk's *current* results with the organiser's scoring.py against the reference — no re-run. */
export function EvaluationCard({ canExport, say }: { canExport: boolean; say: (m: string, k?: "ok" | "err") => void }) {
  const [ev, setEv] = useState<Evaluation | null>(null);
  const [busy, setBusy] = useState(false);
  const [server, setServer] = useState("");
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  useEffect(() => { try { setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1"); } catch { /* ignore */ } }, []);
  const toggleCollapsed = () => { setCollapsed((c) => { try { localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1"); } catch { /* ignore */ } return !c; }); };

  const run = async () => {
    setBusy(true);
    try {
      const file = fileRef.current?.files?.[0];
      if (file) {
        // deployed instances have no private ground_truth.json: send the judges' file with the request (scored in memory, never stored)
        const fd = new FormData(); fd.append("ground_truth", file); fd.append("server", server.trim());
        setEv(await api<Evaluation>("/evaluate", { method: "POST", body: fd }));
      } else {
        const q = server.trim() ? `?server=${encodeURIComponent(server.trim())}` : "";
        setEv(await api<Evaluation>(`/evaluate${q}`));
      }
      setUnavailable(null);
    } catch (e: any) {
      if (e?.status === 404) setUnavailable(e?.detail?.error || "reference not available on this server");
      else say(e?.detail?.error || e.message, "err");
    } finally { setBusy(false); }
  };
  const downloadReport = async () => {
    try {
      if (ev?.report_md) {
        const url = URL.createObjectURL(new Blob([ev.report_md], { type: "text/markdown" }));
        const a = document.createElement("a"); a.href = url; a.download = "novaship-evaluation.md"; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      } else await downloadFile("/evaluate/report.md", "novaship-evaluation.md");
      say("Report downloaded");
    } catch (e: any) { say(e.message, "err"); }
  };
  const pct = (x: number | undefined) => (x == null ? "-" : (x * 100).toFixed(1) + "%");
  const tile = (label: string, value: string, hint: string) => (
    <div className="rounded-xl border border-ink-200 bg-white px-3 py-2">
      <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-accent">{label}</div>
      <div className="dashboard-number text-2xl font-bold text-ink-900">{value}</div>
      <div className="text-[10px] text-ink-500">{hint}</div>
    </div>
  );

  return (
    <Card title="Self-evaluation" className="border-orange-200"
      right={<span className="flex items-center gap-2 text-[11px] text-ink-500">
        {ev && <span>{ev.generated_at.replace("T", " ").replace("Z", " UTC")}{ev.reference === "uploaded" ? " · uploaded reference" : ""}</span>}
        {ev && <button type="button" onClick={toggleCollapsed} className="rounded-md border border-ink-200 bg-white px-2 py-0.5 font-semibold text-ink-700 hover:border-accent hover:text-accent-fg" aria-expanded={!collapsed}>{collapsed ? "View more" : "View less"}</button>}
      </span>}>
      {!collapsed && <p className="text-xs text-ink-600">Scores the results the desk already holds for the 520 bundle emails with the organiser&apos;s <span className="font-mono">scoring.py</span> against <span className="font-mono">ground_truth.json</span>. Nothing is re-run; open any disagreement to check the source documents before changing a decision.</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button kind="primary" disabled={busy || !canExport} onClick={run}>{busy ? "Scoring…" : "Evaluate current results"}</Button>
        <label className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-ink-200 bg-white px-2 py-1 text-xs text-ink-700 hover:border-accent" title="Deployed servers do not hold the private reference: choose the judges' ground_truth.json here. It is scored in memory and never stored.">
          <input ref={fileRef} type="file" accept=".json,application/json" className="hidden" onChange={(e) => setFileName(e.target.files?.[0]?.name || null)} />
          {fileName ? `reference: ${fileName}` : "Upload ground_truth.json"}
        </label>
        {fileName && <button type="button" className="text-[11px] text-ink-500 hover:text-ink-800" onClick={() => { if (fileRef.current) fileRef.current.value = ""; setFileName(null); }}>× use server file</button>}
        <input value={server} onChange={(e) => setServer(e.target.value)} placeholder="organiser server (optional), e.g. http://localhost:8081" className="w-72 rounded-md border border-ink-200 px-2 py-1 text-xs" aria-label="Organiser server URL" />
        {ev && canExport && <Button kind="ghost" disabled={busy} onClick={downloadReport}>Download report (MD)</Button>}
        {ev && canExport && <Button kind="ghost" disabled={busy} onClick={() => downloadFile("/evaluate/submission.json", "submission.json").then(() => say("submission.json downloaded")).catch((e) => say(e.message, "err"))}>Download submission.json</Button>}
      </div>
      {!canExport && <p className="mt-2 text-xs text-review-fg">Needs the export_data permission (Supervisor / Admin).</p>}
      {unavailable && <p className="mt-2 rounded-lg bg-review-bg px-3 py-2 text-xs text-review-fg">{unavailable}. Upload the judges&apos; <span className="font-mono">ground_truth.json</span> above, or run <span className="font-mono">python backend/scripts/evaluate.py</span> on a machine that has the docker bundle.</p>}
      {ev && collapsed && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-ink-200 bg-white px-3 py-2 text-xs">
          <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-accent">Final score</span>
          <span className="dashboard-number text-xl font-bold text-ink-900">{(ev.scoreboard.final_score * 100).toFixed(2)}%</span>
          <span className="text-ink-600">classification {pct(ev.scoreboard.stage1.accuracy)} · mismatch recall {pct(ev.scoreboard.stage3.defect_recall)} · end-to-end {ev.scoreboard.end_to_end.success}/{ev.scoreboard.end_to_end.total} · escalation recall {pct(ev.scoreboard.reliability.escalation_recall)}</span>
          <span className="text-ink-500">{Object.values(ev.diffs).reduce((n, rows) => n + rows.length, 0)} disagreement(s)</span>
          <button type="button" onClick={toggleCollapsed} className="ml-auto font-semibold text-accent-fg hover:underline">View more</button>
        </div>
      )}
      {ev && !collapsed && (
        <div className="mt-3 space-y-3">
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {tile("Final score", (ev.scoreboard.final_score * 100).toFixed(2) + "%", `${ev.counts.answered}/${ev.counts.bundle_emails} emails answered${ev.counts.missing ? ` · ${ev.counts.missing} missing` : ""}`)}
            {tile("Classification", pct(ev.scoreboard.stage1.accuracy), `macro-F1 ${pct(ev.scoreboard.stage1.macro_f1)}`)}
            {tile("Mismatch detection", `${pct(ev.scoreboard.stage3.defect_recall)} recall`, `precision ${pct(ev.scoreboard.stage3.defect_precision)} · field-F1 ${pct(ev.scoreboard.stage3.field_f1)}`)}
            {tile("End to end", `${ev.scoreboard.end_to_end.success}/${ev.scoreboard.end_to_end.total}`, `mismatch cases fully right (${pct(ev.scoreboard.end_to_end.rate)})`)}
            {tile("Ask for help", `${pct(ev.scoreboard.reliability.escalation_recall)} recall`, `precision ${pct(ev.scoreboard.reliability.escalation_precision)} · ${ev.scoreboard.reliability.pred_review} escalated / ${ev.scoreboard.reliability.gold_review} expected`)}
            {ev.server && tile("Organiser server", ev.server.scoreboard ? (ev.server.scoreboard.final_score * 100).toFixed(2) + "%" : "error", ev.server.error || ev.server.url)}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-ink-50 text-[11px] uppercase tracking-wide text-ink-500"><tr><th className="px-2 py-1.5 text-left">Category</th><th className="px-2 py-1.5 text-left">TP</th><th className="px-2 py-1.5 text-left">FP</th><th className="px-2 py-1.5 text-left">FN</th><th className="px-2 py-1.5 text-left">Precision</th><th className="px-2 py-1.5 text-left">Recall</th></tr></thead>
              <tbody>
                {Object.entries(ev.scoreboard.stage1.per || {}).map(([cat, v]) => {
                  const p = v.tp + v.fp ? v.tp / (v.tp + v.fp) : 0; const r = v.tp + v.fn ? v.tp / (v.tp + v.fn) : 0;
                  return <tr key={cat} className="border-t border-ink-100"><td className="px-2 py-1 font-mono">{cat}</td><td className="px-2 py-1">{v.tp}</td><td className="px-2 py-1">{v.fp}</td><td className="px-2 py-1">{v.fn}</td><td className="px-2 py-1">{pct(p)}</td><td className="px-2 py-1">{pct(r)}</td></tr>;
                })}
              </tbody>
            </table>
          </div>
          {(Object.keys(DIFF_TITLES) as (keyof Evaluation["diffs"])[]).map((key) => {
            const rows = ev.diffs[key] || [];
            return (
              <details key={key} open={rows.length > 0} className="rounded-xl border border-ink-200">
                <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-ink-800">{DIFF_TITLES[key]} <Badge className={rows.length ? "bg-mismatch-bg text-mismatch-fg" : "bg-match-bg text-match-fg"}>{rows.length}</Badge></summary>
                {rows.length === 0 ? <p className="px-3 pb-2 text-[11px] text-ink-500">None — the desk agrees with the reference.</p> : (
                  <div className="overflow-x-auto"><table className="w-full text-xs">
                    <thead className="text-[11px] uppercase text-ink-500"><tr><th className="px-3 py-1 text-left">Email</th><th className="px-3 py-1 text-left">Desk</th><th className="px-3 py-1 text-left">Reference</th><th className="px-3 py-1 text-left">Note</th></tr></thead>
                    <tbody>{rows.map((r) => (
                      <tr key={r.email_id} className="border-t border-ink-100">
                        <td className="px-3 py-1 font-mono"><Link href={r.link} className="text-accent hover:underline">{r.email_id}</Link></td>
                        <td className="px-3 py-1">{key === "category" ? r.desk : key === "mismatch_flag" || key === "defect_fields" ? (r.desk_fields || []).join(", ") || "no defect" : `${r.desk}${r.desk_reason ? ` (${r.desk_reason})` : ""}`}</td>
                        <td className="px-3 py-1">{key === "category" ? r.truth : key === "mismatch_flag" || key === "defect_fields" ? (r.truth_fields || []).join(", ") || "no defect" : `${r.truth}${r.truth_reason ? ` (${r.truth_reason})` : ""}`}</td>
                        <td className="px-3 py-1 text-ink-500">{key === "defect_fields" ? `extra: ${(r.extra || []).join(", ") || "-"} · missing: ${(r.missing || []).join(", ") || "-"}` : r.kind || ""}</td>
                      </tr>
                    ))}</tbody>
                  </table></div>
                )}
              </details>
            );
          })}
          <p className="text-[11px] text-ink-500">The scoreboard is a development aid: it cannot judge whether the desk asked for review at the right time or gave enough context. If a desk decision differs from the reference but is reasonable, record the reason on the case rather than forcing the answer. To use the organiser server, start it from <span className="font-mono">sdoc-hackathon-docker</span> (map a free host port such as 8081:8000 if 8080 is taken) and paste its URL above.</p>
          <div className="text-right"><button type="button" onClick={toggleCollapsed} className="text-xs font-semibold text-accent-fg hover:underline">View less</button></div>
        </div>
      )}
    </Card>
  );
}
