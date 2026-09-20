"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { adoptSessionFromFragment } from "@/lib/api";
import { AuthError, AuthLayout } from "@/components/auth";

/**
 * Landing page for GET /auth/google/callback and /auth/microsoft/callback. The API puts the session
 * (or, for a mailbox connect, just the connected address) in the URL fragment; never in a query
 * string or server log. This page stores it and continues to `next`.
 */
export default function OAuthCallbackPage() {
  const router = useRouter();
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState("Finishing sign-in…");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await adoptSessionFromFragment(window.location.hash);
        if (!result) { setErr("No sign-in token was returned. Start again from the login page."); return; }
        if (cancelled) return;
        if (result.connectedOnly) setMsg(`${result.mailbox} connected. Opening the desk…`);
        else if (!result.mailbox) setMsg(result.isNew ? "Account created. Next: connect your mailbox…" : "Signed in. Opening the desk…");
        else setMsg(result.isNew ? `Account created and ${result.mailbox} connected. Opening the desk…` : `Signed in. ${result.mailbox} is connected. Opening the desk…`);
        history.replaceState(null, "", "/auth/callback");
        router.replace(result.next.startsWith("/") ? result.next : "/welcome");
      } catch (x: any) {
        if (!cancelled) setErr(x?.message || "Could not complete the sign-in.");
      }
    })();
    return () => { cancelled = true; };
  }, [router]);

  return (
    <AuthLayout title="Signing you in" subtitle={err ? "" : msg}
      footer={<span>Trouble signing in? <Link href="/login" className="font-semibold text-accent-fg hover:underline">Back to sign in</Link></span>}>
      <AuthError msg={err} />
      {!err && <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-100"><div className="h-full w-1/2 animate-pulse rounded-full bg-accent" /></div>}
    </AuthLayout>
  );
}
