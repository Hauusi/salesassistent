"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type EmailMessage } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { formatDate } from "@/lib/labels";

type GroupBy = "case" | "contact";

/** Debounces a value, so typing does not fire a request per keystroke. */
function useDebounced<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

export default function KnowledgePage() {
  const [query, setQuery] = useState("");
  const [groupBy, setGroupBy] = useState<GroupBy>("case");
  const debouncedQuery = useDebounced(query);

  const { data: results, error, loading } = useApi(
    useCallback(() => api.searchKnowledge(debouncedQuery || undefined), [debouncedQuery]),
    [debouncedQuery]
  );

  const groups = useMemo(() => {
    if (!results) return [];
    const map = new Map<string, { label: string; items: EmailMessage[] }>();
    for (const email of results) {
      const key =
        groupBy === "case" ? email.case?.id ?? "none" : email.contact?.id ?? "none";
      const label =
        groupBy === "case"
          ? email.case?.title ?? "(kein Case)"
          : email.contact?.name || email.contact?.email_address || "(unbekannter Kontakt)";
      if (!map.has(key)) map.set(key, { label, items: [] });
      map.get(key)!.items.push(email);
    }
    return Array.from(map.entries()).map(([key, group]) => ({ key, ...group }));
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

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={!results?.length}
        emptyMessage="Keine Treffer."
      >
        {groups.map((group) => (
          // Keyed by id, not label: two different cases can share a title,
          // and duplicate React keys drop rows from the render.
          <div key={group.key} style={{ marginBottom: 24 }}>
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
      </AsyncState>
    </div>
  );
}
