"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, post, type CaseView } from "@/lib/api";
import { Badge, Button, Empty, KV } from "@/components/ui";

/** Per-case LangGraph view: run the graph, see where it paused, resume with a human decision. */
export function AgentPanel({ c, onChange, say }: { c: CaseView; onChange: () => void; say: (m: string, k?: "ok" | "err") => void }) {
  const [st, setSt] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const load = () => api(`/agent/state/${c.id}`).then(setSt).catch(() => setSt(null));
  useEffect(() => { load(); }, [c.id]);
  const run = async () => { setBusy(true); try { setSt(await post(`/agent/run/${c.id}`)); say("Graph ran"); onChange(); } catch (e: any) { say(e.message, "err"); } finally { setBusy(false); } };
  const resume = async (action: string) => { setBusy(true); try { setSt(await post(`/agent/resume/${c.id}`, { action, draft_id: st?.interrupt?.draft_id, note })); say(`Resumed: ${action}`); onChange(); } catch (e: any) { say(e.message, "err"); } finally { setBusy(false); } };
  return (
    <div className="grid min-w-0 max-w-full gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="min-w-0 rounded-xl border border-ink-200 bg-white p-4">
        <div className="mb-2 flex min-w-0 flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">LangGraph run</h3><div className="flex flex-wrap gap-1"><Button kind="primary" disabled={busy} onClick={run}>Run graph</Button><Button disabled={busy} onClick={load}>Refresh</Button></div></div>
        <p className="break-words text-xs text-ink-500">security_precheck, security_agent, classify, detect_documents, extract, compare (deterministic), summarize_and_draft, human_review (interrupt), notify. Full console on <Link href="/agent" className="text-accent hover:underline">AI agent</Link>.</p>
        {!st || !st.trace?.length ? <div className="mt-3"><Empty text="No graph run for this case yet. The classic pipeline processed it; click Run graph to execute the LangGraph version with the security agent and the human-in-the-loop pause." /></div> : (
          <ol className="mt-3 min-w-0 space-y-1 text-xs">{st.trace.map((t: any, i: number) => <li key={i} className="min-w-0 rounded bg-ink-50 p-1.5"><b className="break-words">{t.node}</b> <Badge className="bg-ink-100 text-ink-700">{t.actor_type}</Badge> <span className="break-all font-mono text-[10px] text-ink-600">{JSON.stringify(t.output).slice(0, 200)}</span></li>)}</ol>
        )}
      </div>
      <div className="min-w-0 rounded-xl border border-ink-200 bg-white p-4 text-sm">
        <h3 className="mb-2 text-sm font-semibold">Human-in-the-loop</h3>
        {!st ? <Empty text="No state." /> : (
          <div className="space-y-2">
            <KV k="Paused" v={st.paused ? <Badge className="bg-review text-white">waiting for you</Badge> : <Badge className="bg-match-bg text-match-fg">not paused</Badge>} />
            <KV k="Next node" v={<span className="font-mono text-xs">{st.next?.join(", ") || "END"}</span>} />
            {st.security_agent && <KV k="Security agent" v={<span className="text-xs">{st.security_agent.outcome} ({st.security_agent.decided_by}): {st.security_agent.reasoning}</span>} />}
            {st.human_decision && <KV k="Last decision" v={<span className="text-xs">{st.human_decision.action} by {st.human_decision.user_id}</span>} />}
            {st.interrupt && (
              <div className="rounded-lg border border-review bg-review-bg/40 p-3 text-xs">
                <div className="font-semibold text-review-fg">Decision required</div>
                <div className="mt-1">{st.interrupt.summary}</div>
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for the audit log" className="mt-2 w-full rounded-md border border-ink-200 px-2 py-1" />
                <div className="mt-2 flex flex-wrap gap-1">
                  {["approve", "reject", "retry", "mark_no_action", "complete"].map((a) => <Button key={a} kind={a === "approve" ? "success" : a === "reject" ? "danger" : "default"} disabled={busy} onClick={() => resume(a)}>{a.replace("_", " ")}</Button>)}
                </div>
                <p className="mt-2 text-ink-500">Approve requires the approve_send permission (Supervisor or Admin). Notify Party and reassign are done on the Collaboration tab.</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
