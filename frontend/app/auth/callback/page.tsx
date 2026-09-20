"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { adoptSessionFromFragment } from "@/lib/api";
import { AuthError, AuthLayout } from "@/components/auth";

/**
 * Landing page for GET /auth/google/callback. The API puts the session in the URL fragment
 * (never in a query string or server log); this page stores it and continues to `next`.
 */
export default function GoogleCallbackPage() {
  const router = useRouter();
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState("Finishing Google sign-in…");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await adoptSessionFromFragment(window.location.hash);
        if (!result) { setErr("No sign-in token was returned. Start again from the login page."); return; }
        if (cancelled) return;
        setMsg(result.isNew ? `Account created and ${result.mailbox} connected. Opening the desk…` : `Signed in. ${result.mailbox} is connected. Opening the desk…`);
        history.replaceState(null, "", "/auth/callback");
        router.replace(result.next.startsWith("/") ? result.next : "/welcome");
      } catch (x: any) {
        if (!cancelled) setErr(x?.message || "Could not complete the sign-in.");
      }
    })();
    return () => { cancelled = true; };
  }, [router]);

  return (
    <AuthLayout title="Google sign-in" subtitle={err ? "" : msg}
      footer={<span>Trouble signing in? <Link href="/login" className="font-semibold text-accent-fg hover:underline">Back to sign in</Link></span>}>
      <AuthError msg={err} />
      {!err && <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-100"><div className="h-full w-1/2 animate-pulse rounded-full bg-accent" /></div>}
    </AuthLayout>
  );
}
