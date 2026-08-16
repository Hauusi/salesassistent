"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type Draft } from "@/lib/api";
import { WichtigkeitBadge, TypBadge } from "@/components/Badges";
import { formatDate } from "@/lib/labels";

export default function DraftsPage() {
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listDrafts("entwurf")
      .then(setDrafts)
      .catch((e: ApiError) => setError(e.message));
  }, []);

  return (
    <div>
      <h1 className="page-title">Freigaben</h1>
      <p className="page-subtitle">
        Vom Assistenten erstellte Antwortentwürfe, die auf Freigabe warten.
        Versand erfolgt erst nach expliziter Freigabe.
      </p>

      {error && <div className="error-box">Fehler beim Laden: {error}</div>}

      {drafts === null && !error && <p className="muted">Lade…</p>}

      {drafts !== null && drafts.length === 0 && (
        <div className="empty-state">Keine offenen Entwürfe. 🎉</div>
      )}

      <div className="list">
        {drafts?.map((draft) => (
          <a key={draft.id} className="list-item" href={`/drafts/${draft.id}`}>
            <div className="row-between">
              <div>
                <div className="row">
                  <WichtigkeitBadge value={draft.email_message.wichtigkeits_kategorie} />
                  <TypBadge value={draft.email_message.typ} />
                </div>
                <div className="subject" style={{ marginTop: 6 }}>
                  {draft.subject || draft.email_message.subject || "(kein Betreff)"}
                </div>
                <div className="snippet">
                  An: {draft.email_message.sender_name || draft.email_message.sender_address}
                  {draft.email_message.case ? ` · Case: ${draft.email_message.case.title}` : ""}
                </div>
              </div>
              <div className="muted" style={{ fontSize: 13, whiteSpace: "nowrap" }}>
                {formatDate(draft.created_at)}
              </div>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
