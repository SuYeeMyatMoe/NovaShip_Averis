"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { GOOGLE_ERRORS, api, getSession, login, startGoogle, startMicrosoft } from "@/lib/api";
import { AuthError, AuthLayout, Field, GoogleButton, MicrosoftButton, OrDivider, PasswordInput, ROLE_LABELS, SubmitButton, inputClass } from "@/components/auth";

type DemoAccount = { email: string; display_name: string; roles: string[] };
type AuthConfig = { auth_mode: string; demo_password?: string; demo_accounts?: DemoAccount[]; google_enabled?: boolean; microsoft_enabled?: boolean };

export default function LoginPage() {
  return <Suspense fallback={null}><LoginForm /></Suspense>;
}

function LoginForm() {
  const router = useRouter();
  const sp = useSearchParams();
  const next = sp.get("next") || "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [showDemo, setShowDemo] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);
  const [msBusy, setMsBusy] = useState(false);

  useEffect(() => {
    if (getSession()) { router.replace(next); return; }
    const code = sp.get("error");
    if (code) setErr(GOOGLE_ERRORS[code] || `Sign-in failed (${code}).`);
    api<AuthConfig>("/auth/config", {}, { auth: false }).then(setCfg).catch(() => setCfg(null));
  }, [router, next, sp]);

  const google = async () => {
    setErr(null); setGoogleBusy(true);
    try { await startGoogle({ next: next.startsWith("/") ? next : "/" }); }
    catch (x: any) { setErr(x.message || "Google sign-in is not configured."); setGoogleBusy(false); }
  };

  const microsoft = async () => {
    setErr(null); setMsBusy(true);
    try { await startMicrosoft({ next: next.startsWith("/") ? next : "/" }); }
    catch (x: any) { setErr(x.message || "Microsoft sign-in is not configured."); setMsBusy(false); }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null); setBusy(true);
    try { await login(email.trim(), password); router.replace(next.startsWith("/") ? next : "/"); }
    catch (x: any) { setErr(x.status === 401 ? "Invalid email or password." : x.message || "Could not sign in."); }
    finally { setBusy(false); }
  };
  const fill = (a: DemoAccount) => { setEmail(a.email); setPassword(cfg?.demo_password || ""); setErr(null); };

  return (
    <AuthLayout title="Sign in" subtitle="" heroMessage={<p className="mt-5 text-3xl font-bold tracking-[-.02em] text-accent sm:text-4xl">Welcome</p>}
      footer={<span>New to the desk? <Link href="/register" className="font-semibold text-accent-fg hover:underline">Create an account</Link></span>}>
      <AuthError msg={err} />
      {(cfg?.microsoft_enabled || cfg?.google_enabled) && (
        <div className="mt-3 space-y-2">
          {cfg?.microsoft_enabled && <MicrosoftButton onClick={microsoft} busy={msBusy} />}
          {cfg?.google_enabled && <GoogleButton onClick={google} busy={googleBusy} />}
          <p className="text-[11px] text-ink-500">
            {cfg?.microsoft_enabled ? "Microsoft signs you in; you connect your Outlook mailbox afterwards. " : ""}
            {cfg?.google_enabled ? "Google signs you in and connects that Gmail in one step." : ""}
          </p>
          <OrDivider text="or with a password" />
        </div>
      )}
      <form onSubmit={submit} className="space-y-4">
        <Field label="Email">
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@aprilasia.com" autoComplete="email" required autoFocus className={inputClass} />
        </Field>
        <Field label="Password">
          <PasswordInput value={password} onChange={setPassword} />
        </Field>
        <SubmitButton busy={busy}>Sign in</SubmitButton>
      </form>

      {cfg?.demo_accounts?.length ? (
        <div className="mt-6 rounded-xl border border-dashed border-orange-200 bg-[#fffaf5] p-3">
          <button type="button" onClick={() => setShowDemo(!showDemo)} className="flex w-full items-center justify-between text-left text-xs font-semibold text-ink-700" aria-expanded={showDemo}>
            <span>Demo accounts <span className="font-normal text-ink-500">· password <span className="font-mono">{cfg.demo_password}</span></span></span>
            <span className="text-accent-fg">{showDemo ? "Hide" : "Show"}</span>
          </button>
          {showDemo && (
            <ul className="mt-2 divide-y divide-orange-100">
              {cfg.demo_accounts.map((a) => (
                <li key={a.email} className="flex items-center gap-2 py-1.5 text-xs">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-semibold text-ink-800">{a.display_name} <span className="font-normal text-ink-500">· {a.roles.map((r) => ROLE_LABELS[r] || r).join(" / ")}</span></div>
                    <div className="truncate font-mono text-[11px] text-ink-500">{a.email}</div>
                  </div>
                  <button type="button" onClick={() => fill(a)} className="rounded-lg border border-ink-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-ink-800 transition hover:border-accent hover:text-accent-fg">Use</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </AuthLayout>
  );
}
