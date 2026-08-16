"use client";

import type { Typ, Wichtigkeit } from "@/lib/api";
import {
  STATUS_LABELS,
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

export function StatusBadge({ value }: { value: string }) {
  return <span className="badge badge-outline">{STATUS_LABELS[value] ?? value}</span>;
}
