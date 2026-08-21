"use client";

import type {
  CaseStatus,
  ContactLastStatus,
  DraftStatus,
  EmailStatus,
  Typ,
  Wichtigkeit,
} from "@/lib/api";
import {
  CONTACT_STATUS_CLASS,
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

export function ContactStatusBadge({ value }: { value: ContactLastStatus | null }) {
  if (!value) return null;
  return <span className={CONTACT_STATUS_CLASS[value]}>{statusLabel(value)}</span>;
}

/** The red "Follow-up nötig" label for contacts whose last inquiry is
 * still open past the configured threshold (see needs_followup in the
 * /api/contacts response). */
export function FollowUpBadge({ show }: { show: boolean }) {
  if (!show) return null;
  return <span className="badge badge-danger">Follow-up nötig</span>;
}
