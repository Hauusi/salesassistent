"use client";

import { useCallback, useState } from "react";
import { api, type Wichtigkeit } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { EmailListItem } from "@/components/EmailListItem";
import { WICHTIGKEIT_LABELS } from "@/lib/labels";

export default function InboxPage() {
  // Typed as the union rather than string: the value comes from a <select>
  // whose options are built from WICHTIGKEIT_LABELS, so it can only ever be
  // one of these. This is what the `as Wichtigkeit` cast further down used
  // to paper over.
  const [wichtigkeit, setWichtigkeit] = useState<Wichtigkeit | "">("");
  const [includeSpam, setIncludeSpam] = useState(false);

  const { data: emails, error, loading } = useApi(
    useCallback(
      () =>
        api.listEmails({
          wichtigkeit: wichtigkeit || undefined,
          include_spam: includeSpam,
        }),
      [wichtigkeit, includeSpam]
    ),
    [wichtigkeit, includeSpam]
  );

  return (
    <div>
      <h1 className="page-title">Posteingang</h1>
      <p className="page-subtitle">
        Alle klassifizierten Mails. Spam-Verdacht ist standardmäßig ausgeblendet
        (nicht gelöscht).
      </p>

      <div className="toolbar">
        <select
          value={wichtigkeit}
          onChange={(e) => setWichtigkeit(e.target.value as Wichtigkeit | "")}
        >
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

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={!emails?.length}
        emptyMessage="Keine Mails in dieser Ansicht."
      >
        <div className="list">
          {emails?.map((email) => (
            <EmailListItem key={email.id} email={email} href={`/inbox/${email.id}`} />
          ))}
        </div>
      </AsyncState>
    </div>
  );
}
