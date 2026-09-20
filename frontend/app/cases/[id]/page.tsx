"use client";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, post, getSession, type CaseView, type ComparisonField } from "@/lib/api";
import { Badge, Button, Card, Confidence, Empty, KV, PRIORITY_COLORS, StatusBadge, Toast, fmtDate } from "@/components/ui";
import { EvidencePanel, SevenFieldCard } from "@/components/comparison";
import { CollaborationPanel } from "@/components/collab";
import { AskPanel, AttachmentsPanel, AuditPanel, DraftPanel, EmailPanel, Timeline } from "@/components/panels";
import { AgentPanel } from "@/components/agent-panel";

const TABS = [["overview","Overview"],["email","Original Email"],["attachments","Attachments"],["compare","Seven-Field Comparison"],["evidence","Evidence"],["summary","AI Summary"],["drafts","Draft Actions"],["collab","Collaboration"],["ask","Ask AI"],["agent","AI Agent"],["audit","Audit History"]] as const;

export default function CasePage() {
  const { id } = useParams<{ id: string }>();
  const sp = useSearchParams();
  const router = useRouter();
  const [c, setC] = useState<CaseView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<string>(sp.get("tab") || "overview");
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const [perms, setPerms] = useState<string[]>([]);
  const [evField, setEvField] = useState<ComparisonField | null>(null);
  const say = (msg: string, kind: "ok" | "err" = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 3500); };
  const load = useCallback(() => api<CaseView>(`/cases/${id}`).then((d) => { setC(d); setErr(null); }).catch((e) => setErr(e.message)), [id]);
  useEffect(() => {
    load();
    const permsFromSession = getSession()?.user.permissions;
    if (permsFromSession) setPerms(permsFromSession);
    else api("/me").then((m) => setPerms(m.permissions)).catch(() => {});
  }, [load]);

  if (err) return <div className="rounded-xl border border-mismatch bg-mismatch-bg p-4 text-sm text-mismatch-fg">Could not load case: {err}</div>;
  if (!c) return <div className="p-8 text-center text-sm text-ink-500">Loading case…</div>;
  const e = c.email!;
  const docs: Record<string, { name: string; text: string | null }> = Object.fromEntries(e.attachments.map((a) => [a.id, { name: a.file_name, text: a.raw_text }]));
  const act = async (path: string, body?: any) => {
    try {
      await post(`/cases/${c.id}${path}`, body);
      const d = await api<CaseView>(`/cases/${c.id}`);
      setC(d);
      const notice = (d.anomalies || []).find((a) => a.signal === "AUTO_DRAFT_AFTER_REPEATED_ACTIONS" || a.signal.startsWith("OPERATOR_"));
      if (notice) say(notice.evidence, notice.signal.startsWith("OPERATOR_") ? "err" : "ok");
      else say("Done");
    } catch (x: any) {
      say(x.message, "err");
    }
  };

  return (
    <div className="min-w-0 space-y-4">
      {toast && <Toast {...toast} />}
      <div className="min-w-0 rounded-2xl border border-ink-200 bg-white/95 p-3 shadow-card sm:p-5">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-500"><Link href="/" className="hover:underline">← Inbox</Link><span>·</span><span className="font-mono">{c.id}</span><span>·</span><Link href={`/cases/${c.id}?tab=email`} onClick={() => setTab("email")} className="text-accent hover:underline">source email {e.id}</Link></div>
            <div className="mt-3 text-[10px] font-bold uppercase tracking-[0.18em] text-accent">Case command view</div>
            <h1 className="mt-1 break-words text-lg font-semibold tracking-tight text-ink-900 sm:text-xl">{e.subject}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-600">
              <span className="font-mono">{e.sender}</span><span>·</span><span>{fmtDate(e.received_at)}</span><span>·</span>
              <Badge className="bg-ink-100 text-ink-700">{c.intent.replace(/_/g, " ")}</Badge>
              <Badge className={c.security.outcome === "SAFE" ? "bg-match-bg text-match-fg" : "bg-mismatch-bg text-mismatch-fg"}>{c.security.outcome}</Badge>
              <span className={PRIORITY_COLORS[c.priority]}>{c.priority}</span>
              <Confidence value={c.classification.confidence} label="intent confidence" />
              {!c.action_required && <Badge className="bg-ink-100 text-ink-600">No reply needed</Badge>}
            </div>
          </div>
          <div className="flex w-full flex-col items-start gap-2 xl:w-auto xl:items-end">
            <StatusBadge status={c.status} />
            <div className="flex w-full flex-wrap justify-start gap-1 xl:w-auto xl:justify-end">
              <Button onClick={() => act("/retry")} title="Re-run the whole pipeline">Retry</Button>
              <Button onClick={() => { setTab("drafts"); }}>Draft Reply</Button>
              <Button onClick={() => { setTab("collab"); }}>Assign / Notify Party</Button>
              <Button onClick={() => act("/request-review", { note: "Manual review requested" })}>Request review</Button>
              {c.action_required && <Button onClick={() => act("/no-action")}>Mark no action</Button>}
              <Button kind="success" onClick={() => act("/complete", { note: "Completed from UI" })}>Mark complete</Button>
            </div>
          </div>
        </div>
        <div className="mt-3"><Timeline c={c} /></div>
      </div>

      {c.errors.filter((x) => !x.resolved).length > 0 && (
        <div className="rounded-xl border border-mismatch/40 bg-mismatch-bg/40 p-3 text-xs">
          <div className="mb-1 font-semibold text-mismatch-fg">Processing issues — visible and recoverable</div>
          {c.errors.map((x) => (
            <div key={x.id} className="flex flex-wrap items-center gap-2 py-0.5"><Badge className="bg-mismatch text-white">{x.category}</Badge><span className="text-ink-500">step {x.step}:</span><span>{x.message}</span><span className="text-ink-500">→ {x.recovery}</span>
              <span className="ml-auto flex gap-1"><Button onClick={() => act("/retry")}>Retry</Button><Button onClick={() => setTab("attachments")}>Upload missing file</Button><Button onClick={() => setTab("collab")}>Reassign</Button><Button onClick={() => act("/request-review")}>Human review</Button></span></div>
          ))}
        </div>
      )}

      <div className="max-w-full overflow-x-auto rounded-xl border border-ink-200 bg-white/80 p-1.5 shadow-sm">
        <div className="flex min-w-max gap-1">
        {TABS.map(([k, label]) => <button key={k} onClick={() => { setTab(k); router.replace(`/cases/${c.id}?tab=${k}`); }} className={`whitespace-nowrap rounded-lg px-3 py-2 text-sm transition ${tab === k ? "bg-ink-900 font-semibold text-white shadow-sm" : "text-ink-500 hover:bg-ink-100 hover:text-ink-800"}`}>{label}{k === "compare" && c.mismatch_count > 0 && <span className="ml-1 rounded-full bg-mismatch px-1.5 text-[10px] text-white">{c.mismatch_count}</span>}{k === "drafts" && c.drafts.length > 0 && <span className="ml-1 rounded-full bg-accent px-1.5 text-[10px] text-white">{c.drafts.length}</span>}</button>)}
        </div>
      </div>

      {tab === "overview" && (
        <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="min-w-0 space-y-4">
            <SevenFieldCard cmp={c.comparison} reviewReason={c.review_reason} onEvidence={(f) => { setEvField(f); setTab("evidence"); }} />
            <Card title="AI summary" right={<span className="text-[11px] text-ink-500">{c.summary?.generated_by} · evidence: {c.summary?.evidence_refs.length}</span>}><p className="break-words text-sm leading-relaxed">{c.summary?.text || "—"}</p></Card>
          </div>
          <div className="min-w-0 space-y-4">
            <Card title="Recommended action">
              {c.recommendation ? (<div className="space-y-1.5 text-sm">
                <div className="flex items-center gap-2"><Badge className="bg-accent text-white">{c.recommendation.action_type.replace(/_/g, " ")}</Badge><span className={PRIORITY_COLORS[c.recommendation.priority]}>{c.recommendation.priority}</span><Confidence value={c.recommendation.confidence} /></div>
                <div>{c.recommendation.recommended_action}</div><div className="text-xs text-ink-500">Why: {c.recommendation.reason}</div><div className="text-xs text-ink-500">Owner: {c.recommendation.responsible_role.replace(/_/g, " ")}</div>
              </div>) : <Empty text="No recommendation" />}
            </Card>
            <Card title="Classification & security">
              <KV k="Intent" v={`${c.intent} (${c.classification.decided_by})`} /><KV k="Rationale" v={<span className="text-xs">{c.classification.rationale}</span>} />
              <KV k="Security" v={`${c.security.outcome} · score ${c.security.score}`} /><KV k="Why" v={<span className="text-xs">{c.security.rationale}</span>} />
              {c.security.signals.length > 0 && <ul className="mt-2 space-y-1 text-xs">{c.security.signals.map((s, i) => <li key={i} className="rounded bg-ink-50 p-1.5"><b>{s.signal}</b> <span className="text-ink-500">({s.severity})</span> — {s.evidence} <span className="text-ink-500">→ {s.recommended_action}</span></li>)}</ul>}
            </Card>
            {c.anomalies.length > 0 && <Card title={c.anomalies.some((a) => a.signal.startsWith("OPERATOR_") || a.signal.startsWith("AUTO_DRAFT")) ? "Unusual operator signals" : "Unusual behaviour signals"}><ul className="space-y-1 text-xs">{c.anomalies.map((a, i) => <li key={i} className={`rounded p-1.5 ${a.signal.startsWith("OPERATOR_") || a.signal.startsWith("AUTO_DRAFT") ? "bg-mismatch-bg/50" : "bg-review-bg/60"}`}><b>{a.signal}</b> <span className="text-ink-500">({a.severity})</span> — {a.evidence} <span className="text-ink-500">→ {a.recommended_action}</span></li>)}</ul></Card>}
            <Card title="Documents">
              <KV k="SI" v={c.si_available ? <span className="text-match-fg">available</span> : <span className="text-review-fg">missing / unreadable</span>} />
              <KV k="Draft BL" v={c.bl_available ? <span className="text-match-fg">available</span> : <span className="text-review-fg">missing / unreadable</span>} />
              {c.review_reason && <KV k="Review reason" v={<Badge className="bg-review-bg text-review-fg">{c.review_reason}</Badge>} />}
              <KV k="Assigned" v={c.assigned_user_id || "—"} /><KV k="Shared with" v={c.shared_with.join(", ") || "—"} />
            </Card>
          </div>
        </div>
      )}
      {tab === "email" && <EmailPanel c={c} />}
      {tab === "attachments" && <AttachmentsPanel c={c} onChange={load} say={say} />}
      {tab === "compare" && <div className="space-y-3"><SevenFieldCard cmp={c.comparison} reviewReason={c.review_reason} onEvidence={(f) => { setEvField(f); setTab("evidence"); }} />
        {c.comparison && <Card title="Discrepancy report"><ReportView id={c.id} /></Card>}</div>}
      {tab === "evidence" && <div className="space-y-2">{evField && <div className="flex items-center gap-2 text-xs"><span>Showing evidence for <b>{evField.label}</b></span><Button kind="ghost" onClick={() => setEvField(null)}>show all seven</Button></div>}<EvidencePanel cmp={c.comparison} docs={docs} selected={evField} /></div>}
      {tab === "summary" && <Card title="AI summary (evidence-grounded)"><p className="text-sm leading-relaxed">{c.summary?.text}</p><div className="mt-3 text-xs text-ink-500">Evidence refs: {c.summary?.evidence_refs.join(", ")}</div>
        <div className="mt-3 text-[11px] uppercase text-ink-500">Decision trace</div><ol className="mt-1 space-y-1 text-xs">{c.trace.map((t, i) => <li key={i} className="rounded bg-ink-50 p-1.5"><b>{t.node}</b> <Badge className="bg-ink-100 text-ink-700">{t.actor_type}</Badge> <span className="font-mono text-[10px] text-ink-600">{JSON.stringify(t.output).slice(0, 240)}</span></li>)}</ol></Card>}
      {tab === "drafts" && <DraftPanel c={c} onChange={load} say={say} perms={perms} />}
      {tab === "collab" && <CollaborationPanel c={c} onChange={load} say={say} />}
      {tab === "ask" && <AskPanel c={c} say={say} />}
      {tab === "agent" && <AgentPanel c={c} onChange={load} say={say} />}
      {tab === "audit" && <AuditPanel c={c} />}
    </div>
  );
}

function ReportView({ id }: { id: string }) {
  const [r, setR] = useState<any>(null);
  useEffect(() => { api(`/cases/${id}/report`).then(setR).catch(() => {}); }, [id]);
  if (!r) return null;
  return (
    <div className="grid gap-3 md:grid-cols-2 text-sm">
      <div className="space-y-1"><KV k="Case ID" v={r.case_id} /><KV k="Email" v={r.email_id} /><KV k="Sender" v={r.sender} /><KV k="Subject" v={r.subject} /><KV k="Received" v={fmtDate(r.received_at)} /><KV k="SI file" v={r.si_file} /><KV k="Draft BL file" v={r.bl_file} /><KV k="Compared" v={fmtDate(r.compared_at)} /><KV k="Status" v={r.status} /><KV k="Assigned" v={r.assigned_user_id || "—"} /><KV k="Shared with" v={r.shared_with.join(", ") || "—"} /><KV k="Audit" v={<Link className="text-accent hover:underline" href={`/cases/${id}?tab=audit`}>view audit history</Link>} /></div>
      <pre className="whitespace-pre-wrap rounded-md bg-ink-50 p-3 font-mono text-xs">{r.compact}</pre>
    </div>
  );
}
