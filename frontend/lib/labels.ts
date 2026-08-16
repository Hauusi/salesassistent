import type { Typ, Wichtigkeit } from "./api";

export const WICHTIGKEIT_LABELS: Record<Wichtigkeit, string> = {
  antwort_erforderlich: "Antwort erforderlich",
  information: "Information",
  newsletter: "Newsletter",
  spam_verdacht: "Spam-Verdacht",
};

export const WICHTIGKEIT_CLASS: Record<Wichtigkeit, string> = {
  antwort_erforderlich: "badge badge-warn",
  information: "badge badge-info",
  newsletter: "badge badge-muted",
  spam_verdacht: "badge badge-danger",
};

export const TYP_LABELS: Record<Typ, string> = {
  bestellung: "Bestellung",
  anfrage: "Anfrage",
  keiner: "-",
};

export const STATUS_LABELS: Record<string, string> = {
  neu: "Neu",
  abgelegt: "Abgelegt",
  wartet_auf_freigabe: "Wartet auf Freigabe",
  erledigt: "Erledigt",
  ausgeblendet: "Ausgeblendet",
  entwurf: "Entwurf",
  freigegeben: "Freigegeben",
  abgelehnt: "Abgelehnt",
  versendet: "Versendet",
};

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("de-DE", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
