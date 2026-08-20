"use client";

import type { CaseStatus, DraftStatus, EmailStatus, Typ, Wichtigkeit } from "@/lib/api";
import {
  statusLabel,
  TYP_LABELS,
  WICHTIGKEIT_CLASS,
  WICHTIGKEIT_LABELS,
} from "@/lib/labels";

export function WichtigkeitBadge({ value }: { value: Wichtigkeit | null }) {
  if (!value) return null;
  return <span className={WICHTIGKEIT_CLASS[value]}>{WICHTIGKEIT_LABELS[value]}</span>;
}

export function TypBadge({ value }: { value: Typ }) {
  if (value === "keiner") return null;
  return <span className="badge badge-outline">{TYP_LABELS[value]}</span>;
}

export function StatusBadge({ value }: { value: EmailStatus | DraftStatus | CaseStatus }) {
  return <span className="badge badge-outline">{statusLabel(value)}</span>;
}
