"use client";

import type { ReactNode } from "react";

interface Props {
  loading: boolean;
  error: string | null;
  isEmpty: boolean;
  emptyMessage: string;
  children: ReactNode;
}

/**
 * The loading / error / empty / content ladder every list view needs.
 *
 * Each page used to spell this out itself, with subtly different wording
 * and ordering - and one of them showed "Lade…" and an error box at the
 * same time.
 */
export function AsyncState({ loading, error, isEmpty, emptyMessage, children }: Props) {
  if (error) return <div className="error-box">Fehler: {error}</div>;
  if (loading) return <p className="muted">Lade…</p>;
  if (isEmpty) return <div className="empty-state">{emptyMessage}</div>;
  return <>{children}</>;
}
