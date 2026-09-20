"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getSession, register } from "@/lib/api";
import { AuthError, AuthLayout, Field, PasswordInput, ROLE_LABELS, SubmitButton, inputClass } from "@/components/auth";

type AuthConfig = { register_roles: string[]; min_password_length: number };

const ROLE_HINTS: Record<string, string> = {
  OPERATIONS_STAFF: "View, compare, edit drafts, share internally and assign. Supervisor, Admin and Auditor roles are granted by an Admin.",
  SUPERVISOR: "Approve external sends and notify parties. Policy edits stay with Admins.",
  ADMIN: "Full desk access, including policy edits.",
  AUDITOR: "Read-only access to cases and the audit trail.",
};

function roleHint(role: string, roles: string[]) {
  if (role === "ADMIN") return "Demo: you join the live shipping desk with full access. The current test cases stay here — this is not a blank workspace.";
  if (role && ROLE_HINTS[role]) return ROLE_HINTS[role];
  if (roles.length > 1) return "Pick the desk role this account should start with. Admin can manage the shared inbox, approve sends and edit policy.";
  return ROLE_HINTS.OPERATIONS_STAFF;
}

function subtitle(roles: string[]) {
  if (roles.includes("ADMIN")) return "You join the live shipping desk. The current cases stay here. Admin self-register is for this shared demo only.";
  return "Self-registered accounts start with least privilege. An Admin can widen the role later.";
}

export default function RegisterPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [role, setRole] = useState("");
  const [cfg, setCfg] = useState<AuthConfig>({ register_roles: ["OPERATIONS_STAFF"], min_password_length: 8 });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (getSession()) { router.replace("/"); return; }
    api<AuthConfig>("/auth/config", {}, { auth: false }).then((c) => { setCfg(c); setRole(c.register_roles[0] || ""); }).catch(() => {});
  }, [router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null);
    if (password !== confirm) return setErr("Passwords do not match.");
    if (password.length < cfg.min_password_length) return setErr(`Password must be at least ${cfg.min_password_length} characters.`);
    setBusy(true);
    try { await register({ email: email.trim(), password, display_name: name.trim(), role: role || undefined }); router.replace("/welcome"); }
    catch (x: any) { setErr(x.message || "Could not create the account."); }
    finally { setBusy(false); }
  };

  return (
    <AuthLayout title="Create an account" subtitle={subtitle(cfg.register_roles)}
      footer={<span>Already have an account? <Link href="/login" className="font-semibold text-accent-fg hover:underline">Sign in</Link></span>}>
      <form onSubmit={submit} className="space-y-4">
        <AuthError msg={err} />
        <Field label="Full name">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Hari Mardianto" autoComplete="name" required minLength={2} autoFocus className={inputClass} />
        </Field>
        <Field label="Work email">
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@aprilasia.com" autoComplete="email" required className={inputClass} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Password" hint={`At least ${cfg.min_password_length} characters.`}>
            <PasswordInput value={password} onChange={setPassword} autoComplete="new-password" minLength={cfg.min_password_length} />
          </Field>
          <Field label="Confirm password">
            <PasswordInput value={confirm} onChange={setConfirm} autoComplete="new-password" minLength={cfg.min_password_length} />
          </Field>
        </div>
        <Field label="Role" hint={roleHint(role, cfg.register_roles)}>
          <div className="relative">
            <select name="role" value={role} required disabled={!cfg.register_roles.length} onChange={(e) => setRole(e.target.value)} className={`${inputClass} cursor-pointer appearance-none pr-10`}>
              {cfg.register_roles.length !== 1 && <option value="" disabled>Select a role</option>}
              {cfg.register_roles.map((r) => <option key={r} value={r}>{ROLE_LABELS[r] || r.replace(/_/g, " ")}</option>)}
            </select>
            <svg className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-500" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
              <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.17l3.71-3.94a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clipRule="evenodd" />
            </svg>
          </div>
        </Field>
        <SubmitButton busy={busy}>Create account</SubmitButton>
      </form>
    </AuthLayout>
  );
}
