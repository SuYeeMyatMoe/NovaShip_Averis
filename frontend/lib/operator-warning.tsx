"use client";
import { useCallback, useState } from "react";
import { type OperatorWarning, WarningDialog } from "@/components/ui";

/**
 * Surface operator-guard signals as a modal. Feed it any mutation response (`operator_warning`),
 * a case view (`anomalies`), or a caught ApiError (`detail.operator_warning` / `detail.operator_warning === true`).
 */
export function useOperatorWarning() {
  const [warning, setWarning] = useState<OperatorWarning | null>(null);
  const notice = useCallback((source: any): boolean => {
    const w: OperatorWarning | null =
      source?.operator_warning && typeof source.operator_warning === "object" ? source.operator_warning
      : source?.detail?.operator_warning && typeof source.detail.operator_warning === "object" ? source.detail.operator_warning
      : source?.detail?.operator_warning === true ? { signal: "OPERATOR_SHARE_DENIED", severity: "MEDIUM", evidence: source.message || source.detail?.error || "Action denied.", recommended_action: "Use an authorised recipient and preview before sending." }
      : null;
    if (w) { if (w.dialog === false) return false; setWarning(w); return true; }
    return false;
  }, []);
  const dialog = <WarningDialog warning={warning} onClose={() => setWarning(null)} />;
  return { warning, notice, dialog, clear: () => setWarning(null) };
}
