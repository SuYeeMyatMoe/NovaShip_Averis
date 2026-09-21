"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, post, put, type PolicySuggestion, type PolicySuggestions } from "@/lib/api";
import { Badge, Button, Card, Empty, Toast, fmtDate } from "@/components/ui";
import { ChangeSummary, PolicyEditor, changedSections } from "@/components/policy-editor";

export default function PoliciesPage() {
  const [data, setData] = useState<any>(null);
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<"form" | "json">("form");
  const [jsonText, setJsonText] = useState("");
  const [jsonErr, setJsonErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const [perms, setPerms] = useState<string[]>([]);
  const say = (msg: string, kind: "ok" | "err" = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 3500); };
  const [denied, setDenied] = useState(false);
  const [suggestions, setSuggestions] = useState<PolicySuggestions | null>(null);
  const [accepted, setAccepted] = useState<string[]>([]);   // learned suggestions carried into the unsaved draft
  const loadSuggestions = () => api<PolicySuggestions>("/policies/suggestions").then(setSuggestions).catch(() => setSuggestions(null));
  const load = () => api("/policies").then((d) => { setData(d); setDraft(d.effective); setJsonText(JSON.stringify(d.effective, null, 2)); setJsonErr(null); }).catch((e) => { if (e.status === 403) setDenied(true); else say(e.message, "err"); });
  useEffect(() => { load(); loadSuggestions(); api("/me").then((m) => setPerms(m.permissions)).catch(() => {}); }, []);

  const base: Record<string, any> = data?.effective || {};
  const changes = useMemo(() => changedSections(base, draft), [base, draft]);
  const changeCount = useMemo(() => Object.keys(draft).reduce((n, s) => n + Object.keys(draft[s] || {}).filter((k) => JSON.stringify(draft[s][k]) !== JSON.stringify(base[s]?.[k])).length, 0), [base, draft]);
  const canEdit = perms.includes("edit_policy");
  const dirty = Object.keys(changes).length > 0;

  const switchMode = (m: "form" | "json") => {
    if (m === "json") { setJsonText(JSON.stringify(draft, null, 2)); setJsonErr(null); }
    else { try { setDraft(JSON.parse(jsonText)); setJsonErr(null); } catch (e: any) { setJsonErr(e.message); return; } }
    setMode(m);
  };
  const discard = () => { setDraft(base); setJsonText(JSON.stringify(base, null, 2)); setJsonErr(null); setNote(""); setAccepted([]); };
  const acceptSuggestion = (s: PolicySuggestion) => {
    const next = { ...draft, [s.section]: s.proposed_section };
    setDraft(next); setJsonText(JSON.stringify(next, null, 2)); setJsonErr(null);
    setAccepted((a) => (a.includes(s.id) ? a : [...a, s.id]));
    if (!note.trim()) setNote(`Learned: ${s.title}`);
    say(`Added to pending changes — review and Save as ${nextVersion(data)}`);
  };
  const dismissSuggestion = async (s: PolicySuggestion) => {
    try { setSuggestions(await post<PolicySuggestions>(`/policies/suggestions/${s.id}/dismiss`, {})); say("Suggestion dismissed (audited); the live policy is unchanged"); }
    catch (e: any) { say(e.message, "err"); }
  };
  const save = async () => {
    let body = changes;
    if (mode === "json") { try { body = changedSections(base, JSON.parse(jsonText)); } catch (e: any) { setJsonErr(e.message); return say("Fix the JSON first", "err"); } }
    if (!Object.keys(body).length) return say("Nothing changed", "err");
    if (!note.trim()) return say("Add a change note — it goes into the audit trail", "err");
    setBusy(true);
    try { await put("/policies", { ...body, change_note: note.trim(), ...(accepted.length ? { accepted_suggestions: accepted } : {}) }); say(`Policy saved as a new version (${Object.keys(body).length} section${Object.keys(body).length > 1 ? "s" : ""} changed, audited)`); setNote(""); setAccepted([]); load(); loadSuggestions(); }
    catch (e: any) { say(e.message, "err"); } finally { setBusy(false); }
  };

  if (denied) return (
    <div className="mx-auto max-w-lg rounded-2xl border border-review/40 bg-review-bg p-6 text-sm text-review-fg">
      <div className="font-semibold">Policies are for Operations staff, Supervisors and Admins.</div>
      <p className="mt-1">The Auditor role is read-only on cases and the audit trail; policy rules are outside that scope. Every policy change is still visible to you as a <span className="font-mono">POLICY_UPDATED</span> event on the <Link href="/audit" className="font-semibold underline">Audit</Link> page.</p>
      <Link href="/" className="mt-3 inline-block font-semibold text-accent-fg hover:underline">← Back to inbox</Link>
    </div>
  );
  if (!data) return <div className="p-8 text-center text-sm text-ink-500">Loading…</div>;
  return (
    <div className="space-y-5">
      {toast && <Toast {...toast} />}
      <header>
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-bold text-accent-fg transition hover:-translate-x-1 hover:text-accent">← <span>Back to inbox</span></Link>
        <h1 className="dashboard-number mt-6 text-4xl font-bold tracking-[-.04em] text-[#583521] sm:text-5xl lg:text-6xl">Policies</h1>
        <p className="mt-3 max-w-3xl text-base font-semibold leading-relaxed text-[#7d6251] sm:text-lg">Review and tune the versioned operating rules that guide verification, human review, communication and security decisions. Every save creates a new version and an audit event.</p>
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card className="border-orange-200 transition duration-200 hover:-translate-y-0.5 hover:border-orange-300 hover:shadow-md" title={<span>Edit policy {canEdit ? <Badge className="bg-accent-bg text-accent-fg">ADMIN</Badge> : <Badge className="bg-ink-100 text-ink-600">read-only</Badge>}</span>}
          right={
            <div className="flex items-center gap-1 rounded-lg border border-ink-200 bg-ink-50 p-0.5 text-[11px] font-semibold">
              {(["form", "json"] as const).map((m) => <button key={m} type="button" onClick={() => switchMode(m)} className={`rounded-md px-2.5 py-1 transition ${mode === m ? "bg-white text-ink-900 shadow-sm" : "text-ink-500 hover:text-ink-800"}`}>{m === "form" ? "Form" : "Advanced JSON"}</button>)}
            </div>
          }>
          {!canEdit && <p className="mb-3 rounded-md border border-review/40 bg-review-bg px-3 py-2 text-xs text-review-fg">You can read every setting, but saving requires the ADMIN role.</p>}
          {mode === "form" ? (
            <PolicyEditor base={base} draft={draft} onChange={setDraft} disabled={!canEdit} />
          ) : (
            <div>
              <p className="mb-2 text-xs text-ink-500">The full effective policy. Edit any value; only sections that differ from the active version are sent.</p>
              <textarea value={jsonText} onChange={(e) => { setJsonText(e.target.value); setJsonErr(null); }} rows={26} disabled={!canEdit} spellCheck={false} className={`w-full rounded-md border p-2 font-mono text-[11px] ${jsonErr ? "border-mismatch" : "border-ink-200"}`} />
              {jsonErr && <div className="mt-1 text-xs text-mismatch-fg">Invalid JSON: {jsonErr}</div>}
            </div>
          )}

          <div className={`mt-4 rounded-xl border p-3 ${dirty ? "border-accent-ring bg-accent-bg/30" : "border-ink-200 bg-ink-50/60"}`}>
            <div className="flex items-center justify-between gap-2">
              <div className="text-xs font-semibold text-ink-800">Pending changes {changeCount > 0 && <Badge className="bg-accent text-white">{changeCount}</Badge>}</div>
              {dirty && <button type="button" onClick={discard} className="text-[11px] font-semibold text-ink-500 hover:text-mismatch">discard all</button>}
            </div>
            <div className="mt-2"><ChangeSummary base={base} draft={mode === "form" ? draft : safeParse(jsonText, draft)} /></div>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Change note (required — written to the audit trail)" disabled={!canEdit} className="flex-1 rounded-md border border-ink-200 bg-white px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-ring/60 disabled:bg-ink-50" />
              <Button kind="primary" disabled={!canEdit || !dirty || busy} onClick={save}>{busy ? "Saving…" : `Save as ${nextVersion(data)}`}</Button>
            </div>
          </div>
        </Card>

        <div className="space-y-4">
          <Card className="border-orange-200 transition duration-200 hover:-translate-y-0.5 hover:border-orange-300 hover:shadow-md" title={<span>Active policy <Badge className="bg-accent-bg text-accent-fg">{data.active.version}</Badge></span>} right={<span className="text-xs text-ink-500">by {data.active.updated_by} · {fmtDate(data.active.updated_at)}</span>}>
            <ul className="list-disc space-y-1 pl-5 text-sm">{data.explanation.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>
          </Card>
          <OperatorProfileCard suggestions={suggestions} accepted={accepted} canEdit={canEdit} onAccept={acceptSuggestion} onDismiss={dismissSuggestion} />
          <Card className="border-orange-200 transition duration-200 hover:-translate-y-0.5 hover:border-orange-300 hover:shadow-md" title="Version history (every change is audited)">
            {data.versions.length ? <table className="w-full text-xs"><thead className="text-[11px] uppercase text-ink-500"><tr><th className="py-1 text-left">Version</th><th className="text-left">By</th><th className="text-left">When</th><th className="text-left">Note</th></tr></thead>
              <tbody>{[...data.versions].reverse().map((v: any) => <tr key={v.id} className="border-t border-ink-100"><td className="py-1 font-mono">{v.version}{v.version === data.active.version && <span className="ml-1 text-[10px] text-match-fg">active</span>}</td><td>{v.updated_by}</td><td className="whitespace-nowrap">{fmtDate(v.updated_at)}</td><td>{v.change_note}</td></tr>)}</tbody></table> : <Empty text="No versions" />}
          </Card>
        </div>
      </div>
    </div>
  );
}

function nextVersion(data: any) { return `v${(data?.versions?.length || 0) + 1}`; }
function safeParse(text: string, fallback: any) { try { return JSON.parse(text); } catch { return fallback; } }

/** What the operator guard has learned for the signed-in user from the audit log, the limits that apply right now,
 *  and what the desk learned from security-gate decisions (proposed, never applied by itself). */
function OperatorProfileCard({ suggestions, accepted, canEdit, onAccept, onDismiss }: { suggestions: PolicySuggestions | null; accepted: string[]; canEdit: boolean; onAccept: (s: PolicySuggestion) => void; onDismiss: (s: PolicySuggestion) => void }) {
  const [profile, setProfile] = useState<any>(null);
  const [cursor, setCursor] = useState(0);
  useEffect(() => { api("/me/operator-profile").then(setProfile).catch(() => setProfile(null)); }, []);
  const items = suggestions?.items || [];
  useEffect(() => { if (cursor >= items.length) setCursor(Math.max(0, items.length - 1)); }, [items.length, cursor]);
  if (!profile) return null;
  const b = profile.baseline, s = profile.settings;
  const current = items[cursor];
  const fmtList = (v: any) => Array.isArray(v) ? (v.length ? v.join(", ") : "(empty)") : String(v);
  return (
    <Card className="border-orange-200 transition duration-200 hover:-translate-y-0.5 hover:border-orange-300 hover:shadow-md" title="Operator guard · learned for you">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-ink-500">audited actions ({b.baseline_days} days)</dt><dd className="font-mono">{b.events}</dd>
        <dt className="text-ink-500">your usual pace</dt><dd className="font-mono">{b.median_per_min.toFixed(1)} / min (p95 {b.p95_per_min})</dd>
        <dt className="text-ink-500">usual hours (UTC)</dt><dd className="font-mono">{b.usual_hours_utc ? `${String(b.usual_hours_utc[0]).padStart(2, "0")}:00-${String(b.usual_hours_utc[1]).padStart(2, "0")}:59` : "not enough history"}</dd>
        <dt className="text-ink-500">burst warning at</dt><dd className="font-mono">{b.effective_burst_limit} actions / {b.burst_window_s}s <span className="text-ink-500">({b.learned ? "learned" : `fixed until ${s.min_baseline_events} actions`})</span></dd>
        <dt className="text-ink-500">auto-draft after</dt><dd className="font-mono">{b.auto_draft_after} actions with no live draft</dd>
      </dl>
      <section className="mt-3 border-t border-orange-100 pt-3" aria-label="Learned from the security gate">
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-xs font-bold uppercase tracking-wide text-accent-fg">Security gate · learned from your decisions</h4>
          {items.length > 1 && (
            <div className="flex items-center gap-1 text-[11px] text-ink-500">
              <button type="button" onClick={() => setCursor((c) => Math.max(0, c - 1))} disabled={cursor === 0} aria-label="Previous suggestion" className="rounded-md border border-ink-200 px-2 py-0.5 font-semibold text-ink-700 transition hover:bg-ink-50 disabled:cursor-not-allowed disabled:opacity-40">‹ Prev</button>
              <span className="font-mono">{cursor + 1} / {items.length}</span>
              <button type="button" onClick={() => setCursor((c) => Math.min(items.length - 1, c + 1))} disabled={cursor >= items.length - 1} aria-label="Next suggestion" className="rounded-md border border-ink-200 px-2 py-0.5 font-semibold text-ink-700 transition hover:bg-ink-50 disabled:cursor-not-allowed disabled:opacity-40">Next ›</button>
            </div>
          )}
        </div>
        {!current ? (
          <div className="mt-2 text-xs text-ink-600">
            <p>Nothing learned yet. After {suggestions?.settings?.min_archives ?? 3} archived mails share a pattern (the same subject wording, the same sender, or the same word in the sender&apos;s domain) a suggestion appears here.</p>
            {suggestions?.progress?.length ? <ul className="mt-1.5 space-y-0.5 text-[11px] text-ink-500">{suggestions.progress.map((p) => <li key={`${p.recipe}:${p.bucket}`} className="flex justify-between gap-2"><span className="truncate" title={p.bucket}>{p.label || p.bucket}</span><span className="shrink-0 font-mono">{p.count} / {p.needed}</span></li>)}</ul> : null}
          </div>
        ) : (
          <div className="mt-2 rounded-xl border border-orange-200 bg-[#fffaf5] p-3 text-xs">
            <div className="text-sm font-semibold text-ink-900">{current.title}</div>
            <p className="mt-1 text-ink-600">{current.rationale}</p>
            <div className="mt-2 rounded-lg bg-white px-2.5 py-1.5 font-mono text-[11px]"><span className="text-ink-500">{current.section}.{current.key}</span> <span className="text-ink-400">{fmtList(current.from_value)}</span> → <span className="font-semibold text-accent-fg">{fmtList(current.to_value)}</span></div>
            <div className="mt-2 flex flex-wrap items-center gap-1 text-[11px] text-ink-500">
              <span>Evidence:</span>
              {current.evidence.slice(0, 4).map((e) => <Link key={e.case_id} href={`/cases/${e.case_id}`} className="rounded-full border border-ink-200 bg-white px-2 py-0.5 font-mono text-accent-fg hover:border-accent" title={`${e.subject} · ${e.outcome} · archived ${fmtDate(e.when)}`}>{e.case_id.replace("case_", "").slice(0, 14)}</Link>)}
              {current.count > 4 && <span>+{current.count - 4} more</span>}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {canEdit ? (
                <>
                  <Button kind="primary" disabled={accepted.includes(current.id)} onClick={() => onAccept(current)}>{accepted.includes(current.id) ? "In pending changes" : "Accept"}</Button>
                  <Button kind="ghost" disabled={accepted.includes(current.id)} onClick={() => onDismiss(current)}>Dismiss</Button>
                </>
              ) : <span className="text-[11px] text-ink-500">Ask an Admin to accept or dismiss this suggestion.</span>}
            </div>
          </div>
        )}
        <p className="mt-2 text-[11px] text-ink-500">Suggestions are never applied by themselves and never enable auto-send: Accept puts the change into the pending changes, and it only takes effect when you save a new version.</p>
      </section>
      <p className="mt-2 text-[11px] text-ink-500">Warnings are shown as a dialog{s.warning_dialog ? "" : " (off in policy)"}, recorded in the audit trail, and never lock an account. Edit the <span className="font-mono">operator_guard</span> section to change the rules.</p>
    </Card>
  );
}
