"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { agentStateOf, api, post, getSession, ApiError, FIELD_LABELS, type CaseRow, type Metrics } from "@/lib/api";
import { Badge, Button, Confidence, PRIORITY_COLORS, StatusBadge, Toast, fmtDate } from "@/components/ui";
import { CaseColHandle, caseColStyle as caseColBox, useCaseColWidth } from "@/components/col-resize";
import { MailboxCard } from "@/components/mailbox-card";
import { Field, inputClass } from "@/components/auth";
import { useOperatorWarning } from "@/lib/operator-warning";

const STATUSES = ["RECEIVED","SECURITY_REVIEW","CLASSIFIED","NO_ACTION_INFO","WAITING_DOCUMENTS","NO_MISMATCH_DETECTED","MISMATCH_DETECTED","HUMAN_REVIEW","DRAFT_READY","NOTIFY_PARTY","AWAITING_RESPONSE","ASSIGNED","COMPLETED","ERROR"];
const INTENTS = ["DOCUMENT_VERIFICATION","DOCUMENT_CORRECTION","PREPARE_SHIPPING_INSTRUCTION","INVOICE_QUERY","OPERATIONAL_UPDATE","GENERAL_ENQUIRY","INFORMATION_ONLY","NO_ACTION_REQUIRED","UNKNOWN_REVIEW"];
const EMPTY_FILTERS = { status: "", priority: "", intent: "", mismatch: "", assigned: "", shared: "", sender: "", q: "", min_confidence: "", security: "", sort: "updated_desc", date_from: "", date_to: "", attention: "", mailbox: "", agent: "pending" };
const AGENT_LABELS: Record<string, string> = { pending: "Not run yet", paused: "Paused – waiting for you", done: "Processed", any: "All (incl. processed)" };
const STATUS_ORDER = ["RECEIVED","SECURITY_CHECK","SECURITY_REVIEW","CLASSIFIED","NO_ACTION_INFO","DOCUMENTS_DETECTED","WAITING_DOCUMENTS","EXTRACTING","COMPARING","NO_MISMATCH_DETECTED","MISMATCH_DETECTED","HUMAN_REVIEW","DRAFT_READY","NOTIFY_PARTY","AWAITING_RESPONSE","ASSIGNED","COMPLETED","ERROR"];
const SORT_LABELS: Record<string, string> = { updated_desc: "Last update", received_desc: "Received", priority: "Priority", confidence: "Confidence, low first", run_desc: "Last agent run" };
const COLUMN_KEYS = ["case","subject","intent","security","action","priority","docs","mismatch","confidence","assigned","shared","status","run","updated","actions"] as const;
type ColKey = (typeof COLUMN_KEYS)[number];
const DEFAULT_COLS: ColKey[] = ["case","subject","action","priority","docs","mismatch","status","updated","actions"];
const COLS_KEY = "novaship.inbox.columns";
const CASE_COL_KEY = "novaship.inbox.caseColWidth";
const PANEL_FILTER_KEYS = ["status","priority","intent","mailbox","mismatch","security","assigned","shared","sender","min_confidence","date_from","date_to"];
const TOOLBAR_BTN = "inline-flex items-center gap-1.5 rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs font-semibold text-ink-800 transition hover:bg-ink-50 aria-expanded:border-accent aria-expanded:text-accent-fg";
const KEBAB_BTN = "rounded-lg border border-ink-200 bg-white px-2 py-1.5 text-sm font-bold leading-none text-ink-700 transition hover:bg-ink-50 aria-expanded:border-accent aria-expanded:text-accent-fg";
const STATUS_TONE: Record<string, string> = { HUMAN_REVIEW: "bg-review", MISMATCH_DETECTED: "bg-mismatch", SECURITY_REVIEW: "bg-mismatch", ERROR: "bg-mismatch", WAITING_DOCUMENTS: "bg-review", NO_MISMATCH_DETECTED: "bg-match", COMPLETED: "bg-match", NO_ACTION_INFO: "bg-ink-300" };

export default function Dashboard() {
  const router = useRouter();
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [fields, setFields] = useState<any[]>([]);
  const [attention, setAttention] = useState<CaseRow[] | null>(null);
  const [security, setSecurity] = useState<any[] | null>(null);
  const [activity, setActivity] = useState<any[] | null>(null);
  const [rows, setRows] = useState<CaseRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [users, setUsers] = useState<any[]>([]);
  const [f, setF] = useState<Record<string, string>>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [apiDown, setApiDown] = useState(false);
  const [fetching, setFetching] = useState(false);
  const limit = 5;
  const me = getSession()?.user;
  const canIngest = me?.permissions.includes("ingest") ?? false;
  const myMailbox = me?.mailbox?.connected ? me.mailbox.address : null;
  const sharedMailbox = me?.mailbox?.providers?.shared_mailbox_configured ?? true;
  const warn = useOperatorWarning();
  const [filtersOpen, setFiltersOpen] = useState(false);
  const closeFilters = useCallback(() => setFiltersOpen(false), []);
  const [visibleCols, setVisibleCols] = useState<ColKey[]>(DEFAULT_COLS);
  const [caseColWidth, setCaseColWidth] = useCaseColWidth(CASE_COL_KEY);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(COLS_KEY);
      if (!raw) return;
      const arr: unknown = JSON.parse(raw);
      if (!Array.isArray(arr)) return;
      const ok = arr.filter((k): k is ColKey => (COLUMN_KEYS as readonly string[]).includes(k));
      if (ok.length) setVisibleCols(ok);
    } catch { /* ignore */ }
  }, []);
  const persistCols = (next: ColKey[]) => { try { localStorage.setItem(COLS_KEY, JSON.stringify(next)); } catch { /* ignore */ } return next; };
  const setCols = (next: ColKey[]) => setVisibleCols(persistCols(next));
  const toggleCol = (k: ColKey) => setVisibleCols((prev) => { const next = prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]; return next.length ? persistCols(next) : prev; });
  const activeFilters = PANEL_FILTER_KEYS.filter((k) => f[k]).length + (f.agent !== "pending" ? 1 : 0);

  const say = (msg: string, kind: "ok" | "err" = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 3500); };

  const load = useCallback(() => {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(page * limit) });
    Object.entries(f).forEach(([k, v]) => v && qs.set(k, v));
    api<{ total: number; items: CaseRow[] }>(`/cases?${qs}`).then((d) => { setRows(d.items); setTotal(d.total); setApiDown(false); }).catch((e) => { setRows([]); setApiDown(!(e instanceof ApiError)); say(e.message, "err"); });
  }, [f, page]);

  const loadWidgets = useCallback(() => {
    api<{ metrics: Metrics; fields: any[]; attention: CaseRow[]; security: any[]; activity: any[] | null; users: any[] }>("/dashboard/bootstrap")
      .then((d) => {
        setMetrics(d.metrics);
        setFields(d.fields || []);
        setAttention(d.attention || []);
        setSecurity(d.security || []);
        setActivity(d.activity);
        setUsers(d.users || []);
      })
      .catch(() => {
        api<Metrics>("/dashboard/metrics").then(setMetrics).catch(() => {});
      });
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadWidgets(); }, [loadWidgets]);

  const applyPreset = (patch: Record<string, string>) => { setF({ ...EMPTY_FILTERS, ...patch }); setPage(0); document.getElementById("case-table")?.scrollIntoView({ behavior: "smooth", block: "start" }); };
  const setFilter = (k: string, v: string) => { setF((p) => ({ ...p, [k]: v })); setPage(0); };
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const batch = async (action: string, params: any = {}, confirm = false) => {
    if (!sel.size) return say("Select cases first", "err");
    setBusy(true);
    try {
      const r = await post("/cases/batch", { action, case_ids: [...sel], params, confirm });
      if (r.requires_confirmation) { if (window.confirm(`${r.note}\n\nProceed with '${action}' on ${r.count} cases?`)) return batch(action, params, true); return; }
      if (action === "export" && r.csv) { const blob = new Blob([r.csv], { type: "text/csv" }); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "cases.csv"; a.click(); }
      if (r.xlsx_base64) {
        const bin = atob(r.xlsx_base64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
        a.download = r.filename || "cases.xlsx";
        a.click();
      }
      const ok = Object.values(r.results as Record<string, any>).filter((x: any) => x.ok).length;
      say(`${action}: ${ok}/${sel.size} succeeded`); setSel(new Set()); load(); loadWidgets();
      warn.notice(r);
    } catch (e: any) { if (!warn.notice(e)) say(e.message, "err"); } finally { setBusy(false); }
  };
  const [running, setRunning] = useState<string | null>(null);
  const runAgent = async (id: string) => {
    setRunning(id);
    try {
      const st = await post<{ paused: boolean; status?: string; interrupt?: { summary?: string } }>(`/agent/run/${id}`);
      if (st.paused) { say(`Agent paused on ${id.replace("case_", "")}: it needs your decision`, "ok"); router.push(`/cases/${id}?tab=agent`); }
      else say(`Agent finished ${id.replace("case_", "")} (${(st.status || "").replace(/_/g, " ")}) — now in Processed`);
      load(); loadWidgets();
    } catch (e: any) { if (!warn.notice(e)) say(e.message, "err"); }
    finally { setRunning(null); }
  };
  const quick = async (id: string, path: string, body?: any) => { try { const r = await post(`/cases/${id}${path}`, body); if (!warn.notice(r)) say("Done"); load(); loadWidgets(); } catch (e: any) { if (!warn.notice(e)) say(e.message, "err"); } };

  const fetchInbox = async () => {
    setFetching(true);
    try {
      const r = await post<{ created: string[]; duplicates_skipped: number; connector: string; mailbox?: string }>("/connectors/poll?limit=10&source=auto");
      const n = r.created?.length || 0;
      const where = r.mailbox && r.mailbox !== "shared" ? r.mailbox : `the shared ${r.connector || ""} mailbox`;
      say(n ? `Fetched ${n} new case(s) from ${where} · skipped ${r.duplicates_skipped || 0} duplicate(s)` : `No new mail in ${where} (${r.duplicates_skipped || 0} already ingested)`);
      load();
      loadWidgets();
    } catch (e: any) { say(e.message, "err"); }
    finally { setFetching(false); }
  };

  const m = metrics;
  const verified = m ? (m.mismatches_detected || 0) + (m.no_mismatch_cases || 0) : 0;
  const mismatchRate = verified ? Math.round(((m?.mismatches_detected || 0) / verified) * 100) : 0;
  type Kpi = { label: string; value: number; tone: string; hint: string; preset: Record<string, string> };
  const kpis = useMemo<Kpi[]>(() => m ? ([
    { label: "Action required", value: m.action_required, tone: "accent", hint: "open cases that need a person", preset: { sort: "priority" } },
    { label: "Mismatches", value: m.mismatches_detected, tone: "mismatch", hint: `${mismatchRate}% of verified pairs`, preset: { mismatch: "yes" } },
    { label: "Human review", value: m.human_review, tone: "review", hint: "waiting for a Human decision", preset: { status: "HUMAN_REVIEW" } },
  ] as Kpi[]) : [], [m, mismatchRate]);

  const statusRows = useMemo(() => {
    if (!m?.by_status) return [];
    const max = Math.max(1, ...Object.values(m.by_status as Record<string, number>));
    return STATUS_ORDER.filter((s) => m.by_status[s]).map((s) => ({ s, n: m.by_status[s] as number, pct: (m.by_status[s] / max) * 100 }));
  }, [m]);
  const intentRows = useMemo(() => {
    if (!m?.by_intent) return [];
    const total = Object.values(m.by_intent as Record<string, number>).reduce((a, b) => a + b, 0) || 1;
    return Object.entries(m.by_intent as Record<string, number>).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ k, v, pct: (v / total) * 100 }));
  }, [m]);
  const fieldMax = Math.max(1, ...fields.map((x) => x.mismatch));
  const secCounts: Record<string, number> = (security || []).reduce((acc: Record<string, number>, r) => ({ ...acc, [r.outcome]: (acc[r.outcome] || 0) + 1 }), {} as Record<string, number>);

  type Col = { key: ColKey; label: string; title?: string; td?: string | ((r: CaseRow) => string); render: (r: CaseRow) => React.ReactNode };
  const columns: Col[] = [
    { key: "case", label: "Case", td: "overflow-hidden font-mono text-[11px]", render: (r) => <Link href={`/cases/${r.id}`} title={r.id} className="block truncate text-accent hover:underline">{r.id.replace("case_", "")}</Link> },
    { key: "subject", label: "Subject / sender", td: "max-w-[360px]", render: (r) => (
      <>
        <Link href={`/cases/${r.id}`} className="line-clamp-1 font-medium text-ink-900 hover:text-accent">{r.subject || "(no subject)"}</Link>
        <div className="truncate text-[11px] text-ink-500">{r.sender}{r.mailbox && <span className="ml-1.5 rounded-full bg-orange-50 px-1.5 py-px text-[10px] font-semibold text-accent-fg" title={`Fetched from ${r.mailbox}`}>{r.mailbox_user_id === me?.id ? "my mailbox" : r.mailbox}</span>}</div>
      </>
    ) },
    { key: "intent", label: "Intent", render: (r) => <Badge className="bg-ink-100 text-ink-700">{r.intent.replace(/_/g, " ")}</Badge> },
    { key: "security", label: "Security", render: (r) => <Badge className={r.security === "SAFE" ? "bg-match-bg text-match-fg" : "bg-mismatch-bg text-mismatch-fg"}>{r.security}</Badge> },
    { key: "action", label: "Action", render: (r) => r.action_required ? <span className="font-semibold text-accent">Required</span> : <span className="text-ink-400">No reply</span> },
    { key: "priority", label: "Priority", td: (r) => `font-semibold ${PRIORITY_COLORS[r.priority] || ""}`, render: (r) => r.priority },
    { key: "docs", label: "SI / BL", td: "font-mono text-[11px]", render: (r) => <><Dot ok={r.si_available} label="SI" /> <Dot ok={r.bl_available} label="BL" /></> },
    { key: "mismatch", label: "Mismatch", render: (r) => (
      <>
        {r.comparison_status === null ? <span className="text-ink-400">—</span> : r.mismatch_count > 0 ? <Badge className="bg-mismatch-bg text-mismatch-fg">{r.mismatch_count} mismatch</Badge> : r.comparison_status === "PASSED" ? <Badge className="bg-match-bg text-match-fg">No mismatch</Badge> : <Badge className="bg-review-bg text-review-fg">Review</Badge>}
        {r.review_reason && <div className="mt-0.5 text-[10px] text-review-fg">{r.review_reason.replace(/_/g, " ")}</div>}
      </>
    ) },
    { key: "confidence", label: "Confidence", render: (r) => <Confidence value={r.confidence} /> },
    { key: "assigned", label: "Assigned", td: "text-[11px]", render: (r) => users.find((u) => u.id === r.assigned_user_id)?.display_name || <span className="text-ink-400">-</span> },
    { key: "shared", label: "Shared", td: "text-[11px]", render: (r) => r.shared_with.length ? `${r.shared_with.length} recipient(s)` : <span className="text-ink-400">-</span> },
    { key: "status", label: "Status", render: (r) => <><StatusBadge status={r.status} />{r.errors > 0 && <div className="mt-0.5 text-[10px] text-mismatch">{r.errors} error(s)</div>}</> },
    { key: "run", label: "Run", title: "Last AI-agent run", td: "whitespace-nowrap text-[11px]", render: (r) => <RunCell r={r} /> },
    { key: "updated", label: "Updated", td: "whitespace-nowrap text-[11px] text-ink-500", render: (r) => fmtDate(r.updated_at) },
    { key: "actions", label: "Actions", td: "whitespace-nowrap", render: (r) => (
      <div className="flex items-center gap-1">
        <Button kind="primary" onClick={() => router.push(`/cases/${r.id}`)}>Open</Button>
        <Menu label={`More actions for ${r.id.replace("case_", "")}`} width={200} triggerClassName={KEBAB_BTN} trigger={<span aria-hidden>⋯</span>}>
          {agentStateOf(r) !== "done" && <MenuItem disabled={running === r.id} onClick={() => runAgent(r.id)}>{running === r.id ? "Running…" : agentStateOf(r) === "paused" ? "Resume agent" : "Run agent"}</MenuItem>}
          <MenuItem onClick={() => quick(r.id, "/compare")}>Compare documents</MenuItem>
          <MenuItem onClick={() => router.push(`/cases/${r.id}?tab=ask`)}>Ask AI</MenuItem>
          <MenuItem onClick={() => router.push(`/cases/${r.id}?tab=collab`)}>Share</MenuItem>
          <MenuItem onClick={() => router.push(`/cases/${r.id}?tab=drafts`)}>Draft</MenuItem>
          <MenuItem onClick={() => router.push(`/cases/${r.id}?tab=audit`)}>Audit</MenuItem>
        </Menu>
      </div>
    ) },
  ];
  const visible = columns.filter((c) => visibleCols.includes(c.key));
  const pages = Math.max(1, Math.ceil(total / limit));
  const caseColStyle = caseColBox(caseColWidth);
  const caseLabels = (rows || []).map((r) => r.id.replace("case_", ""));

  return (
    <div className="dashboard-type space-y-3">
      {toast && <Toast {...toast} />}
      {warn.dialog}
      <div className="relative overflow-hidden pb-4 pt-2"><img src="/domain-logo.jpe" alt="" aria-hidden className="pointer-events-none absolute right-3 top-0 hidden h-44 w-44 rounded-[2.5rem] object-cover opacity-[0.08] mix-blend-multiply sm:block sm:right-12 sm:h-56 sm:w-56" /><div className="relative z-10 text-xs font-bold uppercase tracking-[.18em] text-accent-fg">Dashboard</div><h1 className="hero-dashboard-number relative z-10 mt-2 max-w-5xl text-4xl font-semibold leading-[.98] tracking-[-.055em] text-[#4b2818] sm:text-6xl lg:text-7xl">Shipping operations,<br /><span className="bg-gradient-to-r from-[#e85f0b] via-[#f5832d] to-[#c97532] bg-clip-text text-transparent">under human command.</span></h1><p className="relative z-10 mt-4 text-base text-[#927968] sm:mt-5 sm:text-lg">Your case control center is ready.</p></div>

      <section className="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-[repeat(3,minmax(0,1fr))_minmax(0,1.3fr)]" aria-label="Key metrics">
        {(kpis.length ? kpis : Array.from({ length: 3 }, () => null)).map((k, i) => k ? (
          <button key={k.label} onClick={() => applyPreset(k.preset)} className={`group min-h-[112px] rounded-2xl border border-orange-100 p-3 text-left shadow-card transition duration-200 hover:-translate-y-1 hover:scale-[1.015] hover:shadow-glow active:scale-[0.99] sm:min-h-[122px] sm:p-3.5 ${i === 0 ? "bg-[radial-gradient(circle_at_85%_85%,rgba(230,104,19,.28),transparent_46%),linear-gradient(135deg,#fffdfb_15%,#f9eee6)]" : i === 1 ? "bg-[radial-gradient(circle_at_82%_16%,rgba(247,139,54,.25),transparent_47%),linear-gradient(135deg,#fffdfb_15%,#fff4e7)]" : "bg-[radial-gradient(circle_at_80%_85%,rgba(255,154,58,.34),transparent_47%),linear-gradient(135deg,#fffdfb_15%,#fff2e5)]"}`}>
            <div className="text-sm font-bold uppercase tracking-[.08em] text-accent">{k.label}</div>
            <div className="mt-2 flex items-end justify-between gap-2"><div className="dashboard-number text-4xl font-semibold tabular-nums leading-none text-[#4b2818]">{k.value}</div>{i < 2 && <Trend direction={i === 0 ? "up" : "down"} />}</div>
            <div className="mt-2 text-xs text-ink-600">{k.label === "Human review" ? <>waiting for a <span className="font-bold text-accent">Human</span> decision</> : k.hint}</div>
          </button>
        ) : <div key={i} className="h-[122px] animate-pulse rounded-2xl bg-ink-100" aria-busy />)}
        <section className="min-h-[112px] rounded-2xl border border-orange-100 bg-[radial-gradient(circle_at_82%_16%,rgba(247,139,54,.25),transparent_47%),linear-gradient(135deg,#fffdfb_15%,#fff2e5)] p-3 shadow-card transition duration-200 hover:-translate-y-1 hover:scale-[1.015] hover:shadow-glow sm:min-h-[122px] sm:p-3.5">
          <div className="text-sm font-bold uppercase tracking-[.08em] text-accent">All email status</div>
          <div className="mt-2 text-lg font-semibold tracking-tight text-[#4b2818]">{m ? <>{m.incoming_emails} emails, {m.action_required} need a <span className="font-bold text-accent">Human</span></> : apiDown ? "API offline" : "Loading"}</div>
          <div className="mt-3 flex gap-3"><Stat label="Verified" value={verified} /><Stat label="Pipeline" value={m ? `${m.avg_processing_ms} ms` : ""} /><Stat label="Done" value={m?.completed ?? 0} /></div>
        </section>
      </section>

      {/* ---- analytics row ---------------------------------------------------- */}
      <section className="grid gap-3 lg:grid-cols-2">
        <Panel title="Where cases are in the workflow" link={{ href: "", label: "" }}>
          {statusRows.length === 0 ? <Skeleton n={7} /> : (
            <ul className="space-y-1.5">
              {statusRows.map((r) => (
                <li key={r.s}>
                  <button onClick={() => applyPreset({ status: r.s })} className="group flex min-w-0 w-full items-center gap-1.5 text-[10px] sm:gap-2 sm:text-xs">
                    <span className="w-24 shrink-0 truncate text-left text-ink-700 transition group-hover:text-accent sm:w-36">{r.s.replace(/[_-]+/g, " ").toLowerCase()}</span>
                    <span className="h-2.5 flex-1 overflow-hidden rounded bg-ink-100"><span className={`block h-full rounded bg-gradient-to-r from-[#f68b3b] to-[#a77a72] ${STATUS_TONE[r.s] === "bg-mismatch" ? "!from-[#cb5a4f] !to-[#8e4039]" : ""}`} style={{ width: `${r.pct}%` }} /></span>
                    <span className="w-8 text-right font-mono font-semibold text-ink-800">{r.n}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Needs your attention now" link={{ href: "", label: "" }}>
          {attention === null ? <Skeleton n={3} /> : attention.length === 0 ? <div className="py-8 text-center text-sm text-ink-500">Queue is clear.</div> : (
            <ul className="space-y-2">
              {attention.slice(0, 3).map((r) => <li key={r.id} className="group grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-xl border-l-4 border-mismatch bg-white/80 px-3 py-2 shadow-sm transition hover:-translate-y-0.5 hover:bg-[#fff4ed]">
                <div className="min-w-0 flex-1"><div className="text-[10px] font-bold text-mismatch">{r.priority}</div><Link href={`/cases/${r.id}`} className="block truncate text-xs font-semibold text-ink-900 group-hover:text-accent">{r.subject || "No subject"}</Link></div>
                <div className="flex shrink-0 items-center gap-1">{r.mismatch_count > 0 && <Badge className="hidden bg-mismatch text-white sm:inline-flex">{r.mismatch_count} mismatch</Badge>}<Button kind="primary" onClick={() => router.push(`/cases/${r.id}`)}>Open</Button></div>
              </li>)}
            </ul>
          )}
        </Panel>
      </section>

      {activity && (
        <section className="grid gap-3 lg:grid-cols-[1.2fr_.8fr]">
          <div className="contents">
          <Panel className="lg:col-start-2 lg:row-start-1" title="Security agent" link={{ href: "/security", label: "Open queue" }}>
            {security === null ? <Skeleton n={2} /> : (
              <div className="flex items-center gap-2.5">
                <Ring value={m ? m.security_flagged : 0} total={m ? m.incoming_emails : 1} />
                <ul className="flex-1 space-y-1 text-[11px]">
                  {[["SECURITY_REVIEW", "bg-mismatch"], ["SUSPICIOUS", "bg-review"], ["SPAM", "bg-ink-400"]].map(([k, c]) => (
                    <li key={k} className="flex items-center gap-1.5"><span className={`h-1.5 w-1.5 rounded-full ${c}`} aria-hidden /><span className="flex-1 text-ink-600">{k.replace(/[_-]+/g, " ").toLowerCase()}</span><span className="font-mono font-semibold text-ink-800">{secCounts[k] || 0}</span></li>
                  ))}
                </ul>
              </div>
            )}
          </Panel>
          <Panel className="lg:col-start-1 lg:row-span-2" title="Latest activity" link={{ href: "/audit", label: "Full audit" }}>
            <ul className="space-y-1.5 text-[11px]">
              {activity.map((e) => (
                <li key={e.event_id} className="flex gap-2">
                  <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${e.actor_type === "USER" ? "bg-accent" : e.actor_type === "AI" ? "bg-review" : "bg-ink-300"}`} aria-hidden />
                  <div className="min-w-0 flex-1"><span className="font-medium text-ink-800">{e.action.replace(/_/g, " ").toLowerCase()}</span>{e.case_id && <Link href={`/cases/${e.case_id}`} className="ml-1 font-mono text-accent hover:underline">{e.case_id.replace("case_", "")}</Link>}<div className="text-ink-400">{e.actor_type.toLowerCase()} {e.actor_id}, {fmtDate(e.timestamp)}</div></div>
                </li>
              ))}
            </ul>
            <ActivityTrend events={activity} />
          </Panel>
          <Panel className="lg:col-start-2 lg:row-start-2" title="Seven field checks" link={{ href: "/verification", label: "View all" }}>
            {fields.length === 0 ? <Skeleton n={4} /> : <ul className="grid gap-x-5 gap-y-1.5 sm:grid-cols-2">{fields.map((x, i) => <li key={x.field}><Link href="/verification" className="group flex items-center gap-2 text-xs"><span className="w-4 text-ink-400">{i + 1}</span><span className="min-w-0 flex-1 truncate text-ink-700 group-hover:text-accent">{FIELD_LABELS[x.field]}</span><span className="h-1.5 w-16 overflow-hidden rounded-full bg-ink-100"><span className="block h-full rounded-full bg-gradient-to-r from-[#f58a38] to-[#9d6b5d]" style={{ width: `${(x.mismatch / fieldMax) * 100}%` }} /></span><span className="font-mono text-ink-800">{x.mismatch}</span></Link></li>)}</ul>}
          </Panel>
          </div>
        </section>
      )}

      {/* ---- case table --------------------------------------------------------- */}
      {canIngest && <MailboxCard compact />}
      <div id="case-table" className="scroll-mt-16 overflow-hidden rounded-2xl border border-ink-200 bg-white shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-3 px-4 pb-3 pt-4">
          <div>
            <h2 className="text-base font-semibold text-ink-900">Work queue</h2>
            <p className="mt-0.5 text-xs text-ink-500">{total} cases · page {page + 1} of {pages}</p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {canIngest && (myMailbox || sharedMailbox) && <Button kind="primary" disabled={fetching} onClick={fetchInbox} title={myMailbox ? `Polls ${myMailbox}` : "Polls the shared desk mailbox"}>{fetching ? "Fetching…" : myMailbox ? "Fetch my inbox" : "Fetch Inbox"}</Button>}
            {canIngest && !myMailbox && !sharedMailbox && <Button kind="primary" onClick={() => router.push("/welcome")} title="No mailbox is connected yet">Connect a mailbox</Button>}
            <Button kind={f.attention === "yes" ? "primary" : "default"} onClick={() => applyPreset(f.attention === "yes" ? { agent: f.agent } : { attention: "yes", sort: "priority", agent: f.agent })}>Needs human</Button>
            {me?.id && <Button kind={f.assigned === me.id ? "primary" : "default"} onClick={() => applyPreset(f.assigned === me.id ? { agent: f.agent } : { assigned: me.id, sort: "updated_desc", agent: f.agent })}>Needs me</Button>}
            <button type="button" onClick={() => setFiltersOpen(true)} aria-haspopup="dialog" aria-expanded={filtersOpen} className={TOOLBAR_BTN}>
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M3 5h18l-7 8v6l-4-2v-4z" /></svg>
              Filters
              {activeFilters > 0 && <span className="inline-flex min-w-[1.1rem] items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">{activeFilters}</span>}
            </button>
            <Menu label="Columns" width={240} triggerClassName={TOOLBAR_BTN} trigger={<>
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16M15 4v16" /></svg>
              Columns
              <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M6 9l6 6 6-6" /></svg>
            </>}>
              <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wide text-ink-500">Visible columns</div>
              <MenuItem keepOpen onClick={() => setCols([...COLUMN_KEYS])}>All columns</MenuItem>
              <div className="my-1 border-t border-ink-100" role="separator" />
              {columns.map((c) => <MenuItem key={c.key} keepOpen checked={visibleCols.includes(c.key)} onClick={() => toggleCol(c.key)}>{c.label}</MenuItem>)}
            </Menu>
          </div>
        </div>
        <div className="flex flex-col gap-2 border-t border-ink-100 px-4 py-2.5 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <svg viewBox="0 0 24 24" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
            <input placeholder="Search case, subject, sender or summary" value={f.q} onChange={(e) => setFilter("q", e.target.value)} className="w-full rounded-xl border border-ink-200 bg-white py-2 pl-9 pr-3 text-sm text-ink-900 placeholder:text-ink-400 transition focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-ring/60" aria-label="Search" />
          </div>
          <label className="flex items-center gap-2 text-xs text-ink-500">
            <span className="hidden sm:inline">Sort</span>
            <select value={f.sort} onChange={(e) => setFilter("sort", e.target.value)} aria-label="Sort" className="rounded-lg border border-ink-200 bg-white px-2 py-1.5 text-sm text-ink-800">
              {Object.entries(SORT_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </label>
        </div>

        {sel.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-ink-100 bg-accent-bg px-3 py-2 text-xs">
            <span className="font-semibold text-accent-fg">{sel.size} selected</span>
            <Button disabled={busy} onClick={() => batch("classify")}>Classify</Button>
            <Button disabled={busy} onClick={() => batch("compare")}>Run comparison</Button>
            <Button disabled={busy} onClick={() => batch("mark_no_action")}>Mark no action</Button>
            <Button disabled={busy} onClick={() => { const u = window.prompt("Assign to user id (e.g. u_ops_1)"); if (u) batch("assign", { user_id: u }); }}>Assign</Button>
            <Button disabled={busy} onClick={() => batch("draft")}>Prepare drafts</Button>
            <Button disabled={busy} onClick={() => batch("request_review")}>Request review</Button>
            <Button disabled={busy} onClick={() => batch("export")}>Export CSV</Button>
            <Button disabled={busy} onClick={() => batch("export_xlsx")}>Export Excel</Button>
            <Button disabled={busy} onClick={() => batch("report_xlsx")}>Report (selected)</Button>
            <Button disabled={busy} kind="danger" onClick={() => batch("archive")}>Archive</Button>
            <span className="text-ink-500">External sending is never batched.</span>
          </div>
        )}

        <div className="overflow-x-auto scrollbar-thin">
          <table className="w-full min-w-[880px] text-left text-xs">
            <colgroup>
              <col className="w-8" />
              {visible.map((c) => <col key={c.key} style={c.key === "case" ? { width: caseColWidth } : undefined} />)}
            </colgroup>
            <thead className="bg-ink-50 text-[11px] uppercase tracking-wide text-ink-500">
              <tr>
                <th className="px-2 py-2"><input type="checkbox" aria-label="Select all on page" checked={!!rows?.length && rows.every((r) => sel.has(r.id))} onChange={(e) => setSel(e.target.checked ? new Set((rows || []).map((r) => r.id)) : new Set())} /></th>
                {visible.map((c) => (
                  <th key={c.key} className={`px-2 py-2 ${c.key === "case" ? "relative overflow-hidden" : ""}`} title={c.title} style={c.key === "case" ? caseColStyle : undefined}>
                    {c.key === "case" ? (
                      <>
                        <span className="block truncate pr-1">{c.label}</span>
                        <CaseColHandle storageKey={CASE_COL_KEY} width={caseColWidth} onChange={setCaseColWidth} labels={caseLabels} />
                      </>
                    ) : c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows === null && Array.from({ length: 6 }, (_, i) => <tr key={i} className="border-t border-ink-100"><td colSpan={visible.length + 1} className="px-2 py-2"><div className="h-6 animate-pulse rounded bg-ink-100" /></td></tr>)}
              {rows?.map((r) => (
                <tr key={r.id} className={`border-t border-ink-100 align-top transition hover:bg-accent-bg/30 ${sel.has(r.id) ? "bg-accent-bg/40" : ""} ${agentStateOf(r) === "done" ? "opacity-70" : ""}`}>
                  <td className="px-2 py-2"><input type="checkbox" aria-label={`Select ${r.id}`} checked={sel.has(r.id)} onChange={() => toggle(r.id)} /></td>
                  {visible.map((c) => <td key={c.key} className={`px-2 py-2 ${c.key === "case" ? "overflow-hidden" : ""} ${typeof c.td === "function" ? c.td(r) : c.td || ""}`} style={c.key === "case" ? caseColStyle : undefined}>{c.render(r)}</td>)}
                </tr>
              ))}
              {rows?.length === 0 && <tr><td colSpan={visible.length + 1} className="px-4 py-10 text-center text-sm text-ink-500">{apiDown ? "The API is offline. Start the backend on port 8000 and refresh." : f.agent === "pending" ? <span>Every case in this view has been run by the agent — see <Link href="/history" className="font-semibold text-accent-fg hover:underline">Processed</Link> or <button type="button" onClick={() => setFilter("agent", "any")} className="font-semibold text-accent-fg hover:underline">show processed cases</button>.</span> : <span>No cases match these filters. <button type="button" onClick={() => { setF(EMPTY_FILTERS); setPage(0); }} className="font-semibold text-accent-fg hover:underline">Clear filters</button></span>}</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-ink-100 px-3 py-2 text-xs text-ink-500">
          <span>Page {page + 1} of {pages}</span>
          <div className="flex gap-1"><Button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button><Button disabled={(page + 1) * limit >= total} onClick={() => setPage(page + 1)}>Next</Button></div>
        </div>
      </div>
      <FilterPanel open={filtersOpen} onClose={closeFilters} f={f} setFilter={setFilter} onClear={() => { setF(EMPTY_FILTERS); setPage(0); }} users={users} myMailbox={myMailbox} sharedMailbox={sharedMailbox} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return <div><div className="dashboard-number text-xl font-semibold tabular-nums leading-none text-[#4b2818]">{value}</div><div className="mt-1 text-[9px] font-medium uppercase tracking-wide text-ink-500">{label}</div></div>;
}
function Panel({ title, children, link, right, className = "" }: { title: string; children: React.ReactNode; link: { href: string; label: string }; right?: React.ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 overflow-hidden rounded-2xl border border-orange-100 bg-[radial-gradient(circle,rgba(236,122,42,.18)_1px,transparent_1.2px)] bg-[size:14px_14px] p-3 shadow-card transition duration-200 hover:-translate-y-0.5 hover:border-orange-200 hover:shadow-glow sm:p-3.5 ${className}`}>
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-bold text-accent-fg [font-family:Georgia,'Times_New_Roman',serif]">{title}</h2>{right}{link.href && link.label && <Link href={link.href} className="text-[11px] font-semibold text-accent hover:underline">{link.label} →</Link>}</header>
      {children}
    </section>
  );
}
function Skeleton({ n }: { n: number }) { return <div className="space-y-2" aria-busy>{Array.from({ length: n }, (_, i) => <div key={i} className="h-3.5 animate-pulse rounded bg-ink-100" />)}</div>; }
function Trend({ direction }: { direction: "up" | "down" }) {
  const points = direction === "up" ? "2,20 11,16 18,17 27,9 36,12 46,4" : "2,6 11,10 18,9 27,16 36,13 46,21";
  return <svg viewBox="0 0 48 24" className="h-7 w-14 overflow-visible" aria-label={`${direction}ward trend`}><polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[#4b2818]" /><path d={direction === "up" ? "M42 4h4v4" : "M42 21h4v-4"} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-[#4b2818]" /></svg>;
}
function ActivityTrend({ events }: { events: any[] }) {
  const heights = events.slice(0, 8).map((event, index) => event.actor_type === "USER" ? 72 - index * 3 : event.actor_type === "AI" ? 52 - index * 2 : 34 + index * 2);
  return <div className="mt-4 border-t border-orange-100 pt-3"><div className="mb-2 text-[10px] font-bold uppercase tracking-[.12em] text-accent">Activity pulse</div><div className="flex h-12 items-end gap-1.5">{heights.map((height, index) => <span key={index} className="flex-1 rounded-t-full bg-gradient-to-t from-[#d66a1d] to-[#f7b46f] opacity-85" style={{ height: `${Math.max(18, height)}%` }} />)}</div></div>;
}
function Ring({ value, total }: { value: number; total: number }) {
  const pct = total ? Math.min(100, Math.round((value / total) * 100)) : 0;
  const r = 26, c = 2 * Math.PI * r;
  return (
    <svg width="72" height="72" viewBox="0 0 72 72" role="img" aria-label={`${pct}% of emails flagged`}>
      <circle cx="36" cy="36" r={r} fill="none" stroke="#f5f5f4" strokeWidth="8" />
      <circle cx="36" cy="36" r={r} fill="none" stroke="#ea580c" strokeWidth="8" strokeDasharray={`${(pct / 100) * c} ${c}`} strokeLinecap="round" transform="rotate(-90 36 36)" />
      <text x="36" y="40" textAnchor="middle" fontSize="13" fontWeight="600" fill="#1c1917">{pct}%</text>
    </svg>
  );
}
function Dot({ ok, label }: { ok: boolean; label: string }) {
  return <span className={`inline-flex items-center gap-1 ${ok ? "text-match-fg" : "text-ink-400"}`}><span className={`h-2 w-2 rounded-full ${ok ? "bg-match" : "bg-ink-200"}`} aria-hidden />{label}</span>;
}


/** "—" (never run) / paused · when / done · when · by — the Inbox's view of the last AI-agent run. */
function RunCell({ r }: { r: CaseRow }) {
  const a = r.agent_run;
  if (!a || a.result === "error") return <span className="text-ink-400" title={a?.error ? `last run failed: ${a.error}` : "the AI agent has not run this case yet"}>{a?.error ? "error · retry" : "—"}</span>;
  if (a.result === "paused") return <span className="rounded-full bg-review-bg px-1.5 py-px text-[10px] font-bold uppercase text-review-fg" title={`paused ${fmtDate(a.last_run_at)} · waiting for a decision`}>paused</span>;
  return <span className="text-ink-600" title={`run ${a.runs}× · last by ${a.last_run_by} · ${a.ms} ms`}><span className="rounded-full bg-match-bg px-1.5 py-px text-[10px] font-bold uppercase text-match-fg">done</span> {fmtDate(a.last_run_at)}</span>;
}

/** Right-hand slide-over holding every work-queue filter. Filters apply as soon as they change. */
function FilterPanel({ open, onClose, f, setFilter, onClear, users, myMailbox, sharedMailbox }: { open: boolean; onClose: () => void; f: Record<string, string>; setFilter: (k: string, v: string) => void; onClear: () => void; users: any[]; myMailbox: string | null | undefined; sharedMailbox: boolean }) {
  const first = useRef<HTMLSelectElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    first.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeRef.current(); };
    window.addEventListener("keydown", onKey);
    return () => { document.body.style.overflow = prev; window.removeEventListener("keydown", onKey); };
  }, [open]);
  if (!open) return null;
  const userOpts = users.map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>);
  const opt = (o: string, label?: string) => <option key={o} value={o}>{label || o.replace(/_/g, " ")}</option>;
  return createPortal(
    <>
      <div className="fixed inset-0 z-[55] bg-ink-900/30" onClick={onClose} aria-hidden />
      <aside role="dialog" aria-modal="true" aria-labelledby="filter-title" className="fixed inset-y-0 right-0 z-[56] flex w-full flex-col bg-white shadow-2xl sm:w-[380px]">
        <header className="flex items-start justify-between gap-3 border-b border-ink-100 px-5 py-4">
          <div><h2 id="filter-title" className="text-base font-semibold text-ink-900">Filter work queue</h2><p className="mt-0.5 text-xs text-ink-500">Filters apply instantly to the work queue.</p></div>
          <button type="button" onClick={onClose} aria-label="Close filters" className="rounded-lg px-2 py-1 text-lg leading-none text-ink-400 transition hover:bg-ink-50 hover:text-ink-800">×</button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4 scrollbar-thin">
          <Field label="Sort"><select ref={first} className={inputClass} value={f.sort} onChange={(e) => setFilter("sort", e.target.value)}>{Object.entries(SORT_LABELS).map(([k, l]) => opt(k, l))}</select></Field>
          <Field label="Status"><select className={inputClass} value={f.status} onChange={(e) => setFilter("status", e.target.value)}><option value="">Any status</option>{STATUSES.map((s) => opt(s))}</select></Field>
          <Field label="Priority"><select className={inputClass} value={f.priority} onChange={(e) => setFilter("priority", e.target.value)}><option value="">Any priority</option>{["CRITICAL","HIGH","MEDIUM","LOW"].map((s) => opt(s))}</select></Field>
          <Field label="Intent"><select className={inputClass} value={f.intent} onChange={(e) => setFilter("intent", e.target.value)}><option value="">Any intent</option>{INTENTS.map((s) => opt(s))}</select></Field>
          <Field label="Mailbox"><select className={inputClass} value={f.mailbox} onChange={(e) => setFilter("mailbox", e.target.value)}><option value="">Any mailbox</option>{myMailbox && opt("me", `My mailbox (${myMailbox})`)}{sharedMailbox && opt("shared", "Shared desk mailbox")}</select></Field>
          <Field label="Mismatch"><select className={inputClass} value={f.mismatch} onChange={(e) => setFilter("mismatch", e.target.value)}><option value="">Any result</option>{opt("yes", "Mismatch")}{opt("no", "No mismatch")}</select></Field>
          <Field label="Security"><select className={inputClass} value={f.security} onChange={(e) => setFilter("security", e.target.value)}><option value="">Any outcome</option>{["SAFE","SPAM","SUSPICIOUS","SECURITY_REVIEW"].map((s) => opt(s))}</select></Field>
          <Field label="Assigned to"><select className={inputClass} value={f.assigned} onChange={(e) => setFilter("assigned", e.target.value)}><option value="">Anyone</option>{userOpts}</select></Field>
          <Field label="Shared with"><select className={inputClass} value={f.shared} onChange={(e) => setFilter("shared", e.target.value)}><option value="">Anyone</option>{userOpts}</select></Field>
          <Field label="Sender"><input className={inputClass} placeholder="name@company.com" value={f.sender} onChange={(e) => setFilter("sender", e.target.value)} /></Field>
          <Field label="Minimum confidence"><input type="number" step="0.05" min="0" max="1" placeholder="e.g. 0.80" className={inputClass} value={f.min_confidence} onChange={(e) => setFilter("min_confidence", e.target.value)} /></Field>
          <Field label="Agent run" hint="Processed cases live on the Processed page."><select className={inputClass} value={f.agent} onChange={(e) => setFilter("agent", e.target.value || "any")}>{["pending","paused","done","any"].map((s) => opt(s, AGENT_LABELS[s]))}</select></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Received from"><input type="date" className={inputClass} value={f.date_from} onChange={(e) => setFilter("date_from", e.target.value)} /></Field>
            <Field label="Received to"><input type="date" className={inputClass} value={f.date_to.slice(0, 10)} onChange={(e) => setFilter("date_to", e.target.value ? e.target.value + "T23:59:59" : "")} /></Field>
          </div>
        </div>
        <footer className="flex items-center justify-between border-t border-ink-100 px-5 py-3">
          <Button kind="ghost" onClick={onClear}>Clear all</Button>
          <Button kind="primary" onClick={onClose}>Done</Button>
        </footer>
      </aside>
    </>,
    document.body,
  );
}

const MenuCtx = createContext<() => void>(() => {});

/** Small dropdown menu. The panel is portalled with fixed positioning so the table's horizontal scroll never clips it. */
function Menu({ label, trigger, triggerClassName, width = 224, children }: { label: string; trigger: React.ReactNode; triggerClassName: string; width?: number; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const close = useCallback(() => { setOpen(false); setPos(null); }, []);
  useLayoutEffect(() => {
    if (!open || !btn.current || !panel.current) return;
    const b = btn.current.getBoundingClientRect();
    const h = panel.current.offsetHeight;
    const m = 8, gap = 4;
    const left = Math.max(m, Math.min(b.right - width, window.innerWidth - width - m));
    let top = b.bottom + gap;
    if (top + h > window.innerHeight - m && b.top - gap - h >= m) top = b.top - gap - h;
    setPos({ top, left });
  }, [open, width]);
  // the panel is kept invisible until it is positioned, and invisible elements cannot take focus
  useEffect(() => { if (open && pos) panel.current?.querySelector<HTMLElement>('[role^="menuitem"]:not([disabled])')?.focus({ preventScroll: true }); }, [open, pos]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { const t = e.target as Node; if (panel.current?.contains(t) || btn.current?.contains(t)) return; close(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { close(); btn.current?.focus(); } };
    const onScroll = (e: Event) => { if (panel.current?.contains(e.target as Node)) return; close(); };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", close);
    return () => { document.removeEventListener("pointerdown", onDown, true); document.removeEventListener("keydown", onKey); window.removeEventListener("scroll", onScroll, true); window.removeEventListener("resize", close); };
  }, [open, close]);
  const onPanelKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = Array.from(panel.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([disabled])') || []);
    if (!items.length) return;
    e.preventDefault();
    const i = items.indexOf(document.activeElement as HTMLElement);
    items[e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length].focus();
  };
  return (
    <>
      <button ref={btn} type="button" aria-haspopup="menu" aria-expanded={open} aria-label={label} title={label} onClick={() => (open ? close() : setOpen(true))} className={triggerClassName}>{trigger}</button>
      {open && createPortal(
        <MenuCtx.Provider value={close}>
          <div ref={panel} role="menu" aria-label={label} tabIndex={-1} onKeyDown={onPanelKey} style={{ position: "fixed", top: pos?.top ?? 0, left: pos?.left ?? 0, width, visibility: pos ? "visible" : "hidden" }} className="z-50 overflow-hidden rounded-xl border border-ink-200 bg-white py-1 shadow-lg">{children}</div>
        </MenuCtx.Provider>,
        document.body,
      )}
    </>
  );
}
function MenuItem({ children, onClick, checked, disabled, keepOpen }: { children: React.ReactNode; onClick: () => void; checked?: boolean; disabled?: boolean; keepOpen?: boolean }) {
  const close = useContext(MenuCtx);
  return (
    <button type="button" role={checked === undefined ? "menuitem" : "menuitemcheckbox"} aria-checked={checked} disabled={disabled} onClick={() => { onClick(); if (!keepOpen) close(); }} className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs text-ink-800 transition hover:bg-ink-50 focus:bg-accent-bg focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
      <span>{children}</span>{checked && <span aria-hidden className="text-ink-700">✓</span>}
    </button>
  );
}
