"use client";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui";

/**
 * Form-based policy editor. The schema below only describes how to *display* each
 * key of the backend DEFAULT_POLICY; the backend stays the source of truth and any
 * key not described here still shows up (as JSON) so nothing is hidden.
 * Saving sends only the sections that changed, as full sections (PUT /policies
 * replaces a section wholesale).
 */
type Kind = "number" | "integer" | "percent" | "boolean" | "text" | "list" | "enum" | "readonly";
type FieldSpec = { label: string; help?: string; kind: Kind; options?: string[]; min?: number; max?: number; step?: number };
type SectionSpec = { title: string; blurb: string; fields: Record<string, FieldSpec> };

const ROLE_OPTIONS = ["OPERATIONS_STAFF", "SUPERVISOR", "ADMIN", "AUDITOR"];

export const POLICY_SCHEMA: Record<string, SectionSpec> = {
  verification: {
    title: "Verification",
    blurb: "How the seven-field comparison is decided. The baseline (SI as truth, exactly seven fields) is fixed by contract.",
    fields: {
      source_of_truth: { label: "Source of truth", kind: "readonly", help: "Always the Shipping Instruction." },
      required_fields: { label: "Required fields", kind: "readonly", help: "Exactly these seven, in this order. Not editable." },
      weight_unit: { label: "Weight unit", kind: "enum", options: ["kg"], help: "Gross weight is normalised to this unit before comparing." },
      field_confidence_threshold: { label: "Field confidence threshold", kind: "percent", min: 0.5, max: 1, step: 0.01, help: "A field extracted below this confidence is never decided — it goes to human review." },
      legal_name_normalization: { label: "Legal-name normalisation", kind: "enum", options: ["casefold_whitespace_only"], help: "Only case and whitespace are normalised. Legal names are never rewritten." },
      port_alias_policy: { label: "Port alias policy", kind: "enum", options: ["strip_unlocode_only"], help: "UN/LOCODE in brackets is stripped; port names are otherwise compared as written." },
    },
  },
  human_review: {
    title: "Human review",
    blurb: "When the pipeline stops and asks a person instead of asserting a verdict.",
    fields: {
      confidence_below: { label: "Route to human review below", kind: "percent", min: 0.5, max: 1, step: 0.01, help: "Overall extraction confidence under this value routes the case to HUMAN_REVIEW." },
      missing_required_field: { label: "Missing required field → review", kind: "boolean", help: "If any of the seven fields cannot be found, ask a person rather than guessing." },
      external_recipient_requires_approval: { label: "External recipients need approval", kind: "boolean", help: "Anything sent outside the company needs a Supervisor/Admin click." },
      suspicious_sender_to_security_review: { label: "Suspicious sender → security review", kind: "boolean", help: "Quarantine mail from suspicious senders before any processing." },
    },
  },
  communication: {
    title: "Communication",
    blurb: "Who may share and notify, and whether anything can ever leave automatically.",
    fields: {
      auto_send_external: { label: "Auto-send external email", kind: "boolean", help: "Keep this off: drafts are proposals, humans send." },
      external_drafts_require_confirmation: { label: "External drafts require confirmation", kind: "boolean", help: "A preview + explicit confirm step before an external message is sent." },
      internal_share_roles: { label: "Roles that may share internally", kind: "list", options: ROLE_OPTIONS },
      external_notify_roles: { label: "Roles that may notify external parties", kind: "list", options: ROLE_OPTIONS },
    },
  },
  security: {
    title: "Security",
    blurb: "Inbox precheck: what is blocked, what looks suspicious, who is trusted.",
    fields: {
      blocked_attachment_types: { label: "Blocked attachment types", kind: "list", help: "Extensions that are never parsed. Add one per entry, with the leading dot." },
      suspicious_domain_words: { label: "Suspicious domain words", kind: "list", help: "Sender domains containing these words raise the suspicion score." },
      max_attachments: { label: "Max attachments per email", kind: "integer", min: 1, max: 50, step: 1 },
      duplicate_window_hours: { label: "Duplicate window (hours)", kind: "integer", min: 1, max: 720, step: 1, help: "Identical messages inside this window are treated as duplicates." },
      trusted_domains: { label: "Trusted domains", kind: "list", help: "Internal / first-party sender domains." },
      partner_domains: { label: "Partner domains", kind: "list", help: "Known forwarders, consignees and notify parties." },
      blocked_senders: { label: "Blocked senders", kind: "list", help: "Addresses or domains classified SPAM at the gate. Filled by accepting a learned suggestion; you can also edit it here." },
    },
  },
  learning: {
    title: "Learning from the security gate",
    blurb: "After enough flagged mail from one sender is archived, Policies proposes blocking it. Suggestions are never applied by themselves and never enable auto-send.",
    fields: {
      enabled: { label: "Propose changes", kind: "boolean", help: "Off = no suggestions are computed." },
      min_archives: { label: "Archives before a suggestion", kind: "integer", min: 2, max: 20, step: 1, help: "Flagged mails from one sender a person must archive first." },
      window_days: { label: "Look back (days)", kind: "integer", min: 7, max: 365, step: 1 },
    },
  },
  intent: {
    title: "Intent classifier",
    blurb: "Thresholds for the three-layer classifier: rules → trained text model → optional LLM.",
    fields: {
      intent_model_threshold: { label: "Trained model threshold", kind: "percent", min: 0.3, max: 1, step: 0.01, help: "Below this the trained model does not override the rules." },
      intent_model_rule_ceiling: { label: "Rule confidence ceiling", kind: "percent", min: 0.5, max: 1, step: 0.01, help: "Rules at or above this confidence are final; the model is not consulted." },
      intent_model_override_margin: { label: "Model override margin", kind: "number", min: 0, max: 0.5, step: 0.01, help: "How much more confident the model must be than the rule to win." },
      intent_llm_threshold: { label: "LLM fallback threshold", kind: "percent", min: 0.3, max: 1, step: 0.01, help: "The optional LLM is only asked when everything else is below this." },
    },
  },
};

const SECTION_ORDER = Object.keys(POLICY_SCHEMA);
const deepEqual = (a: any, b: any) => JSON.stringify(a) === JSON.stringify(b);

/** Sections whose content differs from the baseline, as complete sections (what PUT /policies expects). */
export function changedSections(base: Record<string, any>, draft: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const k of Object.keys(draft)) if (!deepEqual(base[k], draft[k])) out[k] = draft[k];
  return out;
}

export function PolicyEditor({ base, draft, onChange, disabled }: { base: Record<string, any>; draft: Record<string, any>; onChange: (next: Record<string, any>) => void; disabled: boolean }) {
  const sections = useMemo(() => [...SECTION_ORDER.filter((k) => k in draft), ...Object.keys(draft).filter((k) => !SECTION_ORDER.includes(k))], [draft]);
  const [openSection, setOpenSection] = useState<string>(sections[0] || "");
  const set = (section: string, key: string, value: any) => onChange({ ...draft, [section]: { ...draft[section], [key]: value } });

  return (
    <div className="space-y-2">
      {sections.map((section) => {
        const spec = POLICY_SCHEMA[section];
        const values = draft[section] || {};
        const dirty = !deepEqual(base[section], values);
        const isOpen = openSection === section;
        const keys = [...Object.keys(spec?.fields || {}).filter((k) => k in values), ...Object.keys(values).filter((k) => !(spec?.fields && k in spec.fields))];
        return (
          <section key={section} className={`rounded-xl border border-orange-200 transition duration-200 hover:-translate-y-0.5 hover:border-orange-400 hover:shadow-md ${dirty ? "bg-accent-bg/20" : "bg-white"}`}>
            <button type="button" onClick={() => setOpenSection(isOpen ? "" : section)} aria-expanded={isOpen} className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-[#fff8f1]">
              <span className={`text-xs transition ${isOpen ? "rotate-90" : ""}`} aria-hidden>▶</span>
              <span className="flex-1">
                <span className="text-sm font-bold text-accent">{spec?.title || section}</span>
                {spec?.blurb && <span className="mt-0.5 block text-xs text-ink-500">{spec.blurb}</span>}
              </span>
              {dirty && <Badge className="bg-accent text-white">changed</Badge>}
              <span className="text-[11px] text-ink-500">{keys.length} settings</span>
            </button>
            {isOpen && (
              <div className="divide-y divide-ink-100 border-t border-ink-100">
                {keys.map((key) => (
                  <FieldRow key={key} name={key} spec={spec?.fields?.[key]} value={values[key]} baseValue={base[section]?.[key]} disabled={disabled}
                    onChange={(v) => set(section, key, v)} onReset={() => set(section, key, base[section]?.[key])} />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function FieldRow({ name, spec, value, baseValue, disabled, onChange, onReset }: { name: string; spec?: FieldSpec; value: any; baseValue: any; disabled: boolean; onChange: (v: any) => void; onReset: () => void }) {
  const kind: Kind = spec?.kind || inferKind(value);
  const dirty = !deepEqual(value, baseValue);
  return (
    <div className={`grid gap-2 px-4 py-3 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1.4fr)_auto] md:items-start ${dirty ? "bg-accent-bg/30" : ""}`}>
      <div>
        <div className="text-sm font-medium text-ink-800">{spec?.label || name.replace(/_/g, " ")}</div>
        <div className="font-mono text-[10px] text-ink-400">{name}</div>
        {spec?.help && <div className="mt-1 text-xs text-ink-500">{spec.help}</div>}
      </div>
      <div className="min-w-0"><FieldInput kind={kind} spec={spec} value={value} disabled={disabled} onChange={onChange} /></div>
      <div className="flex items-start justify-end md:min-w-[110px]">
        {dirty ? <button type="button" onClick={onReset} disabled={disabled} className="text-[11px] font-semibold text-accent-fg hover:underline disabled:opacity-50" title={`Back to ${JSON.stringify(baseValue)}`}>reset</button> : <span className="text-[11px] text-ink-400">unchanged</span>}
      </div>
    </div>
  );
}

function inferKind(v: any): Kind {
  if (typeof v === "boolean") return "boolean";
  if (typeof v === "number") return Number.isInteger(v) ? "integer" : "number";
  if (Array.isArray(v)) return "list";
  if (typeof v === "string") return "text";
  return "readonly";
}

function FieldInput({ kind, spec, value, disabled, onChange }: { kind: Kind; spec?: FieldSpec; value: any; disabled: boolean; onChange: (v: any) => void }) {
  const box = "w-full rounded-lg border border-ink-200 bg-white px-2.5 py-1.5 text-sm text-ink-900 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-ring/60 disabled:bg-ink-50 disabled:text-ink-500";
  switch (kind) {
    case "boolean":
      return (
        <button type="button" role="switch" aria-checked={!!value} disabled={disabled} onClick={() => onChange(!value)}
          className={`inline-flex items-center gap-2 rounded-full border px-1 py-1 pr-3 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${value ? "border-match bg-match-bg text-match-fg" : "border-ink-200 bg-ink-50 text-ink-600"}`}>
          <span className={`flex h-5 w-9 items-center rounded-full p-0.5 transition ${value ? "bg-match" : "bg-ink-300"}`}><span className={`h-4 w-4 rounded-full bg-white shadow transition ${value ? "translate-x-4" : ""}`} /></span>
          {value ? "On" : "Off"}
        </button>
      );
    case "percent":
    case "number":
    case "integer": {
      const min = spec?.min ?? 0, max = spec?.max ?? (kind === "percent" ? 1 : 1000), step = spec?.step ?? (kind === "integer" ? 1 : 0.01);
      const parse = (s: string) => { const n = kind === "integer" ? parseInt(s, 10) : parseFloat(s); return isNaN(n) ? value : Math.min(max, Math.max(min, n)); };
      return (
        <div className="flex items-center gap-3">
          {kind !== "integer" && <input type="range" min={min} max={max} step={step} value={value ?? min} disabled={disabled} onChange={(e) => onChange(parse(e.target.value))} className="w-full accent-[#f36c13]" aria-label={spec?.label} />}
          <input type="number" min={min} max={max} step={step} value={value ?? ""} disabled={disabled} onChange={(e) => onChange(parse(e.target.value))} className={`${box} w-28 shrink-0 font-mono`} />
          {kind === "percent" && <span className="w-12 shrink-0 text-right font-mono text-xs text-ink-500">{Math.round((value ?? 0) * 100)}%</span>}
        </div>
      );
    }
    case "enum":
      return (
        <select value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={box}>
          {(spec?.options || [String(value)]).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    case "list":
      return <ListInput value={Array.isArray(value) ? value : []} options={spec?.options} disabled={disabled} onChange={onChange} />;
    case "readonly":
      return <div className="rounded-lg border border-dashed border-ink-200 bg-ink-50 px-2.5 py-1.5 font-mono text-xs text-ink-600">{Array.isArray(value) ? value.join(" · ") : typeof value === "object" ? JSON.stringify(value) : String(value)}</div>;
    default:
      return <input type="text" value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={box} />;
  }
}

function ListInput({ value, options, disabled, onChange }: { value: string[]; options?: string[]; disabled: boolean; onChange: (v: string[]) => void }) {
  const [text, setText] = useState("");
  const add = (raw: string) => {
    const items = raw.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean).filter((s) => !value.includes(s));
    if (items.length) onChange([...value, ...items]);
    setText("");
  };
  if (options) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const on = value.includes(o);
          return <button key={o} type="button" disabled={disabled} onClick={() => onChange(on ? value.filter((x) => x !== o) : [...value, o])} aria-pressed={on}
            className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${on ? "border-accent bg-accent-bg text-accent-fg" : "border-ink-200 bg-white text-ink-500 hover:border-accent"}`}>{o.replace(/_/g, " ")}</button>;
        })}
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-ink-200 bg-white p-1.5 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent-ring/60">
      <div className="flex flex-wrap gap-1">
        {value.map((item) => (
          <span key={item} className="inline-flex items-center gap-1 rounded-full bg-ink-100 px-2 py-0.5 font-mono text-[11px] text-ink-800">
            {item}
            {!disabled && <button type="button" onClick={() => onChange(value.filter((x) => x !== item))} className="text-ink-500 hover:text-mismatch" aria-label={`Remove ${item}`}>×</button>}
          </span>
        ))}
        {!disabled && (
          <input value={text} onChange={(e) => setText(e.target.value)} onBlur={() => text && add(text)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(text); } else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1)); }}
            placeholder={value.length ? "add…" : "type and press Enter"} className="min-w-[120px] flex-1 bg-transparent px-1 py-0.5 text-xs outline-none placeholder:text-ink-400" />
        )}
      </div>
    </div>
  );
}

export function ChangeSummary({ base, draft }: { base: Record<string, any>; draft: Record<string, any> }) {
  const rows: { section: string; key: string; from: any; to: any }[] = [];
  for (const section of Object.keys(draft)) for (const key of Object.keys(draft[section] || {})) if (!deepEqual(base[section]?.[key], draft[section][key])) rows.push({ section, key, from: base[section]?.[key], to: draft[section][key] });
  if (!rows.length) return <div className="text-xs text-ink-500">No changes yet. Edit a setting above and it will be listed here before you save.</div>;
  return (
    <ul className="space-y-1 text-xs">
      {rows.map((r) => (
        <li key={`${r.section}.${r.key}`} className="flex flex-wrap items-center gap-2 rounded-md bg-white px-2 py-1">
          <span className="font-mono text-[11px] text-ink-500">{r.section}.{r.key}</span>
          <span className="font-mono text-[11px] text-mismatch-fg line-through">{fmt(r.from)}</span><span aria-hidden>→</span><span className="font-mono text-[11px] text-match-fg">{fmt(r.to)}</span>
        </li>
      ))}
    </ul>
  );
}
const fmt = (v: any) => (Array.isArray(v) ? `[${v.join(", ")}]` : typeof v === "object" ? JSON.stringify(v) : String(v));
