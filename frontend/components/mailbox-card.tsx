"use client";
import { useCallback, useEffect, useState } from "react";
import { disconnectMailbox, getMailbox, getSession, post, startGoogle, startMicrosoft, type Mailbox } from "@/lib/api";
import { Button, fmtDate } from "@/components/ui";

const PROVIDER_LABEL: Record<string, string> = { gmail: "Gmail", outlook: "Outlook" };

/**
 * The user's own mailbox on the desk: connected address, last poll, fetch, disconnect, or the
 * "Connect Outlook" / "Connect Gmail" calls to action. `compact` renders the one-line banner used on the Inbox.
 * Which buttons appear comes from `/me/mailbox.providers` (what this deployment has credentials for).
 */
export function MailboxCard({ compact = false, onFetched }: { compact?: boolean; onFetched?: (created: number) => void }) {
  const [mb, setMb] = useState<Mailbox | null>(null);
  const [busy, setBusy] = useState<"" | "fetch" | "disconnect" | "google" | "microsoft">("");
  const [note, setNote] = useState<string | null>(null);
  const me = getSession()?.user;
  const canIngest = me?.permissions.includes("ingest") ?? false;

  const load = useCallback(() => { getMailbox().then(setMb).catch(() => setMb({ connected: false })); }, []);
  useEffect(() => { load(); }, [load]);

  const say = (m: string) => { setNote(m); setTimeout(() => setNote(null), 6000); };
  const here = () => window.location.pathname || "/welcome";
  const connectGoogle = async () => {
    setBusy("google");
    try { await startGoogle({ next: here() }); }
    catch (x: any) { say(x.message || "Google sign-in is not configured."); setBusy(""); }
  };
  const connectMicrosoft = async () => {
    setBusy("microsoft");
    try { await startMicrosoft({ next: here(), intent: "connect" }); }
    catch (x: any) { say(x.message || "Microsoft sign-in is not configured."); setBusy(""); }
  };
  const reconnect = () => (mb?.provider === "outlook" ? connectMicrosoft() : connectGoogle());
  const fetchMine = async () => {
    setBusy("fetch");
    try {
      const r = await post<{ created: string[]; duplicates_skipped: number; mailbox: string }>("/connectors/poll?limit=10&source=mine");
      const n = r.created?.length || 0;
      say(n ? `Fetched ${n} new case(s) from ${r.mailbox} · skipped ${r.duplicates_skipped || 0} duplicate(s)` : `No new mail in ${r.mailbox} (${r.duplicates_skipped || 0} already ingested)`);
      onFetched?.(n); load();
    } catch (x: any) { say(x.message || "Fetch failed"); load(); }
    finally { setBusy(""); }
  };
  const disconnect = async () => {
    if (!window.confirm(`Disconnect ${mb?.address}? The desk keeps the cases already fetched but stops reading and sending from this mailbox.`)) return;
    setBusy("disconnect");
    try { const r = await disconnectMailbox(); say(`${r.address} disconnected${r.google_revoked ? " and revoked at Google" : ""}.${r.note ? ` ${r.note}` : ""}`); load(); }
    catch (x: any) { say(x.message || "Could not disconnect"); }
    finally { setBusy(""); }
  };

  if (mb === null) return compact ? null : <div className="h-20 animate-pulse rounded-2xl bg-ink-100" aria-busy />;
  const providers = mb.providers || { google: true, microsoft: false, shared_mailbox_configured: true };
  const anyProvider = providers.google || providers.microsoft;
  const label = PROVIDER_LABEL[mb.provider || ""] || "mailbox";
  const connectButtons = (
    <>
      {providers.microsoft && <Button kind="primary" disabled={!!busy} onClick={connectMicrosoft}>{busy === "microsoft" ? "Opening Microsoft…" : "Connect Outlook"}</Button>}
      {providers.google && <Button kind={providers.microsoft ? "ghost" : "primary"} disabled={!!busy} onClick={connectGoogle}>{busy === "google" ? "Opening Google…" : "Connect Gmail"}</Button>}
      {!anyProvider && <span className="text-[11px] text-ink-500">No mailbox provider is configured on this deployment (MICROSOFT_* or GOOGLE_OAUTH_* in .env).</span>}
    </>
  );

  if (compact) {
    if (mb.connected) return null;
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-dashed border-orange-200 bg-[#fffaf5] px-3 py-2 text-xs text-ink-700">
        <span className="font-semibold">Your mailbox is not connected.</span>
        <span className="text-ink-500">{providers.shared_mailbox_configured ? "Connect it and the desk reads your inbox and answers from your address." : "The desk only reads connected mailboxes: connect yours to fetch mail and reply from your address."}</span>
        <span className="ml-auto flex gap-2">{connectButtons}</span>
        {note && <span className="w-full text-[11px] text-mismatch-fg">{note}</span>}
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-orange-100 bg-[#fffdf9] p-5 shadow-sm">
      <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-accent">Your mailbox</div>
      {mb.connected ? (
        <>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-semibold text-ink-900">{mb.address}</span>
            <span className="rounded-full bg-ink-100 px-2 py-0.5 text-[10px] font-bold uppercase text-ink-700">{label}</span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${mb.status === "active" ? "bg-match-bg text-match-fg" : "bg-mismatch-bg text-mismatch-fg"}`}>{mb.status}</span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${mb.can_send ? "bg-ink-100 text-ink-700" : "bg-review-bg text-review-fg"}`}>{mb.can_send ? "read + send" : "read only"}</span>
          </div>
          <p className="mt-2 text-sm text-ink-600">
            Fetch pulls this {label} onto the desk and tags each case with it. Approved replies to those cases leave from this address
            {mb.can_send ? "" : " once send permission is granted (reconnect to add it)"}.
            {mb.last_error && <span className="block text-mismatch-fg">Last poll failed: {mb.last_error}. Reconnect if it keeps failing.</span>}
          </p>
          <div className="mt-2 text-[11px] text-ink-500">Connected {fmtDate(mb.connected_at || "")}{mb.last_polled_at ? ` · last fetched ${fmtDate(mb.last_polled_at)}` : " · not fetched yet"}</div>
          <div className="mt-3 flex flex-wrap gap-2">
            {canIngest && <Button kind="primary" disabled={!!busy} onClick={fetchMine}>{busy === "fetch" ? "Fetching…" : "Fetch my inbox"}</Button>}
            <Button kind="ghost" disabled={!!busy} onClick={reconnect}>Reconnect</Button>
            <Button kind="danger" disabled={!!busy} onClick={disconnect}>{busy === "disconnect" ? "Disconnecting…" : "Disconnect"}</Button>
          </div>
        </>
      ) : (
        <>
          <p className="mt-1 text-sm text-ink-700">
            No mailbox is connected to <span className="font-mono">{me?.email}</span>.
            {providers.microsoft ? " Connect Outlook (Microsoft asks once for read and send permission)" : ""}{providers.microsoft && providers.google ? " or" : ""}{providers.google ? " connect Gmail" : ""}
            {anyProvider ? ": the desk fetches its mail and approved replies leave from that address." : ""}
            {!providers.shared_mailbox_configured && anyProvider ? " Nothing is read until a mailbox is connected." : ""}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">{connectButtons}</div>
        </>
      )}
      {note && <div className="mt-3 rounded-lg bg-ink-50 px-3 py-1.5 text-xs text-ink-700">{note}</div>}
    </div>
  );
}
