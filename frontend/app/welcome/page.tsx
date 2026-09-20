import Link from "next/link";
import { MailboxCard } from "@/components/mailbox-card";

/**
 * Guide page for first-time users and demo judges.
 * Design read: trust-first B2B guide page for shipping operators. Variance 5, motion 3, density 4.
 * Single accent (orange), 12px radius, no decorative eyebrows, split hero, four different section layouts.
 */
export const metadata = { title: "Guide, NovaShip Averis" };

const STEPS = [
  { title: "Using your own email", body: "Sign up with Google and your Gmail is connected in the same consent. You join this shared desk — the 520 test cases stay in the Inbox. Fetch Inbox pulls your mailbox next to the seeded cases, each tagged with its address, and approved replies leave from it.", href: "/register", cta: "Create an account" },
  { title: "Open the Inbox", body: "Every email is already a case. Filter Needs human or Mismatch = yes to see what needs a decision today.", href: "/", cta: "Open inbox" },
  { title: "Read the seven fields", body: "On a case, the comparison card shows Shipper, Consignee, Notify Party, Port of Loading, Port of Discharge, Container Count and Gross Weight side by side with the Shipping Instruction as truth.", href: "/cases/case_email_004", cta: "See a mismatch case" },
  { title: "Check the evidence", body: "Each value links to the exact line in the document and shows which label was resolved, for example Load Port to Port of Loading.", href: "/cases/case_email_004?tab=evidence", cta: "View evidence" },
  { title: "Approve, edit or reject the draft", body: "The correction request is written for you but never sent. A Supervisor approves; Operations staff can edit and share internally.", href: "/cases/case_email_004?tab=drafts", cta: "Open drafts" },
  { title: "Notify the right party", body: "Pick an approved recipient, preview exactly the fields that will be disclosed, confirm. The extracted Notify Party is a value to compare, not permission to send.", href: "/cases/case_email_004?tab=collab", cta: "Try Notify Party" },
  { title: "Ask the assistant", body: "Questions are answered only from this case, the documents, the comparison and the policy knowledge base, with citations.", href: "/cases/case_email_004?tab=ask", cta: "Ask AI" },
];

const ROLES = [
  ["Operations staff", "Najiha (hanna_azhari@aprilasia.com), Deswita, Willy, Mitchelle", "view, compare, edit drafts, share internally, assign"],
  ["Supervisor", "Hari (hari_mardianto@aprilasia.com), Teo Ei Leen", "everything above plus approve external sends and notify external parties"],
  ["Admin", "Syed Faraz Ali (faraz_ali@aprilasia.com), or your own email via /register", "everything plus edit policy and rebuild the knowledge index"],
  ["Auditor", "Ooi Sok Yong (sokyong_ooi@aprilasia.com)", "read-only, including the global audit log"],
];

export default function WelcomePage() {
  return (
    <div className="space-y-10 pb-8">
      <section className="grid items-center gap-8 pt-6 md:grid-cols-[1.1fr_1fr]">
        <div>
          <Link href="/" className="inline-flex items-center gap-2 text-sm font-bold text-accent-fg transition hover:-translate-x-1 hover:text-accent">← <span>Back to inbox</span></Link>
          <h1 className="dashboard-number mt-6 text-4xl font-bold tracking-[-.04em] text-[#583521] sm:text-5xl md:text-6xl">Every email becomes a case. Every verdict shows its evidence.</h1>
          <p className="mt-4 max-w-3xl text-base font-semibold leading-relaxed text-[#7d6251] sm:text-lg">NovaShip Averis reads the shared shipping inbox, checks each Draft Bill of Lading against its Shipping Instruction on seven fields, and asks a person before anything leaves the mailbox.</p>
          <div className="mt-5 flex flex-wrap gap-2">
            <Link href="/" className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-white transition hover:bg-accent-hover active:scale-[0.98]">Open the inbox</Link>
            <Link href="/agent" className="rounded-xl border border-ink-200 bg-white px-4 py-2 text-sm font-medium text-ink-800 transition hover:bg-ink-50 active:scale-[0.98]">See how the agent works</Link>
          </div>
        </div>
        <div className="rounded-2xl border border-orange-100 bg-[#fffdf9] p-5 shadow-sm transition duration-200 hover:-translate-y-1 hover:shadow-md">
          <div className="text-sm font-semibold text-ink-800">What a finished check looks like</div>
          <div className="mt-3 space-y-2 text-sm">
            {[["Shipper", "match"], ["Consignee", "match"], ["Notify Party", "match"], ["Port of Loading", "match"], ["Port of Discharge", "match"], ["Container Count", "mismatch"], ["Gross Weight (kg)", "match"]].map(([f, r]) => (
              <div key={f} className={`flex items-center justify-between rounded-lg px-3 py-1.5 ${r === "mismatch" ? "bg-mismatch-bg text-mismatch-fg" : "bg-ink-50 text-ink-700"}`}>
                <span>{f}</span><span className="font-mono text-xs">{r === "mismatch" ? "SI 3 x 40'HC, BL 4 x 40'HC" : "match"}</span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-ink-500">One field differs, so only one field is flagged. When all seven match the result reads: No mismatch detected.</p>
        </div>
      </section>

      <MailboxCard />

      <section>
        <h2 className="text-xl font-semibold text-ink-900">How to use the desk</h2>
        <ol className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {STEPS.map((s, i) => (
            <li key={s.title} className={`flex min-h-[190px] flex-col rounded-2xl border border-orange-100 p-4 shadow-card transition duration-200 hover:-translate-y-1 hover:scale-[1.015] hover:border-orange-200 hover:shadow-glow ${i % 3 === 0 ? "bg-[radial-gradient(circle_at_85%_85%,rgba(230,104,19,.28),transparent_46%),linear-gradient(135deg,#fffdfb_15%,#f9eee6)]" : i % 3 === 1 ? "bg-[radial-gradient(circle_at_82%_16%,rgba(247,139,54,.25),transparent_47%),linear-gradient(135deg,#fffdfb_15%,#fff2e5)]" : "bg-[radial-gradient(circle_at_80%_85%,rgba(255,154,58,.34),transparent_47%),linear-gradient(135deg,#fffdfb_15%,#fff4e7)]"}`}>
              <div className="text-xs text-ink-500">Step {i + 1}</div>
              <div className="mt-1 font-semibold text-ink-900">{s.title}</div>
              <p className="mt-1 flex-1 text-sm leading-relaxed text-ink-600">{s.body}</p>
              <Link href={s.href} className="mt-3 text-sm font-medium text-accent hover:underline">{s.cta}</Link>
            </li>
          ))}
        </ol>
      </section>

      <section className="rounded-2xl border border-orange-200 bg-orange-50 p-6 shadow-sm">
        <h2 className="text-xl font-semibold text-[#6c432c]">How the AI is kept honest</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          <div className="rounded-xl border border-orange-200 bg-white/80 p-4 text-[#573929] transition duration-200 hover:-translate-y-1 hover:border-orange-300 hover:bg-white hover:shadow-md"><div className="font-semibold">AI reads and drafts</div><p className="mt-1 text-sm leading-relaxed text-[#765b4a]">Security agent, intent, document type, field extraction, summary, reply draft, translation and the case assistant.</p></div>
          <div className="rounded-xl border border-orange-200 bg-white/80 p-4 text-[#573929] transition duration-200 hover:-translate-y-1 hover:border-orange-300 hover:bg-white hover:shadow-md"><div className="font-semibold">Code decides</div><p className="mt-1 text-sm leading-relaxed text-[#765b4a]">The seven-field comparison is a deterministic function with unit tests. A model can call it as a tool but can never overrule it.</p></div>
          <div className="rounded-xl border border-orange-200 bg-white/80 p-4 text-[#573929] transition duration-200 hover:-translate-y-1 hover:border-orange-300 hover:bg-white hover:shadow-md"><div className="font-semibold">People approve</div><p className="mt-1 text-sm leading-relaxed text-[#765b4a]">The LangGraph pauses at human review. External email and Notify Party messages need a Supervisor. Everything is written to an append-only audit log.</p></div>
        </div>
      </section>

      <section>
        <h2 className="text-2xl font-bold text-accent">Who can do what</h2>
        <p className="mt-1 max-w-[65ch] text-sm text-ink-600">Register with your own email as Admin to manage this shared project, or sign in as a demo account (password <span className="font-mono">novaship123</span>). Permissions are enforced by the API, not just hidden in the UI.</p>
        <div className="mt-4 overflow-x-auto rounded-2xl border border-orange-100 bg-white shadow-sm">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-ink-50 text-left text-xs uppercase tracking-wide text-ink-500"><tr><th className="px-4 py-2">Role</th><th className="px-4 py-2">Demo users</th><th className="px-4 py-2">Can</th></tr></thead>
            <tbody>{ROLES.map(([r, u, c]) => <tr key={r} className="border-t border-ink-100"><td className="px-4 py-2 font-medium text-ink-900">{r}</td><td className="px-4 py-2 text-ink-600">{u}</td><td className="px-4 py-2 text-ink-600">{c}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border border-orange-100 bg-[#fffdf9] p-5 shadow-sm transition duration-200 hover:-translate-y-1 hover:shadow-md">
          <h2 className="text-lg font-semibold text-ink-900">Pages</h2>
          <ul className="mt-2 space-y-1.5 text-sm text-ink-700">
            <li><Link className="text-accent hover:underline" href="/">Inbox</Link>: metrics, case table, filters, batch actions.</li>
            <li><Link className="text-accent hover:underline" href="/verification">Seven fields</Link>: per-field mismatch statistics and every case per field.</li>
            <li><Link className="text-accent hover:underline" href="/security">Security</Link>: what the security agent flagged and why.</li>
            <li><Link className="text-accent hover:underline" href="/agent">AI agent</Link>: run the LangGraph on a case, see the pause, resume with a decision.</li>
            <li><Link className="text-accent hover:underline" href="/audit">Audit</Link>: global append-only history.</li>
            <li><Link className="text-accent hover:underline" href="/policies">Policies</Link>: versioned thresholds and rules (Admin edits).</li>
          </ul>
        </div>
        <div className="rounded-2xl border border-orange-100 bg-[#fffdf9] p-5 shadow-sm transition duration-200 hover:-translate-y-1 hover:shadow-md">
          <h2 className="text-lg font-semibold text-ink-900">Try these cases</h2>
          <ul className="mt-2 space-y-1.5 text-sm text-ink-700">
            <li><Link className="font-mono text-accent hover:underline" href="/cases/case_email_004">case_email_004</Link>: Consignee and Notify Party differ.</li>
            <li><Link className="font-mono text-accent hover:underline" href="/cases/case_email_001">case_email_001</Link>: all seven match.</li>
            <li><Link className="font-mono text-accent hover:underline" href="/cases/case_email_499">case_email_499</Link>: only Gross Weight differs.</li>
            <li><Link className="font-mono text-accent hover:underline" href="/cases/case_email_507">case_email_507</Link>: Draft BL missing, upload it to continue.</li>
            <li><Link className="font-mono text-accent hover:underline" href="/cases/case_email_512">case_email_512</Link>: scanned PDF, human review with reason.</li>
            <li><Link className="font-mono text-accent hover:underline" href="/cases/case_email_015">case_email_015</Link>: spam, no reply needed.</li>
          </ul>
        </div>
      </section>
    </div>
  );
}
