"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, post } from "@/lib/api";
import { Badge, Button, Card, Empty, KV, Toast } from "@/components/ui";

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

/** LangGraph agent console: run the graph on a case, see the paused interrupt, resume with a human decision. */
export default function AgentPage() {
  const [graph, setGraph] = useState<{ mermaid: string; nodes: string[] } | null>(null);
  const [rag, setRag] = useState<any>(null);
  const [caseId, setCaseId] = useState("case_email_004");
  const [st, setSt] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const [note, setNote] = useState("");
  const say = (msg: string, kind: "ok" | "err" = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 3500); };
  useEffect(() => { api("/agent/graph").then(setGraph).catch(() => {}); api("/rag/info").then(setRag).catch(() => {}); }, []);
  const load = (id = caseId) => api(`/agent/state/${id}`).then(setSt).catch((e) => say(e.message, "err"));
  const run = async () => { setBusy(true); try { setSt(await post(`/agent/run/${caseId}`)); say("Graph ran"); } catch (e: any) { say(e.message, "err"); } finally { setBusy(false); } };
  const resume = async (action: string) => {
    setBusy(true);
    try { setSt(await post(`/agent/resume/${caseId}`, { action, draft_id: st?.interrupt?.draft_id, note })); say(`Resumed with ${action}`); }
    catch (e: any) { say(e.message, "err"); } finally { setBusy(false); }
  };
  const traceNodes = new Set((st?.trace || []).map((t: any) => t.node));
  const alias: Record<string, string> = { classify: "intent_classifier", detect_documents: "attachment_classifier", extract: "document_extractor", compare: "seven_field_comparator", summarize_and_draft: "summary_and_draft", notify: "notifier" };

  return (
    <div className="space-y-5">
      {toast && <Toast {...toast} />}
      <header>
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-bold text-accent-fg transition hover:-translate-x-1 hover:text-accent">← <span>Back to inbox</span></Link>
        <h1 className="dashboard-number mt-6 text-4xl font-bold tracking-[-.04em] text-[#583521] sm:text-5xl lg:text-6xl">AI agent</h1>
        <p className="mt-3 max-w-3xl text-base font-semibold leading-relaxed text-[#7d6251] sm:text-lg">The AI agent classifies, extracts, summarises and prepares drafts for each case. When a human decision is needed, the case pauses until someone reviews it.</p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <Card className="border-orange-200 transition duration-200 hover:-translate-y-1 hover:border-orange-300 hover:shadow-md" title={<span className="text-lg font-bold text-accent">Graph nodes</span>} right={rag && <span className="text-[11px] text-ink-500">RAG: {rag.embedding_provider} embeddings, {rag.vector_store} store, {rag.chunks} chunks</span>}>
          <ol className="space-y-1.5">
            {NODES.map(([n, desc], i) => {
              const hit = traceNodes.has(n) || traceNodes.has(alias[n] || "");
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
          <Card className="border-orange-200 transition duration-200 hover:-translate-y-1 hover:border-orange-300 hover:shadow-md" title={<span className="text-lg font-bold text-accent">Run on a case</span>}>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input value={caseId} onChange={(e) => setCaseId(e.target.value)} className="flex-1 rounded-md border border-ink-200 px-2 py-1.5 font-mono text-xs" aria-label="Case id" />
              <Button kind="primary" disabled={busy} onClick={run}>Run graph</Button>
              <Button disabled={busy} onClick={() => load()}>Refresh state</Button>
            </div>
            <p className="mt-2 text-xs text-ink-500">Try case_email_004 (two mismatches, pauses), case_email_001 (all match, no pause), case_email_015 (spam), case_email_512 (scanned PDF).</p>
          </Card>

          <Card className="border-orange-200 transition duration-200 hover:-translate-y-1 hover:border-orange-300 hover:shadow-md" title={<span className="text-lg font-bold text-accent">Graph state</span>}>
            {!st ? <Empty text="Run the graph or refresh the state for a case." /> : (
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
                      {st.interrupt.allowed_actions.filter((a: string) => a !== "notify_party" && a !== "reassign").map((a: string) => <Button key={a} kind={a === "approve" ? "success" : a === "reject" ? "danger" : "default"} disabled={busy} onClick={() => resume(a)}>{a.replace("_", " ")}</Button>)}
                      <Link href={`/cases/${caseId}?tab=collab`}><Button kind="ghost">notify party / reassign in case</Button></Link>
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
    </div>
  );
}
