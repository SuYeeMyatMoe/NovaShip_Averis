"use client";
import Link from "next/link";
import { useState } from "react";

/**
 * Shared chrome for /login and /register: brand panel on the left, form card on the right.
 * Same palette as the dashboard (one orange accent, warm stone neutrals, white surfaces).
 */
export function AuthLayout({ title, subtitle, subtitleClassName = "", heroMessage, children, footer }: { title: string; subtitle: string; subtitleClassName?: string; heroMessage?: React.ReactNode; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <div className="dashboard-surface relative flex min-h-screen flex-col overflow-hidden">
      <img src="/domain-logo.jpe" alt="" aria-hidden className="pointer-events-none absolute right-4 top-4 h-28 w-28 rounded-3xl object-cover opacity-[0.14] mix-blend-multiply md:right-12 md:top-8 md:h-40 md:w-40" />
      <div className="relative z-10 mx-auto grid w-full max-w-6xl flex-1 items-center gap-10 px-4 py-10 md:grid-cols-[1.05fr_1fr] md:px-8">
        <section className="hidden md:block">
          <Link href="/login" className="inline-flex items-center" aria-label="NovaShip Averis">
            <img src="/novaship-logo-clean.png" alt="NovaShip" className="h-auto w-[150px]" />
          </Link>
          <h1 className="dashboard-number mt-8 text-5xl font-bold leading-[1.02] tracking-[-.04em] text-[#583521] xl:text-6xl">Every email becomes a case. Every verdict shows its evidence.</h1>
          {heroMessage ?? <p className="mt-5 max-w-xl text-lg font-semibold leading-relaxed text-[#7d6251]">Seven-field SI ↔ Draft BL verification, decided deterministically. AI drafts, people approve, every action is audited.</p>}
          <div className="mt-8 rounded-2xl border border-orange-100 bg-[#fffdf9] p-5 shadow-card">
            <div className="text-sm font-semibold text-ink-800">What a finished check looks like</div>
            <div className="mt-3 space-y-1.5 text-sm">
              {[["Shipper", "match"], ["Consignee", "match"], ["Notify Party", "match"], ["Port of Loading", "match"], ["Port of Discharge", "match"], ["Container Count", "mismatch"], ["Gross Weight (kg)", "match"]].map(([f, r]) => (
                <div key={f} className={`flex items-center justify-between rounded-lg px-3 py-1.5 ${r === "mismatch" ? "bg-mismatch-bg text-mismatch-fg" : "bg-ink-50 text-ink-700"}`}>
                  <span>{f}</span><span className="font-mono text-xs">{r === "mismatch" ? "SI 3 x 40'HC · BL 4 x 40'HC" : "match"}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="w-full">
          <div className="mb-6 flex items-center md:hidden">
            <img src="/novaship-logo-clean.png" alt="NovaShip" className="h-auto w-[122px]" />
          </div>
          <div className="rounded-2xl border border-orange-100 bg-[radial-gradient(circle,rgba(236,122,42,.18)_1px,transparent_1.2px)] bg-[size:14px_14px] p-6 shadow-card transition duration-200 hover:-translate-y-1 hover:border-orange-300 hover:shadow-lg sm:p-8">
            <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-accent">NovaShip Averis</div>
            <h2 className="dashboard-number mt-1 text-3xl font-bold tracking-[-.03em] text-ink-900">{title}</h2>
            {subtitle && <p className={`mt-1.5 text-sm ${subtitleClassName || "text-ink-500"}`}>{subtitle}</p>}
            <div className="mt-6">{children}</div>
          </div>
          {footer && <div className="mt-4 text-center text-sm text-ink-600">{footer}</div>}
        </section>
      </div>
      <footer className="border-t border-ink-200/80 bg-white/60 px-4 py-3 text-center text-[11px] text-ink-500">SI is the source of truth. Seven fields compared deterministically. AI proposes, humans approve. Every action audited.</footer>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-ink-700">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-[11px] text-ink-500">{hint}</span>}
    </label>
  );
}

export const inputClass = "w-full rounded-xl border border-ink-200 bg-white px-3 py-2.5 text-sm text-ink-900 placeholder:text-ink-400 transition focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-ring/60 disabled:bg-ink-50";

export function PasswordInput({ value, onChange, placeholder = "••••••••", autoComplete = "current-password", minLength }: { value: string; onChange: (v: string) => void; placeholder?: string; autoComplete?: string; minLength?: number }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input type={show ? "text" : "password"} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} autoComplete={autoComplete} minLength={minLength} required className={`${inputClass} pr-16`} />
      <button type="button" onClick={() => setShow(!show)} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md px-2 py-1 text-[11px] font-semibold text-ink-500 hover:bg-ink-100 hover:text-ink-800" aria-label={show ? "Hide password" : "Show password"}>{show ? "Hide" : "Show"}</button>
    </div>
  );
}

/** "Continue with Google": one consent creates or opens the account and connects that Gmail to the desk (Microsoft is two consents: login, then Connect Outlook). */
export function GoogleButton({ onClick, busy, label = "Continue with Google" }: { onClick: () => void; busy?: boolean; label?: string }) {
  return (
    <button type="button" onClick={onClick} disabled={busy} className="flex w-full items-center justify-center gap-2.5 rounded-xl border border-ink-200 bg-white px-4 py-2.5 text-sm font-semibold text-ink-800 transition hover:border-accent hover:text-accent-fg active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60">
      <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden>
        <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.3l7.9 6.1C12.4 13.4 17.7 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h12.7c-.6 3-2.3 5.5-4.8 7.2l7.6 5.9c4.5-4.1 7-10.2 7-17.1z" />
        <path fill="#FBBC05" d="M10.5 28.6A14.5 14.5 0 0 1 9.7 24c0-1.6.3-3.2.8-4.6l-7.9-6.1A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.7l7.9-6.1z" />
        <path fill="#34A853" d="M24 48c6.3 0 11.7-2.1 15.5-5.7l-7.6-5.9c-2.1 1.4-4.8 2.3-7.9 2.3-6.3 0-11.6-3.9-13.5-9.4l-7.9 6.1C6.5 42.6 14.6 48 24 48z" />
      </svg>
      {busy ? "Opening Google…" : label}
    </button>
  );
}

/** "Continue with Microsoft": identity only; the Outlook mailbox is connected afterwards in a separate consent. */
export function MicrosoftButton({ onClick, busy, label = "Continue with Microsoft" }: { onClick: () => void; busy?: boolean; label?: string }) {
  return (
    <button type="button" onClick={onClick} disabled={busy} className="flex w-full items-center justify-center gap-2.5 rounded-xl border border-ink-200 bg-white px-4 py-2.5 text-sm font-semibold text-ink-800 transition hover:border-accent hover:text-accent-fg active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60">
      <svg width="18" height="18" viewBox="0 0 21 21" aria-hidden>
        <rect x="1" y="1" width="9" height="9" fill="#F25022" /><rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
        <rect x="1" y="11" width="9" height="9" fill="#00A4EF" /><rect x="11" y="11" width="9" height="9" fill="#FFB900" />
      </svg>
      {busy ? "Opening Microsoft…" : label}
    </button>
  );
}

export function OrDivider({ text = "or" }: { text?: string }) {
  return (
    <div className="my-4 flex items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-400" aria-hidden>
      <span className="h-px flex-1 bg-ink-200" />{text}<span className="h-px flex-1 bg-ink-200" />
    </div>
  );
}

export function AuthError({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return <div role="alert" className="rounded-xl border border-mismatch/40 bg-mismatch-bg px-3 py-2 text-sm text-mismatch-fg">{msg}</div>;
}

export function SubmitButton({ busy, children }: { busy: boolean; children: React.ReactNode }) {
  return (
    <button type="submit" disabled={busy} className="w-full rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-white shadow-glow transition hover:bg-accent-hover active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60">
      {busy ? "Please wait…" : children}
    </button>
  );
}

export const ROLE_LABELS: Record<string, string> = { OPERATIONS_STAFF: "Operations staff", SUPERVISOR: "Supervisor", ADMIN: "Admin", AUDITOR: "Auditor" };
