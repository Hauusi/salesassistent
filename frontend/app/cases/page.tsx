"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type CaseListItem } from "@/lib/api";
import { formatDate } from "@/lib/labels";

export default function CasesPage() {
  const [cases, setCases] = useState<CaseListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listCases().then(setCases).catch((e: ApiError) => setError(e.message));
  }, []);

  return (
    <div>
      <h1 className="page-title">Cases</h1>
      <p className="page-subtitle">
        Themen/Verläufe, denen eingehende Mails automatisch zugeordnet werden.
      </p>

      {error && <div className="error-box">Fehler beim Laden: {error}</div>}
      {cases === null && !error && <p className="muted">Lade…</p>}
      {cases !== null && cases.length === 0 && (
        <div className="empty-state">Noch keine Cases vorhanden.</div>
      )}

      <div className="list">
        {cases?.map((c) => (
          <a key={c.id} className="list-item" href={`/cases/${c.id}`}>
            <div className="row-between">
              <div>
                <div className="subject">{c.title}</div>
                <div className="snippet">
                  {c.contacts.map((ct) => ct.name || ct.email_address).join(", ") || "Keine Kontakte"}
                  {" · "}
                  {c.email_count} Mail(s)
                </div>
              </div>
              <div className="muted" style={{ fontSize: 13, whiteSpace: "nowrap" }}>
                {formatDate(c.created_at)}
              </div>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
