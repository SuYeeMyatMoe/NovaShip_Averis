"use client";
import { useEffect, useState } from "react";
import { api, post, type CaseView } from "@/lib/api";
import { Badge, Button, Card, Empty, fmtDate } from "@/components/ui";
import { useOperatorWarning } from "@/lib/operator-warning";

type Recipient = { id: string; label: string; recipient_type: string; external: boolean; roles: string[]; allowed: boolean };

const STEPS = ["Notify Party values", "Select recipient", "Preview", "Send / share"] as const;

/**
 * Collaboration tab. The whole share flow lives inside the Notify Party card:
 * Notify Party values -> Select recipient -> Preview -> Confirm -> Send/Share -> Audit -> Status.
 */
export function CollaborationPanel({ c, onChange, say }: { c: CaseView; onChange: () => void; say: (m: string, k?: "ok" | "err") => void }) {
  const warn = useOperatorWarning();
  const [recips, setRecips] = useState<Recipient[]>([]);
  const [started, setStarted] = useState(["NOTIFY_PARTY", "AWAITING_RESPONSE"].includes(c.status));
  const [pick, setPick] = useState<string>("");
  const [filter, setFilter] = useState<"all" | "internal" | "external">("all");
  const [message, setMessage] = useState("");
  const [due, setDue] = useState("");
  const [fields, setFields] = useState<string[]>(c.comparison?.mismatch_fields || []);
  const [preview, setPreview] = useState<any>(null);
  const [users, setUsers] = useState<any[]>([]);
  const [shares, setShares] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [assignTo, setAssignTo] = useState("");

  const reload = () => {
    api(`/cases/${c.id}/recipients`).then((d) => setRecips(d.recipients)).catch(() => {});
    api(`/cases/${c.id}/audit`).then((d) => setShares(d.shares)).catch(() => {});
    api("/users").then((d) => setUsers(d.users)).catch(() => {});
  };
  useEffect(reload, [c.id, c.updated_at]);

  const chosen = recips.find((r) => r.id === pick);
  const step = preview ? 3 : chosen ? 2 : 1;

  const beginNotify = async () => {
    setBusy(true);
    try { const d = await post(`/cases/${c.id}/notify-party`); setRecips(d.recipients); setStarted(true); say("Notify Party flow started — select an authorised recipient"); onChange(); }
    catch (e: any) { say(e.message, "err"); } finally { setBusy(false); }
  };
  const body = (preview_only: boolean, confirm_external = false) => ({
    recipient_type: chosen!.recipient_type, recipient_user_id: chosen!.external ? undefined : chosen!.id, recipient_party_id: chosen!.external ? chosen!.id : undefined,
    message: message || undefined, due_date: due || undefined, include_fields: fields, preview_only, confirm_external,
  });
  const doPreview = async () => { if (!chosen) return say("Select a recipient", "err"); setBusy(true); try { setPreview(await post(`/cases/${c.id}/share`, body(true))); } catch (e: any) { if (!warn.notice(e)) say(e.message, "err"); } finally { setBusy(false); } };
  const doSend = async () => {
    if (!chosen) return; setBusy(true);
    try {
      const r = preview?.share?.id ? await post(`/cases/${c.id}/share/${preview.share.id}/confirm`) : await post(`/cases/${c.id}/share`, body(false, true));
      say(r.share.is_external ? "External notification sent & audited" : "Shared internally & audited"); setPreview(null); setPick(""); onChange(); reload();
    } catch (e: any) { say(e.message, "err"); } finally { setBusy(false); }
  };
  const assign = async () => { if (!assignTo) return; try { await post(`/cases/${c.id}/assign`, { user_id: assignTo }); say("Assigned"); onChange(); } catch (e: any) { say(e.message, "err"); } };
  const ack = async (id: string) => { try { await post(`/shares/${id}/acknowledge`, { response: "Acknowledged" }); reload(); } catch (e: any) { say(e.message, "err"); } };

  const npField = c.comparison?.fields.find((f) => f.field === "notify_party");
  const visible = recips.filter((r) => filter === "all" || (filter === "external") === r.external);

  return (
    <div className="grid min-w-0 max-w-full gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      {warn.dialog}
      <Card title="Notify Party" right={!started ? <Button kind="primary" disabled={busy} onClick={beginNotify}>Start Notify Party flow</Button> : <Badge className="bg-accent-soft text-accent-fg">flow active</Badge>}>
        <Stepper step={step} />

        {/* Step 1 — the extracted values (comparison only, never authorisation) */}
        <section className="mt-4">
          <StepHeading n={1} title="Notify Party on the documents" done={step > 0} />
          {npField ? (
            <div className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
              <div className="rounded-md bg-ink-50 p-2"><div className="text-[11px] uppercase text-ink-500">On SI (source of truth)</div><div className="font-mono text-xs">{npField.si_original || "—"}</div></div>
              <div className="rounded-md bg-ink-50 p-2"><div className="text-[11px] uppercase text-ink-500">On Draft BL</div><div className="font-mono text-xs">{npField.bl_original || "—"}</div></div>
              <div className="sm:col-span-2"><Badge className={npField.result === "MATCH" ? "bg-match-bg text-match-fg" : "bg-mismatch-bg text-mismatch-fg"}>{npField.result === "MATCH" ? "values match" : npField.result.replace(/_/g, " ")}</Badge></div>
            </div>
          ) : <div className="mt-2"><Empty text="Notify Party not extracted yet (no comparison). You can still share the case internally." /></div>}
          <p className="mt-2 text-xs text-ink-500">The extracted Notify Party is a <b>comparison value</b>, not authorisation to send. Only approved recipients can be chosen below.</p>
        </section>

        {/* Step 2 — select an authorised recipient */}
        <section className="mt-5">
          <StepHeading n={2} title="Select an authorised recipient" done={!!chosen} />
          {!started && npField && npField.result !== "MATCH" && (
            <p className="mt-2 rounded-md border border-accent-ring/60 bg-accent-bg/50 px-2.5 py-1.5 text-xs text-accent-fg">Notify Party differs between SI and BL. <b>Start Notify Party flow</b> moves the case to NOTIFY_PARTY (audited) so the follow-up is tracked; internal shares work either way.</p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-1 text-[11px]">
            {(["all", "internal", "external"] as const).map((f) => (
              <button key={f} type="button" onClick={() => setFilter(f)} className={`rounded-full border px-2.5 py-1 font-semibold capitalize transition ${filter === f ? "border-accent bg-accent-bg text-accent-fg" : "border-ink-200 bg-white text-ink-600 hover:border-accent"}`}>{f}</button>
            ))}
            <span className="w-full text-ink-500 sm:ml-auto sm:w-auto">{recips.filter((r) => r.allowed).length} of {recips.length} permitted for your role</span>
          </div>
          <div className="mt-2 max-h-56 space-y-1 overflow-auto scrollbar-thin">
            {visible.map((r) => (
              <label key={r.id} className={`flex min-w-0 max-w-full items-center gap-2 overflow-hidden rounded-md border px-2 py-1.5 text-xs ${pick === r.id ? "border-accent bg-accent-bg/40" : "border-ink-100"} ${!r.allowed ? "opacity-50" : "cursor-pointer"}`}>
                <input type="radio" name="recip" disabled={!r.allowed} checked={pick === r.id} onChange={() => { setPick(r.id); setPreview(null); }} />
                <span className="min-w-0 flex-1 truncate">{r.label}</span>
                <Badge className={`shrink-0 ${r.external ? "bg-accent-soft text-accent-fg" : "bg-ink-100 text-ink-700"}`}>{r.external ? "External" : "Internal"}</Badge>
                <span className="hidden text-[10px] text-ink-500 sm:inline">{r.roles.join("/")}</span>
                {!r.allowed && <span className="text-[10px] text-mismatch">not permitted</span>}
              </label>
            ))}
            {!visible.length && <Empty text="No recipients in this group." />}
          </div>
          <div className="mt-3 grid gap-2">
            <div className="text-[11px] uppercase text-ink-500">Fields to disclose (default: mismatches only)</div>
            <div className="flex flex-wrap gap-2 text-xs">
              {(c.comparison?.fields || []).map((f) => (
                <label key={f.field} className="flex items-center gap-1"><input type="checkbox" checked={fields.includes(f.field)} onChange={(e) => { setFields(e.target.checked ? [...fields, f.field] : fields.filter((x) => x !== f.field)); setPreview(null); }} />{f.label}{f.result === "MISMATCH" && <span className="text-mismatch">●</span>}</label>
              ))}
            </div>
            <textarea value={message} onChange={(e) => { setMessage(e.target.value); setPreview(null); }} placeholder="Optional message to the recipient" className="w-full rounded-md border border-ink-200 p-2 text-sm" rows={2} />
            <div className="flex flex-wrap items-center gap-2 text-xs"><span className="text-ink-500">Due date</span><input type="date" value={due} onChange={(e) => { setDue(e.target.value); setPreview(null); }} className="rounded-md border border-ink-200 px-2 py-1" /></div>
          </div>
        </section>

        {/* Step 3 + 4 — preview exactly what leaves, then confirm */}
        <section className="mt-5">
          <StepHeading n={3} title="Preview exactly what will be shared" done={!!preview} />
          <div className="mt-2 flex flex-wrap gap-2">
            <Button kind={preview ? "default" : "primary"} disabled={!chosen || busy} onClick={doPreview}>{preview ? "Refresh preview" : "Preview"}</Button>
            {chosen && !chosen.external && !preview && <Button disabled={busy} onClick={doSend}>Share internally without preview</Button>}
          </div>
          {preview ? (
            <div className="mt-3 space-y-2">
              <div className="flex min-w-0 items-center gap-2 text-xs"><Badge className={`shrink-0 ${preview.share.is_external ? "bg-accent-soft text-accent-fg" : "bg-ink-100 text-ink-700"}`}>{preview.share.is_external ? "External" : "Internal"}</Badge><span className="min-w-0 truncate text-ink-500">to {preview.share.recipient_label}</span></div>
              <pre className="whitespace-pre-wrap rounded-md bg-ink-50 p-3 font-mono text-[11px] text-ink-900">{preview.preview}</pre>
              <StepHeading n={4} title={preview.share.is_external ? "Confirm and send to the external party" : "Send / share internally"} done={false} />
              {preview.share.is_external ? (
                <div className="rounded-md border border-review bg-review-bg p-3 text-xs text-review-fg">
                  <b>Human confirmation required for external sending.</b> Only the fields listed above are disclosed. The original email body is not included.
                  <div className="mt-2"><Button kind="success" disabled={busy} onClick={doSend}>Confirm & send to external party</Button></div>
                </div>
              ) : <Button kind="primary" disabled={busy} onClick={doSend}>Send / share internally</Button>}
            </div>
          ) : <p className="mt-2 text-xs text-ink-500">{chosen ? "Preview shows the exact fields and message the recipient will receive." : "Select a recipient first."}</p>}
        </section>
      </Card>

      <div className="space-y-4">
        <Card title="Assign owner">
              <div className="flex w-full min-w-0 flex-col gap-2 xl:flex-row">
            <select value={assignTo} onChange={(e) => setAssignTo(e.target.value)} className="w-full min-w-0 flex-1 rounded-md border border-ink-200 px-2 py-1.5 text-sm">
              <option value="">Select internal user…</option>
              {users.map((u) => <option key={u.id} value={u.id}>{u.display_name} · {u.roles.join("/")}</option>)}
            </select>
            <Button kind="primary" className="w-full xl:w-auto" onClick={assign}>Assign / Reassign</Button>
          </div>
          <div className="mt-2 text-xs text-ink-500">Currently: {users.find((u) => u.id === c.assigned_user_id)?.display_name || "unassigned"}{c.assigned_team_id ? ` · team ${c.assigned_team_id}` : ""}</div>
        </Card>

        <Card title="Shares & notifications" right={<span className="text-[11px] text-ink-500">audit → status</span>}>
          {shares.length ? (
            <>
            <div className="space-y-2 sm:hidden">{shares.map((s) => (
              <div key={s.id} className="rounded-lg border border-ink-100 p-2 text-xs">
                <div className="break-words font-semibold text-ink-900">{s.recipient_label}</div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5"><Badge className={s.is_external ? "bg-accent-soft text-accent-fg" : "bg-ink-100 text-ink-700"}>{s.recipient_type.replace(/_/g, " ")}</Badge><span>{s.status}</span></div>
                <div className="mt-1 text-[10px] text-ink-500">Sent {fmtDate(s.sent_at)} · {s.acknowledged_at ? `ack ${fmtDate(s.acknowledged_at)}` : s.viewed_at ? `viewed ${fmtDate(s.viewed_at)}` : "not viewed"}</div>
                {(s.status === "SENT" || s.status === "SIMULATED") && <div className="mt-2"><Button kind="ghost" onClick={() => ack(s.id)}>Mark acknowledged</Button></div>}
              </div>
            ))}</div>
            <div className="hidden max-w-full overflow-x-auto sm:block"><table className="w-full min-w-[640px] text-xs">
              <thead className="text-[11px] uppercase text-ink-500"><tr><th className="py-1 text-left">Recipient</th><th className="text-left">Type</th><th className="text-left">Status</th><th className="text-left">Sent</th><th className="text-left">Viewed / Ack</th><th /></tr></thead>
              <tbody>{shares.map((s) => (
                <tr key={s.id} className="border-t border-ink-100">
                  <td className="py-1.5 pr-2">{s.recipient_label}<div className="text-[10px] text-ink-500">by {s.shared_by}</div></td>
                  <td><Badge className={s.is_external ? "bg-accent-soft text-accent-fg" : "bg-ink-100 text-ink-700"}>{s.recipient_type.replace(/_/g, " ")}</Badge></td>
                  <td>{s.status}</td><td className="whitespace-nowrap">{fmtDate(s.sent_at)}</td><td className="whitespace-nowrap">{s.acknowledged_at ? `ack ${fmtDate(s.acknowledged_at)}` : s.viewed_at ? fmtDate(s.viewed_at) : "—"}</td>
                  <td>{(s.status === "SENT" || s.status === "SIMULATED") && <Button kind="ghost" onClick={() => ack(s.id)}>Mark acknowledged</Button>}</td>
                </tr>
              ))}</tbody>
            </table></div>
            </>
          ) : <Empty text="Nothing shared yet." />}
        </Card>
      </div>
    </div>
  );
}

function Stepper({ step }: { step: number }) {
  return (
    <ol className="flex flex-wrap items-center gap-1 text-[11px]" aria-label="Notify Party progress">
      {STEPS.map((label, i) => (
        <li key={label} className="flex min-w-0 items-center gap-1">
          <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${i < step ? "bg-match text-white" : i === step ? "bg-accent text-white" : "bg-ink-100 text-ink-500"}`}>{i < step ? "✓" : i + 1}</span>
          <span className={`min-w-0 ${i === step ? "font-semibold text-ink-900" : "text-ink-500"}`}>{label}</span>
          {i < STEPS.length - 1 && <span className="mx-1 h-px w-4 bg-ink-200" aria-hidden />}
        </li>
      ))}
    </ol>
  );
}

function StepHeading({ n, title, done }: { n: number; title: string; done: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${done ? "bg-match text-white" : "bg-accent-bg text-accent-fg"}`}>{done ? "✓" : n}</span>
      <h4 className="min-w-0 break-words text-sm font-semibold text-ink-800">{title}</h4>
    </div>
  );
}
