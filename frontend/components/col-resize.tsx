"use client";
import { useEffect, useRef, useState } from "react";

export const CASE_COL_DEFAULT = 80;
export const CASE_COL_MIN = 72;
export const CASE_COL_MAX = 448;

export function clampCaseCol(n: number) {
  return Math.min(CASE_COL_MAX, Math.max(CASE_COL_MIN, Math.round(n)));
}

export function persistCaseCol(storageKey: string, n: number) {
  const w = clampCaseCol(n);
  try { localStorage.setItem(storageKey, String(w)); } catch { /* ignore */ }
  return w;
}

export function measureCaseFit(labels: string[]) {
  if (typeof document === "undefined" || !labels.length) return CASE_COL_MAX;
  const el = document.createElement("span");
  el.style.cssText = "position:absolute;left:-9999px;top:0;white-space:nowrap;font:11px ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace";
  document.body.appendChild(el);
  let max = CASE_COL_DEFAULT;
  for (const label of labels) {
    el.textContent = label;
    max = Math.max(max, el.offsetWidth + 24);
  }
  el.remove();
  return clampCaseCol(max);
}

export function caseColStyle(width: number) {
  return { width, maxWidth: width, minWidth: width };
}

export function useCaseColWidth(storageKey: string) {
  const [width, setWidth] = useState(CASE_COL_DEFAULT);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return;
      const n = Number(raw);
      if (Number.isFinite(n)) setWidth(clampCaseCol(n));
    } catch { /* ignore */ }
  }, [storageKey]);
  return [width, setWidth] as const;
}

export function CaseColHandle({ storageKey, width, onChange, labels }: { storageKey: string; width: number; onChange: (n: number) => void; labels: string[] }) {
  const widthRef = useRef(width);
  widthRef.current = width;
  const drag = useRef<{ x: number; w: number } | null>(null);
  const apply = (n: number) => { const w = persistCaseCol(storageKey, n); onChange(w); };

  return (
    <button
      type="button"
      aria-orientation="vertical"
      aria-valuemin={CASE_COL_MIN}
      aria-valuemax={CASE_COL_MAX}
      aria-valuenow={width}
      aria-label="Resize Case column"
      title="Drag to expand the case id. Double-click to fit or reset."
      className="absolute inset-y-0 right-0 z-10 w-2 cursor-col-resize touch-none border-0 bg-transparent p-0 hover:bg-accent/40"
      onPointerDown={(e) => {
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, w: widthRef.current };
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        onChange(clampCaseCol(drag.current.w + e.clientX - drag.current.x));
      }}
      onPointerUp={() => {
        if (!drag.current) return;
        drag.current = null;
        persistCaseCol(storageKey, widthRef.current);
      }}
      onPointerCancel={() => { drag.current = null; }}
      onDoubleClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        drag.current = null;
        apply(Math.abs(widthRef.current - CASE_COL_DEFAULT) <= 4 ? measureCaseFit(labels) : CASE_COL_DEFAULT);
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") { e.preventDefault(); apply(widthRef.current + 16); }
        if (e.key === "ArrowLeft") { e.preventDefault(); apply(widthRef.current - 16); }
        if (e.key === "Home") { e.preventDefault(); apply(CASE_COL_DEFAULT); }
        if (e.key === "End") { e.preventDefault(); apply(measureCaseFit(labels)); }
      }}
    />
  );
}
