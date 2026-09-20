"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { AUTH_PATHS, api, getSession, logout, redirectToLogin } from "@/lib/api";
import { ROLE_LABELS } from "@/components/auth";

type Me = { id: string; email: string; display_name: string; roles: string[]; permissions: string[] };

// `perm` hides the entry for roles the API would reject anyway (Audit: Supervisor/Admin/Auditor; Policies: Ops/Supervisor/Admin).
const NAV: { href: string; label: string; icon: string; perm?: string }[] = [
  { href: "/", label: "Inbox", icon: "inbox" },
  { href: "/verification", label: "Seven fields", icon: "check" },
  { href: "/security", label: "Security", icon: "shield" },
  { href: "/agent", label: "AI agent", icon: "spark" },
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
          <div className="ml-auto flex items-center gap-3 text-xs transition duration-200 hover:-translate-y-0.5 hover:shadow-sm lg:mt-auto lg:ml-0 lg:w-full lg:max-w-full lg:flex-col lg:items-stretch lg:rounded-2xl lg:border lg:border-[#eaded3] lg:bg-[#fbf7f2] lg:p-2.5 lg:hover:border-orange-300 lg:hover:bg-[#fffaf5]">
            <span className={`hidden items-center gap-1.5 whitespace-nowrap xl:flex ${health ? "text-match" : "text-mismatch"}`} title={health ? `repository: ${health.backend}` : "backend unreachable"}>
              <span className={`h-2 w-2 rounded-full ${health ? "bg-match" : "bg-mismatch"}`} aria-hidden />{health ? `API online, ${health.cases} cases` : "API offline"}
            </span>
            <div className="flex min-w-0 items-center gap-2.5 lg:w-full">
              <span className="dashboard-number flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-bold text-white shadow-glow" aria-hidden>{initials(me?.display_name)}</span>
              <div className="hidden min-w-0 flex-1 sm:block">
                <div className="truncate text-[13px] font-semibold text-ink-900" title={me?.email}>{me?.display_name || "…"}</div>
                <div className="truncate text-[11px] text-ink-500" title={me?.email}>{(me?.roles || []).map((r) => ROLE_LABELS[r] || r).join(" · ") || "—"}</div>
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
    check: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="m8 12 2.5 2.5L16 9" /></>,
    shield: <path d="M12 3 19 6v5c0 4.7-3 7.9-7 10-4-2.1-7-5.3-7-10V6l7-3Z" />,
    spark: <path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9L12 2Zm7 14 .8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z" />,
    audit: <><path d="M6 3h9l3 3v15H6z" /><path d="M9 11h6M9 15h6M9 19h4" /></>,
    policy: <><path d="M12 3 4 6v5c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V6l-8-3Z" /><path d="m9 12 2 2 4-4" /></>,
    guide: <><circle cx="12" cy="12" r="8" /><path d="M12 10v5M12 7h.01" /></>,
  };
  return <svg viewBox="0 0 24 24" className="h-5 w-5 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{paths[name]}</svg>;
}
