"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, post, getSession } from "@/lib/api";
import { Badge, Button, Card, Empty, Toast } from "@/components/ui";

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

/** Operator run console: agent pause/resume, batch (never sends email), CSV/XLSX export, RAG provider visibility. */
export default function WorkbenchPage() {
  const [rag, setRag] = useState<any>(null);
  const [caseId, setCaseId] = useState("case_email_004");
  const [ids, setIds] = useState("case_email_004");
  const [st, setSt] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const [note, setNote] = useState("");
  const me = getSession()?.user;
  const canBatch = me?.permissions.includes("batch") ?? false;
  const canExport = me?.permissions.includes("export_data") ?? false;
  const say = (msg: string, kind: "ok" | "err" = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 4000); };

  useEffect(() => { api("/rag/info").then(setRag).catch(() => {}); }, []);

  const selected = ids.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);

  const run = async () => {
    setBusy(true);
    try { setSt(await post(`/agent/run/${caseId}`)); say("Graph ran"); }
    catch (e: any) { say(e.message, "err"); }
    finally { setBusy(false); }
  };
  const resume = async (action: string) => {
    setBusy(true);
    try { setSt(await post(`/agent/resume/${caseId}`, { action, draft_id: st?.interrupt?.draft_id, note })); say(`Resumed with ${action}`); }
    catch (e: any) { say(e.message, "err"); }
    finally { setBusy(false); }
  };
  const batch = async (action: string, params: Record<string, string> = {}, confirm = false) => {
    if (!selected.length) return say("Enter at least one case id", "err");
    setBusy(true);
    try {
      const r = await post("/cases/batch", { action, case_ids: selected, params, confirm });
      if (r.requires_confirmation) {
        if (window.confirm(`${r.note}\n\nProceed with '${action}' on ${r.count} cases?`)) return batch(action, params, true);
        return;
      }
      if (r.csv) {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([r.csv], { type: "text/csv" }));
        a.download = "cases.csv";
        a.click();
      }
      if (r.xlsx_base64) downloadBase64Xlsx(r.xlsx_base64, r.filename || "cases.xlsx");
      const ok = Object.values(r.results as Record<string, any>).filter((x: any) => x.ok).length;
      say(`${action}: ${ok}/${selected.length} succeeded`);
    } catch (e: any) { say(e.message, "err"); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-5">
      {toast && <Toast {...toast} />}
      <header>
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-bold text-accent-fg transition hover:-translate-x-1 hover:text-accent">← <span>Back to inbox</span></Link>
        <h1 className="dashboard-number mt-6 text-4xl font-bold tracking-[-.04em] text-[#583521] sm:text-5xl">Workbench</h1>
        <p className="mt-3 max-w-3xl text-base font-semibold leading-relaxed text-[#7d6251]">Run the AI agent, batch-process selected cases, and export CSV or Excel. External email is never sent from this page.</p>
      </header>

      <Card title="RAG and providers" className="border-orange-200">
        {rag ? (
          <div className="flex flex-wrap gap-2 text-sm">
            <Badge className="bg-accent-bg text-accent-fg">embeddings {rag.embedding_provider}</Badge>
            <Badge className="bg-ink-100 text-ink-700">{rag.dims} dims</Badge>
            <Badge className="bg-ink-100 text-ink-700">store {rag.vector_store}</Badge>
            <span className="text-ink-500">{rag.chunks} chunks indexed</span>
          </div>
        ) : <Empty text="RAG info unavailable." />}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Run agent on one case" className="border-orange-200">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input value={caseId} onChange={(e) => setCaseId(e.target.value)} className="flex-1 rounded-md border border-ink-200 px-2 py-1.5 font-mono text-xs" aria-label="Case id" />
            <Button kind="primary" disabled={busy} onClick={run}>Run graph</Button>
            <Button disabled={busy} onClick={() => api(`/agent/state/${caseId}`).then(setSt).catch((e) => say(e.message, "err"))}>Refresh state</Button>
          </div>
          {!st ? <p className="mt-3 text-xs text-ink-500">Run or refresh to see interrupt/resume controls. The graph diagram stays on <Link href="/agent" className="text-accent hover:underline">/agent</Link>.</p> : (
            <div className="mt-3 space-y-2 text-sm">
              <div>Paused: <b>{String(!!st.paused)}</b> · next: <span className="font-mono text-xs">{st.next?.join(", ") || "END"}</span> · status: {st.status || "-"}</div>
              {st.interrupt && (
                <div className="rounded-lg border border-review bg-review-bg/40 p-3 text-xs">
                  <div className="mb-1 font-semibold text-review-fg">Human decision required</div>
                  <div>{st.interrupt.summary}</div>
                  <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for the audit log" className="mt-2 w-full rounded-md border border-ink-200 px-2 py-1" />
                  <div className="mt-2 flex flex-wrap gap-1">
                    {(st.interrupt.allowed_actions || []).filter((a: string) => a !== "notify_party" && a !== "reassign").map((a: string) => (
                      <Button key={a} kind={a === "approve" ? "success" : a === "reject" ? "danger" : "default"} disabled={busy} onClick={() => resume(a)}>{a.replace("_", " ")}</Button>
                    ))}
                    <Link href={`/cases/${caseId}?tab=collab`}><Button kind="ghost">notify / reassign in case</Button></Link>
                  </div>
                </div>
              )}
              <Link href={`/cases/${caseId}`} className="text-xs text-accent hover:underline">Open case {caseId}</Link>
            </div>
          )}
        </Card>

        <Card title="Batch run (Supervisor / Admin)" className="border-orange-200">
          <label className="text-xs font-semibold text-ink-600">Case ids (comma or newline)</label>
          <textarea value={ids} onChange={(e) => setIds(e.target.value)} rows={5} className="mt-1 w-full rounded-md border border-ink-200 px-2 py-1.5 font-mono text-xs" />
          <p className="mt-1 text-[11px] text-ink-500">{selected.length} selected · External sending is never batched.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button disabled={busy || !canBatch} onClick={() => batch("classify")}>Classify</Button>
            <Button disabled={busy || !canBatch} onClick={() => batch("compare")}>Compare</Button>
            <Button disabled={busy || !canBatch} onClick={() => batch("draft")}>Prepare drafts</Button>
            <Button disabled={busy || !canBatch} onClick={() => batch("request_review")}>Request review</Button>
            <Button disabled={busy || !canExport} onClick={() => batch("export")}>Export CSV</Button>
            <Button disabled={busy || !canExport} onClick={() => batch("export_xlsx")}>Export Excel</Button>
          </div>
          {!canBatch && <p className="mt-2 text-xs text-review-fg">Batch actions need Supervisor or Admin. Export needs export_data.</p>}
        </Card>
      </div>
    </div>
  );
}
