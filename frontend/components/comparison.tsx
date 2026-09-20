"use client";
import { useState } from "react";
import type { Comparison, ComparisonField } from "@/lib/api";
import { Badge, Confidence, RESULT_STYLES } from "@/components/ui";

/** The fixed, always-visible seven-field comparison card. Mismatches are loud; matches are quiet but never hidden. */
export function SevenFieldCard({ cmp, reviewReason, onEvidence }: { cmp: Comparison | null; reviewReason?: string | null; onEvidence?: (f: ComparisonField) => void }) {
  const [showMatches, setShowMatches] = useState(true);
  if (!cmp) {
    return (
      <div className="rounded-2xl border-2 border-dashed border-ink-200 bg-white p-5 shadow-card">
        <div className="text-sm font-semibold text-ink-800">Seven-field SI ↔ Draft BL comparison</div>
        <div className="mt-1 text-sm text-ink-500">Not compared yet{reviewReason ? ` — ${reviewReason.replace(/_/g, " ")}` : ""}. The seven required fields (Shipper, Consignee, Notify Party, Port of Loading, Port of Discharge, Container Count, Gross Weight kg) will appear here once both documents are readable.</div>
      </div>
    );
  }
  const passed = cmp.mismatch_count === 0 && cmp.review_fields.length === 0;
  const tone = passed ? "border-match bg-match-bg/40" : cmp.mismatch_count > 0 ? "border-mismatch bg-mismatch-bg/30" : "border-review bg-review-bg/40";
  return (
    <div className={`min-w-0 overflow-hidden rounded-2xl border-2 bg-white shadow-card ${tone}`}>
      <div className="flex flex-wrap items-start gap-3 border-b border-ink-100 bg-white/80 px-3 py-3 sm:px-5 sm:py-4">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-wide text-ink-500">Seven-field verification · SI is source of truth</div>
          <div className={`text-lg font-semibold ${passed ? "text-match-fg" : cmp.mismatch_count > 0 ? "text-mismatch-fg" : "text-review-fg"}`}>{cmp.message}</div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs sm:ml-auto">
          <Badge className={passed ? "bg-match text-white" : cmp.mismatch_count > 0 ? "bg-mismatch text-white" : "bg-review text-white"}>{cmp.comparison_status.replace(/_/g, " ")}</Badge>
          <span className="text-ink-500">{cmp.mismatch_count}/{cmp.required_field_count} mismatch · {cmp.review_fields.length} review</span>
          <label className="flex items-center gap-1 text-ink-500"><input type="checkbox" checked={showMatches} onChange={(e) => setShowMatches(e.target.checked)} />show matches</label>
        </div>
      </div>
      <div className="max-w-full overflow-x-auto"><table className="w-full min-w-[860px] text-sm">
        <thead className="text-[11px] uppercase tracking-wide text-ink-500">
          <tr className="border-b border-ink-100"><th className="px-4 py-2 text-left">Field</th><th className="px-2 py-2 text-left">Shipping Instruction (truth)</th><th className="px-2 py-2 text-left">Draft BL</th><th className="px-2 py-2 text-left">Result</th><th className="px-2 py-2 text-left">Confidence</th><th className="px-2 py-2 text-left">Attention</th></tr>
        </thead>
        <tbody>
          {cmp.fields.map((f) => {
            const st = RESULT_STYLES[f.result];
            if (f.result === "MATCH" && !showMatches) return null;
            return (
              <tr key={f.field} className={`border-b border-ink-100 ${st.row}`}>
                <td className="px-4 py-2 font-medium text-ink-900">{f.label}</td>
                <td className="px-2 py-2 font-mono text-xs"><Val v={f.si_original} norm={f.si_normalized} strong={f.result === "MISMATCH"} /></td>
                <td className="px-2 py-2 font-mono text-xs"><Val v={f.bl_original} norm={f.bl_normalized} strong={f.result === "MISMATCH"} /></td>
                <td className="px-2 py-2"><Badge className={`border ${st.badge}`}>{st.label}</Badge></td>
                <td className="px-2 py-2"><Confidence value={f.confidence} /></td>
                <td className="px-2 py-2 text-xs text-ink-700">
                  {f.result === "MATCH" ? <span className="text-ink-400">—</span> : <div><div>{f.attention}</div><div className="text-[11px] text-ink-500">{f.reason}</div></div>}
                  {onEvidence && <button onClick={() => onEvidence(f)} className="mt-1 text-[11px] text-accent hover:underline">view evidence ↗</button>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table></div>
      <div className="border-t border-ink-100 bg-ink-50/70 px-3 py-3 text-[11px] text-ink-500 sm:px-5">Compared {new Date(cmp.compared_at).toLocaleString()} · normalization: case/whitespace/unit only (no legal-name rewriting) · a mismatch in one field never affects another.</div>
    </div>
  );
}

function Val({ v, norm, strong }: { v: string | null; norm: any; strong: boolean }) {
  if (v === null || v === undefined || v === "") return <span className="italic text-review-fg">blank / missing</span>;
  return <span className={strong ? "font-semibold text-ink-900" : "text-ink-800"} title={norm !== null && norm !== undefined ? `normalized: ${norm}` : ""}>{v}</span>;
}

export function EvidencePanel({ cmp, docs, selected }: { cmp: Comparison | null; docs: Record<string, { name: string; text: string | null }>; selected?: ComparisonField | null }) {
  if (!cmp) return <div className="text-sm text-ink-500">No comparison evidence yet.</div>;
  const fields = selected ? [selected] : cmp.fields;
  return (
    <div className="space-y-3">
      {fields.map((f) => (
        <div key={f.field} className={`rounded-xl border p-4 shadow-sm ${f.result === "MISMATCH" ? "border-mismatch/40 bg-mismatch-bg/30" : "border-ink-200 bg-white"}`}>
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">{f.label} <Badge className={`border ${RESULT_STYLES[f.result].badge}`}>{RESULT_STYLES[f.result].label}</Badge></div>
          <div className="grid gap-3 md:grid-cols-2">
            <Ev title="SI evidence" ev={f.si_evidence} doc={docs[f.si_evidence.document_id || ""]} value={f.si_original} />
            <Ev title="Draft BL evidence" ev={f.bl_evidence} doc={docs[f.bl_evidence.document_id || ""]} value={f.bl_original} />
          </div>
        </div>
      ))}
    </div>
  );
}

function Ev({ title, ev, doc, value }: { title: string; ev: any; doc?: { name: string; text: string | null }; value: string | null }) {
  return (
    <div className="rounded-lg border border-ink-100 bg-white p-3 text-xs">
      <div className="mb-1 flex items-center justify-between text-[11px] uppercase tracking-wide text-ink-500"><span>{title}</span><span className="font-mono normal-case">{doc?.name || ev.document_id || "—"}{ev.page ? ` · p.${ev.page}` : ""}{ev.line ? ` · line ${ev.line}` : ""}</span></div>
      {ev.label_found && <div className="text-ink-500">label resolved: <span className="font-mono text-ink-800">{ev.label_found}</span></div>}
      <pre className="mt-1 whitespace-pre-wrap rounded bg-ink-50 p-2 font-mono text-[11px] text-ink-900">{ev.snippet || "(no snippet — value missing)"}</pre>
      {value !== null && value !== "" && <div className="mt-1 text-ink-500">extracted: <span className="font-mono text-ink-900">{value}</span></div>}
    </div>
  );
}
