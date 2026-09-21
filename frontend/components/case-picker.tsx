"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { StatusBadge } from "@/components/ui";

export type CaseSuggestion = { id: string; subject: string; sender: string; status: string; priority: string; mismatch_count: number; mailbox: string | null; agent?: "pending" | "paused" | "done" };

function useSuggestions(query: string, open: boolean) {
  const [items, setItems] = useState<CaseSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    const t = setTimeout(() => {
      api<{ items: CaseSuggestion[] }>(`/cases/suggest?q=${encodeURIComponent(query)}&limit=50`)
        .then((d) => { if (!cancelled) setItems(d.items || []); })
        .catch(() => { if (!cancelled) setItems([]); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, 200);
    return () => { cancelled = true; clearTimeout(t); };
  }, [query, open]);
  return { items, loading };
}

function Row({ s, active, onPick }: { s: CaseSuggestion; active: boolean; onPick: () => void }) {
  return (
    <li role="option" aria-selected={active} onMouseDown={(e) => { e.preventDefault(); onPick(); }}
      className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs ${active ? "bg-accent-bg" : "hover:bg-ink-50"}`}>
      <span className="w-28 shrink-0 truncate font-mono text-[11px] text-accent-fg" title={s.id}>{s.id.replace("case_", "")}</span>
      <span className="min-w-0 flex-1 truncate text-ink-800">{s.subject || "(no subject)"} <span className="text-ink-400">· {s.sender}</span></span>
      {s.mismatch_count > 0 && <span className="shrink-0 whitespace-nowrap rounded-full bg-mismatch-bg px-1.5 text-[10px] font-semibold text-mismatch-fg">{s.mismatch_count} mismatch</span>}
      {s.agent && s.agent !== "pending" && <span className={`shrink-0 whitespace-nowrap rounded-full px-1.5 text-[10px] font-semibold ${s.agent === "paused" ? "bg-review-bg text-review-fg" : "bg-match-bg text-match-fg"}`} title="AI-agent run state">{s.agent === "paused" ? "paused" : "processed"}</span>}
      {(!s.agent || s.agent === "pending") && <span className="shrink-0 whitespace-nowrap rounded-full bg-ink-100 px-1.5 text-[10px] font-semibold text-ink-600" title="the AI agent has not run this case yet">not run</span>}
      <span className="hidden shrink-0 whitespace-nowrap sm:inline-flex"><StatusBadge status={s.status} /></span>
    </li>
  );
}

/** Single-case combobox: type an id fragment, subject or sender; pick with mouse or arrows + Enter. */
export function CasePicker({ value, onChange, placeholder = "Type a case id, subject or sender", className = "" }: { value: string; onChange: (id: string) => void; placeholder?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value);
  const [cursor, setCursor] = useState(0);
  const { items, loading } = useSuggestions(query, open);
  useEffect(() => { setQuery(value); }, [value]);
  const pick = (s: CaseSuggestion) => { onChange(s.id); setQuery(s.id); setOpen(false); };
  return (
    <div className={`relative ${className}`}>
      <input role="combobox" aria-expanded={open} aria-autocomplete="list" aria-label="Case id" value={query}
        onChange={(e) => { setQuery(e.target.value); onChange(e.target.value.trim()); setOpen(true); setCursor(0); }}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (!open || !items.length) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, items.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
          else if (e.key === "Enter") { e.preventDefault(); pick(items[cursor]); }
          else if (e.key === "Escape") setOpen(false);
        }}
        placeholder={placeholder} className="w-full rounded-md border border-ink-200 px-2 py-1.5 font-mono text-xs" />
      {open && (items.length > 0 || loading) && (
        <ul role="listbox" className="absolute left-0 right-0 top-full z-30 mt-1 max-h-80 overflow-y-auto overflow-x-hidden rounded-xl border border-ink-200 bg-white py-1 shadow-lg scrollbar-thin">
          {loading && items.length === 0 && <li className="px-3 py-1.5 text-xs text-ink-400">Searching…</li>}
          {items.map((s, i) => <Row key={s.id} s={s} active={i === cursor} onPick={() => pick(s)} />)}
        </ul>
      )}
    </div>
  );
}

/** Multi-case picker: chips + the same autocomplete; paste comma/newline separated ids to add many at once. */
export function CaseMultiPicker({ ids, onChange, className = "" }: { ids: string[]; onChange: (ids: string[]) => void; className?: string }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const { items, loading } = useSuggestions(query, open);
  const inputRef = useRef<HTMLInputElement>(null);
  const add = useCallback((raw: string | string[]) => {
    const incoming = (Array.isArray(raw) ? raw : raw.split(/[\s,;]+/)).map((s) => s.trim()).filter(Boolean);
    if (!incoming.length) return;
    const next = [...ids];
    for (const id of incoming) if (!next.includes(id)) next.push(id);
    onChange(next);
    setQuery("");
  }, [ids, onChange]);
  const remove = (id: string) => onChange(ids.filter((x) => x !== id));
  const candidates = items.filter((s) => !ids.includes(s.id));
  return (
    <div className={`relative ${className}`}>
      <div className="flex min-h-[38px] flex-wrap items-center gap-1 rounded-md border border-ink-200 bg-white px-2 py-1" onClick={() => inputRef.current?.focus()}>
        {ids.map((id) => (
          <span key={id} className="inline-flex items-center gap-1 rounded-full bg-accent-bg px-2 py-0.5 font-mono text-[11px] text-accent-fg">
            {id.replace("case_", "")}
            <button type="button" aria-label={`Remove ${id}`} onClick={() => remove(id)} className="text-ink-500 hover:text-mismatch">×</button>
          </span>
        ))}
        <input ref={inputRef} role="combobox" aria-expanded={open} aria-autocomplete="list" aria-label="Add case ids" value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); setCursor(0); }}
          onFocus={() => setOpen(true)} onBlur={() => { setOpen(false); if (query.includes(",") || query.includes("\n")) add(query); }}
          onPaste={(e) => { const text = e.clipboardData.getData("text"); if (/[\s,;]/.test(text.trim())) { e.preventDefault(); add(text); } }}
          onKeyDown={(e) => {
            if (e.key === "Backspace" && !query && ids.length) { remove(ids[ids.length - 1]); return; }
            if (e.key === "Enter" || e.key === "," ) {
              e.preventDefault();
              if (open && candidates[cursor]) add(candidates[cursor].id); else if (query.trim()) add(query);
              return;
            }
            if (!open || !candidates.length) return;
            if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, candidates.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
            else if (e.key === "Escape") setOpen(false);
          }}
          placeholder={ids.length ? "Add more…" : "Type to search cases, or paste ids"} className="min-w-[160px] flex-1 border-0 px-1 py-1 font-mono text-xs outline-none" />
      </div>
      {open && (candidates.length > 0 || loading) && (
        <ul role="listbox" className="absolute left-0 right-0 top-full z-30 mt-1 max-h-80 overflow-y-auto overflow-x-hidden rounded-xl border border-ink-200 bg-white py-1 shadow-lg scrollbar-thin">
          {loading && candidates.length === 0 && <li className="px-3 py-1.5 text-xs text-ink-400">Searching…</li>}
          {candidates.map((s, i) => <Row key={s.id} s={s} active={i === cursor} onPick={() => add(s.id)} />)}
        </ul>
      )}
    </div>
  );
}
