"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { AUTH_PATHS, api, getSession, logout, redirectToLogin, type NewMailItem, type NotificationFeed, type NotificationItem } from "@/lib/api";
import { ROLE_LABELS } from "@/components/auth";

type Me = { id: string; email: string; display_name: string; roles: string[]; permissions: string[]; mailbox?: { connected: boolean; address?: string; status?: string; provider?: string } };

// `perm` hides the entry for roles the API would reject anyway (Audit: Supervisor/Admin/Auditor; Policies: Ops/Supervisor/Admin).
const NAV: { href: string; label: string; icon: string; perm?: string }[] = [
  { href: "/", label: "Inbox", icon: "inbox" },
  { href: "/workbench", label: "Workbench", icon: "bench" },
  { href: "/verification", label: "Seven fields", icon: "check" },
  { href: "/security", label: "Security", icon: "shield" },
  { href: "/history", label: "History", icon: "history" },
  { href: "/audit", label: "Audit", icon: "audit", perm: "view_audit" },
  { href: "/policies", label: "Policies", icon: "policy", perm: "view_policy" },
  { href: "/welcome", label: "Guide", icon: "guide" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const isAuthPage = AUTH_PATHS.some((p) => path.startsWith(p));
  const [me, setMe] = useState<Me | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [open, setOpen] = useState(false);
  const [ready, setReady] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    if (isAuthPage) return;
    const s = getSession();
    if (!s) { redirectToLogin(); return; }
    setSigningOut(false);   // Shell lives in the root layout, so state survives login -> logout -> login
    setMe(s.user);          // instant paint from the stored session, then confirm with the API
    setReady(true);
    api<Me>("/me").then(setMe).catch(() => {});
    api("/health").then(setHealth).catch(() => setHealth(null));
  }, [isAuthPage]);

  const signOut = async () => {
    if (signingOut) return;
    setSigningOut(true);
    await logout();                      // revokes server-side + clears localStorage (never throws)
    window.location.assign("/login");    // full reload: no stale Shell/page state survives the sign-out
  };

  if (isAuthPage) return <>{children}</>;
  if (!ready) return <div className="dashboard-surface flex min-h-screen items-center justify-center text-sm text-ink-500">Checking your session…</div>;

  const nav = NAV.filter((n) => !n.perm || !me || me.permissions.includes(n.perm));
  const active = (href: string) => (href === "/" ? path === "/" || path.startsWith("/cases") : path.startsWith(href));
  return (
    <div className="dashboard-surface flex min-h-screen flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2">Skip to content</a>
      <header className="sticky top-0 z-40 h-16 border-b border-ink-200/80 bg-white/90 backdrop-blur-xl lg:fixed lg:left-0 lg:top-0 lg:h-screen lg:w-64 lg:border-b-0 lg:border-r">
        <div className="mx-auto flex h-full max-w-[1600px] items-center gap-2 px-3 sm:gap-4 sm:px-4 lg:mx-0 lg:flex-col lg:items-stretch lg:gap-6 lg:px-5 lg:py-7">
          <Link href="/" className="flex shrink-0 items-center" aria-label="NovaShip Averis home">
            <img src="/novaship-logo-clean.png" alt="NovaShip" className="h-auto w-[104px] sm:w-[122px] lg:w-[132px]" />
          </Link>
          <nav className="hidden items-center gap-0.5 text-sm lg:flex lg:w-full lg:flex-col" aria-label="Primary">
            {nav.map((n) => (
              <div key={n.href} className="w-full">
                <Link href={n.href} aria-current={active(n.href) ? "page" : undefined} className={`dashboard-number flex items-center gap-3 whitespace-nowrap rounded-xl px-3 py-2.5 text-[15px] font-semibold transition duration-200 hover:translate-x-1 ${active(n.href) ? "bg-accent-bg text-accent-fg ring-1 ring-accent-ring/50" : "text-ink-700 hover:bg-[#fff0e5] hover:text-accent-fg"}`}><NavIcon name={n.icon} />{n.label}</Link>
              </div>
            ))}
          </nav>
          <button className="rounded-md border border-ink-200 px-2 py-1 text-xs text-ink-700 lg:hidden" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="mobile-nav">{open ? "Close" : "Menu"}</button>
          <div className="ml-auto flex items-center gap-2 text-xs sm:gap-3 lg:mt-auto lg:ml-0 lg:w-full lg:max-w-full lg:flex-col lg:items-stretch lg:rounded-2xl lg:border lg:border-[#eaded3] lg:bg-[#fbf7f2] lg:p-2.5 lg:hover:border-orange-300 lg:hover:bg-[#fffaf5] lg:transition lg:duration-200 lg:hover:-translate-y-0.5 lg:hover:shadow-sm">
            <NotificationBell />
            <span className={`hidden items-center gap-1.5 whitespace-nowrap xl:flex ${health ? "text-match" : "text-mismatch"}`} title={health ? `repository: ${health.backend}` : "backend unreachable"}>
              <span className={`h-2 w-2 rounded-full ${health ? "bg-match" : "bg-mismatch"}`} aria-hidden />{health ? `API online, ${health.cases} cases` : "API offline"}
            </span>
            <div className="flex min-w-0 items-center gap-2.5 lg:w-full">
              <span className="dashboard-number flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-bold text-white shadow-glow" aria-hidden>{initials(me?.display_name)}</span>
              <div className="hidden min-w-0 flex-1 sm:block">
                <div className="truncate text-[13px] font-semibold text-ink-900" title={me?.email}>{me?.display_name || "…"}</div>
                <div className="truncate text-[11px] text-ink-500" title={me?.email}>{(me?.roles || []).map((r) => ROLE_LABELS[r] || r).join(" · ") || "—"}</div>
                {me?.mailbox?.connected && <div className="truncate font-mono text-[10px] text-ink-500" title={`Connected ${me.mailbox.provider === "outlook" ? "Outlook" : "Gmail"} (${me.mailbox.status})`}>✉ {me.mailbox.address}</div>}
              </div>
              <SignOutButton onClick={signOut} busy={signingOut} className="inline-flex lg:hidden" />
            </div>
            <SignOutButton onClick={signOut} busy={signingOut} className="hidden w-full lg:inline-flex" />
          </div>
        </div>
        {open && (
          <nav id="mobile-nav" className="max-h-[calc(100vh-4rem)] overflow-y-auto border-t border-ink-200 bg-white px-4 py-2 shadow-lg lg:hidden" aria-label="Primary mobile">
            {nav.map((n) => <Link key={n.href} href={n.href} onClick={() => setOpen(false)} className={`block rounded-md px-2 py-2 text-sm ${active(n.href) ? "bg-accent-bg text-accent-fg" : "text-ink-700"}`}>{n.label}</Link>)}
          </nav>
        )}
      </header>
      <main id="main" className="mx-auto w-full min-w-0 max-w-[1600px] flex-1 px-3 py-5 sm:px-4 sm:py-6 lg:ml-64 lg:w-[calc(100%-16rem)] lg:max-w-none lg:px-8 lg:py-8">{children}</main>
      <footer className="border-t border-ink-200/80 bg-white/60 px-4 py-3 text-center text-[11px] text-ink-500 lg:ml-64 lg:w-[calc(100%-16rem)]">SI is the source of truth. Seven fields compared deterministically. AI proposes, humans approve. Every action audited.</footer>
    </div>
  );
}

function NotificationBell() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [mail, setMail] = useState<NewMailItem[]>([]);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<Set<string>>(new Set());
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [prevMailIds, setPrevMailIds] = useState<Set<string> | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem("novaship.seenNotifications");
      if (raw) setSeen(new Set(JSON.parse(raw) as string[]));
      const rawClosed = localStorage.getItem("novaship.closedNewMail");
      if (rawClosed) setClosed(new Set(JSON.parse(rawClosed) as string[]));
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    const load = () => {
      api<NotificationFeed>("/me/notifications").then((d) => {
        setItems(d.items || []);
        const incoming = d.new_mail || [];
        setMail(incoming);
        // a mail that was not in the previous poll -> short toast, so the arrival is visible even with the bell closed
        setPrevMailIds((prev) => {
          if (prev) {
            const fresh = incoming.filter((m) => !prev.has(m.case_id));
            if (fresh.length) { setFlash(`${fresh.length} new mail in ${fresh[0].mailbox || "your mailbox"}: ${fresh[0].subject}`); window.setTimeout(() => setFlash(null), 8000); }
          }
          return new Set(incoming.map((m) => m.case_id));
        });
      }).catch(() => {});
    };
    load();
    const t = window.setInterval(load, 30000);
    return () => window.clearInterval(t);
  }, []);

  const keyOf = (n: NotificationItem) => `${n.case_id}:${n.updated_at}`;
  const visibleMail = mail.filter((m) => !closed.has(m.case_id));
  const unseen = items.filter((n) => !seen.has(keyOf(n))).length + visibleMail.length;

  const persistSeen = (next: Set<string>) => {
    setSeen(next);
    try { localStorage.setItem("novaship.seenNotifications", JSON.stringify([...next])); } catch { /* ignore */ }
  };
  const closeMail = (ids: string[]) => {
    const next = new Set([...closed, ...ids]);
    setClosed(next);
    try { localStorage.setItem("novaship.closedNewMail", JSON.stringify([...next].slice(-500))); } catch { /* ignore */ }
  };

  const toggle = () => {
    setOpen((was) => {
      if (!was) persistSeen(new Set([...seen, ...items.map(keyOf)]));
      return !was;
    });
  };
  const when = (iso: string) => { try { return new Date(iso).toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" }); } catch { return iso; } };

  return (
    <div className="relative lg:w-full">
      <button type="button" onClick={toggle} aria-expanded={open} aria-label={unseen ? `${unseen} notifications` : "Notifications"} className="inline-flex items-center gap-1.5 rounded-lg border border-ink-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-ink-700 transition hover:border-accent hover:text-accent-fg lg:w-full lg:justify-center">
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M6 8a6 6 0 1 1 12 0c0 7 3 7 3 9H3s3-2 3-9" /><path d="M10 21a2 2 0 0 0 4 0" /></svg>
        <span className="hidden sm:inline">Alerts</span>
        {unseen > 0 && <span className="inline-flex min-w-[1.1rem] items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">{unseen > 9 ? "9+" : unseen}</span>}
      </button>
      {flash && !open && (
        <div role="status" className="fixed bottom-4 right-4 z-50 max-w-[22rem] rounded-xl border border-orange-200 bg-white px-3 py-2 text-xs text-ink-800 shadow-lg">
          <span className="font-semibold text-accent">New mail</span> · {flash}
          <button type="button" onClick={() => setFlash(null)} className="ml-2 text-ink-400 hover:text-ink-800" aria-label="Dismiss">×</button>
        </div>
      )}
      {open && (
        <div className="absolute right-0 z-50 mt-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-ink-200 bg-white shadow-lg lg:bottom-full lg:right-auto lg:left-0 lg:mb-2 lg:mt-0 lg:w-full">
          <div className="flex items-center justify-between border-b border-ink-100 px-3 py-2 text-[11px] font-semibold text-ink-800">
            <span>New mail{visibleMail.length ? ` (${visibleMail.length})` : ""}</span>
            {visibleMail.length > 0 && (
              <span className="flex gap-2">
                <Link href={`/cases/${visibleMail[0].case_id}`} onClick={() => { closeMail([visibleMail[0].case_id]); setOpen(false); }} className="font-semibold text-accent-fg hover:underline">Open latest</Link>
                <button type="button" onClick={() => closeMail(visibleMail.map((m) => m.case_id))} className="text-ink-500 hover:text-ink-800">Clear all</button>
              </span>
            )}
          </div>
          {visibleMail.length === 0 ? <p className="px-3 py-3 text-center text-[11px] text-ink-500">No new mail in your mailbox.</p> : (
            <ul className="max-h-56 overflow-y-auto">
              {visibleMail.map((m) => (
                <li key={m.case_id} className="flex items-stretch border-b border-ink-50">
                  <Link href={`/cases/${m.case_id}`} onClick={() => { closeMail([m.case_id]); setOpen(false); }} className="min-w-0 flex-1 px-3 py-2 hover:bg-accent-bg">
                    <div className="truncate text-xs font-semibold text-ink-900">{m.subject}</div>
                    <div className="truncate text-[10px] text-ink-500">{m.sender} · {when(m.received_at)}</div>
                    <div className="text-[10px] text-ink-500">{m.status.replace(/_/g, " ")}{m.action_required ? " · needs a person" : ""}</div>
                  </Link>
                  <button type="button" onClick={() => closeMail([m.case_id])} className="px-2 text-ink-400 hover:bg-ink-50 hover:text-ink-800" aria-label="Close notification" title="Close">×</button>
                </li>
              ))}
            </ul>
          )}
          <div className="border-b border-t border-ink-100 px-3 py-2 text-[11px] font-semibold text-ink-800">Needs a person</div>
          {items.length === 0 ? <p className="px-3 py-6 text-center text-xs text-ink-500">Queue is clear.</p> : (
            <ul className="max-h-64 overflow-y-auto">
              {items.map((n) => (
                <li key={keyOf(n)}>
                  <Link href={`/cases/${n.case_id}`} onClick={() => setOpen(false)} className="block border-b border-ink-50 px-3 py-2 hover:bg-accent-bg">
                    <div className="text-[10px] font-bold uppercase tracking-wide text-accent">{n.reason}</div>
                    <div className="truncate text-xs font-semibold text-ink-900">{n.subject || n.case_id}</div>
                    <div className="text-[10px] text-ink-500">{n.status.replace(/_/g, " ")} · {n.priority}</div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function SignOutButton({ onClick, busy, className = "" }: { onClick: () => void; busy: boolean; className?: string }) {
  return (
    <button type="button" onClick={onClick} disabled={busy} aria-busy={busy} className={`items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-[11px] font-semibold text-ink-700 transition hover:border-accent hover:text-accent-fg disabled:cursor-wait disabled:opacity-60 ${className}`}>
      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M10 17l5-5-5-5M15 12H3M13 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6" /></svg>
      {busy ? "Signing out…" : "Sign out"}
    </button>
  );
}

function initials(name?: string | null) {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  return parts.length ? (parts[0][0] + (parts[parts.length - 1][0] || "")).toUpperCase() : "?";
}

function NavIcon({ name }: { name: string }) {
  const paths: Record<string, React.ReactNode> = {
    inbox: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 9h5l2 3h4l2-3h5" /></>,
    bench: <><rect x="3" y="14" width="18" height="6" rx="1" /><path d="M6 14V8h4v6M14 14V6h4v8" /></>,
    check: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="m8 12 2.5 2.5L16 9" /></>,
    shield: <path d="M12 3 19 6v5c0 4.7-3 7.9-7 10-4-2.1-7-5.3-7-10V6l7-3Z" />,
    spark: <path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9L12 2Zm7 14 .8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z" />,
    audit: <><path d="M6 3h9l3 3v15H6z" /><path d="M9 11h6M9 15h6M9 19h4" /></>,
    history: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /><path d="M3.5 12H6" /></>,
    policy: <><path d="M12 3 4 6v5c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V6l-8-3Z" /><path d="m9 12 2 2 4-4" /></>,
    guide: <><circle cx="12" cy="12" r="8" /><path d="M12 10v5M12 7h.01" /></>,
  };
  return <svg viewBox="0 0 24 24" className="h-5 w-5 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{paths[name]}</svg>;
}
