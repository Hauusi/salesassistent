"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type EmailMessage } from "@/lib/api";
import { formatDate } from "@/lib/labels";

type GroupBy = "case" | "contact";

export default function KnowledgePage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<EmailMessage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState<GroupBy>("case");

  useEffect(() => {
    const handle = setTimeout(() => {
      api
        .searchKnowledge(query || undefined)
        .then(setResults)
        .catch((e: ApiError) => setError(e.message));
    }, 250);
    return () => clearTimeout(handle);
  }, [query]);

  const groups = useMemo(() => {
    if (!results) return [];
    const map = new Map<string, { label: string; items: EmailMessage[] }>();
    for (const email of results) {
      const key =
        groupBy === "case"
          ? email.case?.id ?? "none"
          : email.contact?.id ?? "none";
      const label =
        groupBy === "case"
          ? email.case?.title ?? "(kein Case)"
          : email.contact?.name || email.contact?.email_address || "(unbekannter Kontakt)";
      if (!map.has(key)) map.set(key, { label, items: [] });
      map.get(key)!.items.push(email);
    }
    return Array.from(map.values());
  }, [results, groupBy]);

  return (
    <div>
      <h1 className="page-title">Wissensbasis</h1>
      <p className="page-subtitle">
        Durchsuchbare Ablage aller als &quot;Information&quot; eingeordneten Mails,
        gruppiert nach Kontakt oder Case.
      </p>

      <div className="toolbar">
        <input
          type="text"
          placeholder="Suche in Betreff, Inhalt, Absender…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ minWidth: 320 }}
        />
        <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupBy)}>
          <option value="case">Gruppieren nach Case</option>
          <option value="contact">Gruppieren nach Kontakt</option>
        </select>
      </div>

      {error && <div className="error-box">Fehler bei der Suche: {error}</div>}
      {results === null && !error && <p className="muted">Lade…</p>}
      {results !== null && results.length === 0 && (
        <div className="empty-state">Keine Treffer.</div>
      )}

      {groups.map((group) => (
        <div key={group.label} style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 14, color: "var(--text-muted)", marginBottom: 8 }}>
            {group.label} ({group.items.length})
          </h3>
          <div className="list">
            {group.items.map((email) => (
              <a key={email.id} className="list-item" href={`/inbox/${email.id}`}>
                <div className="row-between">
                  <div>
                    <div className="subject">{email.subject || "(kein Betreff)"}</div>
                    <div className="snippet">{email.snippet}</div>
                  </div>
                  <div className="muted" style={{ fontSize: 13, whiteSpace: "nowrap" }}>
                    {formatDate(email.received_at)}
                  </div>
                </div>
              </a>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
