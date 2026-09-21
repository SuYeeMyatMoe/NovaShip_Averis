"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, post, getSession, downloadFile, type CaseRow } from "@/lib/api";
import { Badge, Button, Card, Empty, KV, StatusBadge, Toast } from "@/components/ui";
import { CaseMultiPicker } from "@/components/case-picker";
import { useOperatorWarning } from "@/lib/operator-warning";

function downloadBase64Xlsx(b64: string, filename: string) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

type RunRow = { action: string; ok: boolean; status?: string; paused?: boolean; next?: string[]; interrupt?: any; ms?: number; error?: any; agent_run?: { result: string; last_run_at: string } | null };
const LAST_RUN_KEY = "novaship.workbench.lastRun";
type LlmPosture = { provider: string; model: string | null; privacy: "mask" | "off"; audit_provider_calls: boolean; vision_ocr: boolean; embeddings: string; calls: { total: number; masked: number; errors: number } };

const RESUME_ACTIONS = ["retry", "request_review", "reject", "mark_no_action", "complete"];

const NODES = [
  ["security_precheck", "Deterministic spam, sender, link, attachment-type, duplicate and bypass checks."],
  ["security_agent", "LLM reasons over the signals and may escalate (never downgrade) to SECURITY_REVIEW."],
  ["classify", "Intent + priority + action needed. Rules on the real subject grammar; LLM tie-break under 0.75."],
  ["detect_documents", "SI / Draft BL / invoice / supporting / unknown; routes to waiting-documents or unreadable."],
  ["extract", "Seven-field extraction with label-synonym resolution and evidence (page, line, snippet)."],
  ["compare", "The final check that decides whether fields match or differ."],
  ["summarize_and_draft", "Policy evaluation, recommendation, summary, draft (never sent), RAG context."],
  ["human_review", "interrupt(): the graph pauses here until a person approves, edits, rejects, reassigns, notifies or retries."],
  ["notify", "Executes only what the human approved, then writes GRAPH_COMPLETED to the audit log."],
] as const;

const NODE_ALIAS: Record<string, string> = { classify: "intent_classifier", detect_documents: "attachment_classifier", extract: "document_extractor", compare: "seven_field_comparator", summarize_and_draft: "summary_and_draft", notify: "notifier" };

/** Operator run console: agent pause/resume, batch (never sends email), CSV/XLSX export, RAG + privacy posture. */
export default function WorkbenchPage() {
  const [rag, setRag] = useState<any>(null);
  const [caseId, setCaseId] = useState("case_email_004");
  const [ids, setIds] = useState<string[]>(["case_email_004"]);
  const [st, setSt] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const [note, setNote] = useState("");
  const [parallel, setParallel] = useState(4);
  const [rows, setRows] = useState<Record<string, RunRow>>({});
  const [lastAction, setLastAction] = useState<string | null>(null);
  const [pending, setPending] = useState<{ pending: number; paused: number } | null>(null);
  // the last results table survives a trip into a case and back
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(LAST_RUN_KEY);
      if (raw) { const saved = JSON.parse(raw); if (saved?.rows) { setRows(saved.rows); setLastAction(saved.lastAction || null); } }
    } catch { /* ignore */ }
  }, []);
  useEffect(() => {
    try { Object.keys(rows).length ? sessionStorage.setItem(LAST_RUN_KEY, JSON.stringify({ rows, lastAction })) : sessionStorage.removeItem(LAST_RUN_KEY); } catch { /* ignore */ }
  }, [rows, lastAction]);
  const loadPending = useCallback(() => {
    Promise.all([api<{ total: number }>("/cases?agent=pending&limit=1"), api<{ total: number }>("/cases?agent=paused&limit=1")])
      .then(([p, q]) => setPending({ pending: p.total, paused: q.total })).catch(() => {});
  }, []);
  useEffect(() => { loadPending(); }, [loadPending]);
  const addPending = async (which: "pending" | "paused", n: number) => {
    try {
      const r = await api<{ items: CaseRow[] }>(`/cases?agent=${which}&sort=received_desc&limit=${n}`);
      const fresh = r.items.map((c) => c.id).filter((id) => !ids.includes(id));
      setIds([...ids, ...fresh]);
      say(fresh.length ? `Added ${fresh.length} ${which === "pending" ? "not-run" : "paused"} case(s)` : `Nothing new to add`);
    } catch (e: any) { say(e.message, "err"); }
  };
  const { notice, dialog } = useOperatorWarning();
  const me = getSession()?.user;
  const canBatch = me?.permissions.includes("batch") ?? false;
  const canExport = me?.permissions.includes("export_data") ?? false;
  const canCompare = me?.permissions.includes("compare") ?? false;
  const say = (msg: string, kind: "ok" | "err" = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 4000); };

  const loadRag = useCallback(() => { api("/rag/info").then(setRag).catch(() => {}); }, []);
  useEffect(() => { loadRag(); }, [loadRag]);

  const inspect = async (id: string) => {
    setCaseId(id);
    try { setSt(await api(`/agent/state/${id}`)); document.getElementById("graph-state")?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
    catch (e: any) { say(e.message, "err"); }
  };
  const resume = async (id: string, action: string) => {
    setBusy(true);
    try {
      const state = await post(`/agent/resume/${id}`, { action, draft_id: rows[id]?.interrupt?.draft_id ?? st?.interrupt?.draft_id, note });
      if (id === caseId) setSt(state);
      setRows((r) => (r[id] ? { ...r, [id]: { ...r[id], paused: !!state.paused, status: state.status, next: state.next, interrupt: state.interrupt } } : r));
      say(`${id.replace("case_", "")}: resumed with ${action}`);
    } catch (e: any) { say(e.message, "err"); }
    finally { setBusy(false); }
  };

  const failedIds = Object.entries(rows).filter(([, r]) => !r.ok).map(([id]) => id);
  const pausedIds = Object.entries(rows).filter(([, r]) => r.ok && r.paused).map(([id]) => id);

  const batch = async (action: string, params: Record<string, any> = {}, confirm = false, only?: string[]) => {
    const targets = only ?? ids;
    if (!targets.length) return say("Add at least one case", "err");
    setBusy(true);
    try {
      const r = await post("/cases/batch", { action, case_ids: targets, params: { ...params, parallel }, confirm });
      if (r.requires_confirmation) {
        if (window.confirm(`${r.note}\n\nProceed with '${action}' on ${r.count} cases?`)) return batch(action, params, true, only);
        return;
      }
      if (r.csv) {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([r.csv], { type: "text/csv" }));
        a.download = "cases.csv";
        a.click();
      }
      if (r.xlsx_base64) downloadBase64Xlsx(r.xlsx_base64, r.filename || "cases.xlsx");
      const results = (r.results || {}) as Record<string, any>;
      setLastAction(action);
      setRows((prev) => ({ ...(only ? prev : {}), ...Object.fromEntries(Object.entries(results).map(([id, x]) => [id, { action, ...x }])) }));
      const ok = Object.values(results).filter((x: any) => x.ok).length;
      say(`${action}: ${ok}/${Object.keys(results).length} succeeded${r.parallel > 1 ? ` · ${r.parallel} in parallel` : ""}`, ok === Object.keys(results).length ? "ok" : "err");
      notice(r);
    } catch (e: any) { say(e.message, "err"); notice(e); }
    finally { setBusy(false); }
  };

  const runAgentBatch = async (only?: string[]) => {
    const targets = only ?? ids;
    if (!targets.length) return say("Add at least one case", "err");
    setBusy(true);
    try {
      const r = await post("/agent/run-batch", { case_ids: targets, parallel });
      const results = (r.results || {}) as Record<string, any>;
      setLastAction("agent");
      setRows((prev) => ({ ...(only ? prev : {}), ...Object.fromEntries(Object.entries(results).map(([id, x]) => [id, { action: "agent", ...x }])) }));
      say(`agent: ${Object.keys(results).length - r.failed_ids.length}/${Object.keys(results).length} ran · ${r.paused_ids.length} waiting for a person · ${r.parallel} in parallel`, r.failed_ids.length ? "err" : "ok");
      loadPending();
    } catch (e: any) { say(e.message, "err"); }
    finally { setBusy(false); }
  };
  const resumeBatch = async (action: string) => {
    if (!pausedIds.length) return say("No paused cases in the last run", "err");
    if (!window.confirm(`Resume ${pausedIds.length} paused case(s) with '${action.replace("_", " ")}'? Nothing is sent.`)) return;
    setBusy(true);
    try {
      const r = await post("/agent/resume-batch", { action, case_ids: pausedIds, note });
      const results = (r.results || {}) as Record<string, any>;
      setRows((prev) => ({ ...prev, ...Object.fromEntries(Object.entries(results).map(([id, x]) => [id, { ...(prev[id] || { action: "agent" }), ok: x.ok, paused: x.ok ? !!x.paused : prev[id]?.paused, status: x.status ?? prev[id]?.status, error: x.ok ? undefined : x.error }])) }));
      say(`resume ${action}: ${Object.values(results).filter((x: any) => x.ok).length}/${pausedIds.length} succeeded`);
      loadPending();
    } catch (e: any) { say(e.message, "err"); }
    finally { setBusy(false); }
  };
  const retryFailed = () => {
    if (!failedIds.length) return;
    if (lastAction === "agent") return runAgentBatch(failedIds);
    if (lastAction) return batch(lastAction, { retry_failed: failedIds }, true, failedIds);
  };
  const overallReport = async () => {
    setBusy(true);
    try { await downloadFile("/export/report.xlsx", "novaship-report.xlsx"); say("Overall report downloaded"); }
    catch (e: any) { say(e.message, "err"); }
    finally { setBusy(false); }
  };

  const llm: LlmPosture | undefined = rag?.llm;
  const rowList = Object.entries(rows);
  const traceNodes = new Set((st?.trace || []).map((t: any) => t.node));

  return (
    <div className="space-y-5">
      {toast && <Toast {...toast} />}
      {dialog}
      <header>
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-bold text-accent-fg transition hover:-translate-x-1 hover:text-accent">← <span>Back to inbox</span></Link>
        <h1 className="dashboard-number mt-6 text-4xl font-bold tracking-[-.04em] text-[#583521] sm:text-5xl">Workbench</h1>
        <p className="mt-3 max-w-3xl text-base font-semibold leading-relaxed text-[#7d6251]">Batch-run the AI agent on selected cases in parallel, review each pause, and export CSV, Excel or the overall report. External email is never sent from this page.</p>
      </header>

      <Card title="AI providers and privacy" className="border-orange-200">
        {rag ? (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2 text-sm">
              <Badge className="bg-accent-bg text-accent-fg">embeddings {rag.embedding_provider}</Badge>
              <Badge className="bg-ink-100 text-ink-700">{rag.dims} dims</Badge>
              <Badge className="bg-ink-100 text-ink-700">store {rag.vector_store}</Badge>
              <span className="text-ink-500">{rag.chunks} chunks indexed</span>
            </div>
            {llm && (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge className="bg-ink-100 text-ink-700">model {llm.provider === "none" ? "none (rules only)" : `${llm.provider} · ${llm.model}`}</Badge>
                <Badge className={llm.privacy === "mask" ? "bg-match-bg text-match-fg" : "bg-review-bg text-review-fg"}>{llm.privacy === "mask" ? "identifiers masked before any prompt" : "prompts unmasked (LLM_PRIVACY=off)"}</Badge>
                <Badge className={llm.vision_ocr ? "bg-ink-100 text-ink-700" : "bg-ink-100 text-ink-500"}>vision OCR {llm.vision_ocr ? "on (scans go to Gemini)" : "off"}</Badge>
                <span className="text-xs text-ink-500">{llm.calls.total} provider call(s) this process · {llm.calls.masked} masked · {llm.calls.errors} failed · each audited as metadata only</span>
              </div>
            )}
          </div>
        ) : <Empty text="RAG info unavailable." />}
      </Card>

      <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <Card title="Graph nodes" className="border-orange-200" right={rag && <span className="text-[11px] text-ink-500">RAG: {rag.embedding_provider} embeddings, {rag.vector_store} store, {rag.chunks} chunks</span>}>
          <ol className="space-y-1.5">
            {NODES.map(([n, desc], i) => {
              const hit = traceNodes.has(n) || traceNodes.has(NODE_ALIAS[n] || "");
              const paused = st?.paused && st?.next?.includes(n);
              return (
                <li key={n} className={`flex gap-3 rounded-lg border border-orange-200 p-2 text-xs transition duration-200 hover:-translate-y-0.5 hover:border-orange-400 hover:shadow-sm ${paused ? "bg-review-bg/50" : hit ? "bg-match-bg/30" : "bg-[#fffdf9]"}`}>
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent-bg text-[11px] font-bold text-accent-fg ring-1 ring-accent-ring/60">{i + 1}</span>
                  <div><div className="font-mono font-semibold text-ink-900">{n}{n === "compare" && <Badge className="ml-2 bg-ink-900 text-white">deterministic</Badge>}{n === "human_review" && <Badge className="ml-2 bg-review text-white">interrupt</Badge>}</div><div className="text-ink-600">{desc}</div></div>
                </li>
              );
            })}
          </ol>
        </Card>

        <div className="space-y-4">
        <Card title="Batch run (Supervisor / Admin)" className="border-orange-200">
            <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-dashed border-orange-200 bg-[#fffaf5] px-3 py-2 text-xs text-ink-700">
              <span className="font-semibold">Before run:</span>
              {pending ? <span><b>{pending.pending}</b> case(s) not run by the agent yet · <b>{pending.paused}</b> paused for a decision</span> : <span className="text-ink-400">counting…</span>}
              <span className="ml-auto flex gap-1">
                <Button kind="ghost" disabled={busy || !pending?.pending} onClick={() => addPending("pending", 20)}>Add 20 newest not-run</Button>
                <Button kind="ghost" disabled={busy || !pending?.paused} onClick={() => addPending("paused", 50)}>Add all paused</Button>
                <Link href="/history"><Button kind="ghost">Processed ↗</Button></Link>
              </span>
            </div>
            <label className="text-xs font-semibold text-ink-600">Cases</label>
            <CaseMultiPicker ids={ids} onChange={setIds} className="mt-1" />
            <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-ink-500">
              <span>{ids.length} selected · External sending is never batched.</span>
              <label className="inline-flex items-center gap-1">parallel
                <select value={parallel} onChange={(e) => setParallel(Number(e.target.value))} className="rounded border border-ink-200 px-1 py-0.5 text-[11px]">
                  {[1, 2, 4, 8].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              {ids.length > 0 && <button type="button" className="text-accent hover:underline" onClick={() => setIds([])}>clear</button>}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button kind="primary" disabled={busy || !canCompare} onClick={() => runAgentBatch()}>Run agent on selected</Button>
              <Button disabled={busy || !canBatch} onClick={() => batch("classify")}>Classify</Button>
              <Button disabled={busy || !canBatch} onClick={() => batch("compare")}>Compare</Button>
              <Button disabled={busy || !canBatch} onClick={() => batch("draft")}>Prepare drafts</Button>
              <Button disabled={busy || !canBatch} onClick={() => batch("request_review")}>Request review</Button>
              <Button disabled={busy || !canExport} onClick={() => batch("export")}>Export CSV</Button>
              <Button disabled={busy || !canExport} onClick={() => batch("export_xlsx")}>Export Excel</Button>
              <Button disabled={busy || !canExport} onClick={() => batch("report_xlsx")}>Report (selected)</Button>
              <Button kind="ghost" disabled={busy || !canExport} onClick={overallReport}>Overall report (Excel)</Button>
            </div>
            {!canBatch && <p className="mt-2 text-xs text-review-fg">Batch actions need Supervisor or Admin. Export needs export_data.</p>}
          </Card>

          <div id="graph-state" className="scroll-mt-16" />
          <Card title="Graph state" className="border-orange-200">
            {!st ? <Empty text="Run the agent on selected cases, then press Inspect on a row to see its graph state here." /> : (
              <div className="space-y-2 text-sm">
                <KV k="Paused" v={st.paused ? <Badge className="bg-review text-white">waiting for human</Badge> : <Badge className="bg-match-bg text-match-fg">not paused</Badge>} />
                <KV k="Next node" v={<span className="font-mono text-xs">{st.next?.join(", ") || "END"}</span>} />
                <KV k="Case status" v={st.status || "-"} /><KV k="Route" v={st.route || "-"} /><KV k="Notified" v={String(st.notified)} />
                {st.security_agent && <KV k="Security agent" v={<span className="text-xs">{st.security_agent.outcome} ({st.security_agent.decided_by}, {st.security_agent.confidence}): {st.security_agent.reasoning}</span>} />}
                {st.rag_context?.length > 0 && <KV k="RAG context" v={<span className="font-mono text-[10px]">{st.rag_context.map((r: any) => r.id).join(", ")}</span>} />}
                {st.interrupt && (
                  <div className="rounded-lg border border-review bg-review-bg/40 p-3 text-xs">
                    <div className="mb-1 font-semibold text-review-fg">Human decision required</div>
                    <div>{st.interrupt.summary}</div>
                    {st.interrupt.mismatch_fields?.length > 0 && <div className="mt-1">Mismatch: {st.interrupt.mismatch_fields.join(", ")}</div>}
                    {st.interrupt.review_reason && <div className="mt-1">Review reason: {st.interrupt.review_reason}</div>}
                    {st.interrupt.draft_subject && <div className="mt-1 text-ink-500">Draft: {st.interrupt.draft_subject}</div>}
                    <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for the audit log" className="mt-2 w-full rounded-md border border-ink-200 px-2 py-1" />
                    <div className="mt-2 flex flex-wrap gap-1">
                      {(st.interrupt.allowed_actions || []).filter((a: string) => a !== "notify_party" && a !== "reassign").map((a: string) => (
                        <Button key={a} kind={a === "approve" ? "success" : a === "reject" ? "danger" : "default"} disabled={busy} onClick={() => resume(caseId, a)}>{a.replace("_", " ")}</Button>
                      ))}
                      <Link href={`/cases/${caseId}?tab=collab`}><Button kind="ghost">notify / reassign in case</Button></Link>
                    </div>
                  </div>
                )}
                <details className="text-xs"><summary className="cursor-pointer text-accent">Trace ({st.trace?.length || 0} nodes)</summary>
                  <ol className="mt-1 space-y-1">{(st.trace || []).map((t: any, i: number) => <li key={i} className="rounded bg-ink-50 p-1.5"><b>{t.node}</b> <Badge className="bg-ink-100 text-ink-700">{t.actor_type}</Badge> <span className="font-mono text-[10px] text-ink-600">{JSON.stringify(t.output).slice(0, 220)}</span></li>)}</ol>
                </details>
                <Link href={`/cases/${caseId}`} className="text-xs text-accent hover:underline">Open case {caseId}</Link>
              </div>
            )}
          </Card>
        </div>
      </div>

      {rowList.length > 0 && (
        <Card title={`Last run · ${lastAction === "agent" ? "agent" : lastAction} · ${rowList.length} case(s)`} className="border-orange-200"
          right={<div className="flex flex-wrap gap-1">
            {failedIds.length > 0 && <Button kind="danger" disabled={busy} onClick={retryFailed}>Retry {failedIds.length} failed</Button>}
            {pausedIds.length > 0 && RESUME_ACTIONS.filter((a) => a !== "reject").map((a) => <Button key={a} disabled={busy} onClick={() => resumeBatch(a)}>{a.replace("_", " ")} all paused ({pausedIds.length})</Button>)}
          </div>}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-ink-50 text-[11px] uppercase tracking-wide text-ink-500">
                <tr><th className="px-2 py-2 text-left">Case</th><th className="px-2 py-2 text-left">Result</th><th className="px-2 py-2 text-left">Status</th><th className="px-2 py-2 text-left">Time</th><th className="px-2 py-2 text-left">Detail</th><th className="px-2 py-2 text-left">Actions</th></tr>
              </thead>
              <tbody>
                {rowList.map(([id, r]) => (
                  <tr key={id} className={`cursor-pointer border-t border-ink-100 hover:bg-accent-bg/30 ${!r.ok ? "bg-mismatch-bg/40" : r.paused ? "bg-review-bg/40" : ""}`}
                      onClick={(e) => { if ((e.target as HTMLElement).closest("button, a")) return; window.location.assign(`/cases/${id}`); }} title="Open the case">
                    <td className="px-2 py-1.5 font-mono"><Link href={`/cases/${id}`} className="text-accent hover:underline">{id.replace("case_", "")}</Link></td>
                    <td className="px-2 py-1.5">{!r.ok ? <Badge className="bg-mismatch text-white">error</Badge> : r.paused ? <Badge className="bg-review-bg text-review-fg">needs a person</Badge> : <Badge className="bg-match-bg text-match-fg">ok</Badge>}</td>
                    <td className="px-2 py-1.5">{r.status ? <StatusBadge status={r.status} /> : <span className="text-ink-400">-</span>}</td>
                    <td className="px-2 py-1.5 font-mono text-ink-500">{r.ms != null ? `${r.ms} ms` : "-"}</td>
                    <td className="max-w-[420px] px-2 py-1.5 text-ink-700">
                      {!r.ok && r.error ? <span className="text-mismatch-fg">{r.error.error || r.error.message || JSON.stringify(r.error)}{r.error.category ? ` · ${r.error.category}` : ""}{r.error.retryable ? " · retryable" : ""}</span>
                        : r.paused && r.interrupt ? <span className="line-clamp-2">{r.interrupt.summary}</span>
                        : r.action === "agent" && r.ok ? <span className="text-ink-600">Agent finished{r.status ? ` · ${r.status.replace(/_/g, " ").toLowerCase()}` : ""} · <Link href={`/history?case=${id}`} className="text-accent-fg hover:underline">in Processed ↗</Link></span>
                        : r.next?.length ? <span className="font-mono text-ink-500">next: {r.next.join(", ")}</span> : <span className="text-ink-400">-</span>}
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="flex flex-wrap gap-1">
                        <Link href={`/cases/${id}`}><Button kind="primary">Open</Button></Link>
                        <Button kind="ghost" disabled={busy} title="Show this case's graph state above" onClick={() => inspect(id)}>Inspect</Button>
                        {r.paused && r.interrupt && (r.interrupt.allowed_actions || RESUME_ACTIONS).filter((a: string) => a !== "notify_party" && a !== "reassign").map((a: string) => (
                          <Button key={a} kind={a === "approve" ? "success" : a === "reject" ? "danger" : "ghost"} disabled={busy} onClick={() => resume(id, a)}>{a.replace("_", " ")}</Button>
                        ))}
                        {!r.ok && <Button kind="ghost" disabled={busy} onClick={() => (lastAction === "agent" ? runAgentBatch([id]) : lastAction ? batch(lastAction, { retry_failed: [id] }, true, [id]) : undefined)}>retry</Button>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-ink-500">Every row is isolated: one failing case never stops the others. Bulk resume allows only non-sending decisions; approve stays per case.</p>
        </Card>
      )}
    </div>
  );
}
