import type {
  CaseStatus,
  ContactLastStatus,
  DraftStatus,
  EmailStatus,
  Typ,
  Wichtigkeit,
} from "./api";

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

/**
 * Status labels, split by the enum they belong to.
 *
 * These used to be one `Record<string, string>` mixing email, draft and
 * case statuses. Because the key type was `string`, adding a value to any
 * of those enums compiled fine and only showed up at runtime as a raw
 * `snake_case` string in the UI. Keyed by the real union types, the build
 * now fails until the label exists.
 */
export const EMAIL_STATUS_LABELS: Record<EmailStatus, string> = {
  neu: "Neu",
  abgelegt: "Abgelegt",
  wartet_auf_freigabe: "Wartet auf Freigabe",
  erledigt: "Erledigt",
  ausgeblendet: "Ausgeblendet",
};

export const DRAFT_STATUS_LABELS: Record<DraftStatus, string> = {
  entwurf: "Entwurf",
  freigegeben: "Wird versendet",
  abgelehnt: "Abgelehnt",
  versendet: "Versendet",
};

export const CASE_STATUS_LABELS: Record<CaseStatus, string> = {
  offen: "Offen",
  geschlossen: "Geschlossen",
};

/** Status of a contact's most recent inquiry - see ContactLastStatus in the
 * backend (derived from EmailStatus + CaseStatus, not a stored field). */
export const CONTACT_STATUS_LABELS: Record<ContactLastStatus, string> = {
  offen: "Offen",
  beantwortet: "Beantwortet",
  abgeschlossen: "Abgeschlossen",
};

export const CONTACT_STATUS_CLASS: Record<ContactLastStatus, string> = {
  offen: "badge badge-warn",
  beantwortet: "badge badge-success",
  abgeschlossen: "badge badge-muted",
};

export function statusLabel(
  value: EmailStatus | DraftStatus | CaseStatus | ContactLastStatus
): string {
  return (
    {
      ...EMAIL_STATUS_LABELS,
      ...DRAFT_STATUS_LABELS,
      ...CASE_STATUS_LABELS,
      ...CONTACT_STATUS_LABELS,
    }[value] ?? value
  );
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("de-DE", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
