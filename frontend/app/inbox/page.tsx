"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type EmailMessage, type Wichtigkeit } from "@/lib/api";
import { WichtigkeitBadge, TypBadge, StatusBadge } from "@/components/Badges";
import { WICHTIGKEIT_LABELS, formatDate } from "@/lib/labels";

export default function InboxPage() {
  const [emails, setEmails] = useState<EmailMessage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wichtigkeit, setWichtigkeit] = useState<string>("");
  const [includeSpam, setIncludeSpam] = useState(false);

  useEffect(() => {
    api
      .listEmails({
        wichtigkeit: wichtigkeit || undefined,
        include_spam: includeSpam,
      })
      .then(setEmails)
      .catch((e: ApiError) => setError(e.message));
  }, [wichtigkeit, includeSpam]);

  return (
    <div>
      <h1 className="page-title">Posteingang</h1>
      <p className="page-subtitle">
        Alle klassifizierten Mails. Spam-Verdacht ist standardmäßig ausgeblendet
        (nicht gelöscht).
      </p>

      <div className="toolbar">
        <select value={wichtigkeit} onChange={(e) => setWichtigkeit(e.target.value)}>
          <option value="">Alle Kategorien</option>
          {Object.entries(WICHTIGKEIT_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 400 }}>
          <input
            type="checkbox"
            checked={includeSpam}
            onChange={(e) => setIncludeSpam(e.target.checked)}
            style={{ width: "auto" }}
          />
          Spam-Verdacht anzeigen
        </label>
      </div>

      {error && <div className="error-box">Fehler beim Laden: {error}</div>}
      {emails === null && !error && <p className="muted">Lade…</p>}
      {emails !== null && emails.length === 0 && (
        <div className="empty-state">Keine Mails in dieser Ansicht.</div>
      )}

      <div className="list">
        {emails?.map((email) => (
          <a key={email.id} className="list-item" href={`/inbox/${email.id}`}>
            <div className="row-between">
              <div>
                <div className="row">
                  <WichtigkeitBadge value={email.wichtigkeits_kategorie as Wichtigkeit} />
                  <TypBadge value={email.typ} />
                  <StatusBadge value={email.status} />
                </div>
                <div className="subject" style={{ marginTop: 6 }}>
                  {email.subject || "(kein Betreff)"}
                </div>
                <div className="snippet">
                  {email.sender_name || email.sender_address}
                  {email.case ? ` · Case: ${email.case.title}` : ""}
                </div>
              </div>
              <div className="muted" style={{ fontSize: 13, whiteSpace: "nowrap" }}>
                {formatDate(email.received_at)}
              </div>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
