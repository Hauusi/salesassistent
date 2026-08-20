"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { formatDate } from "@/lib/labels";

export default function CasesPage() {
  const { data: cases, error, loading } = useApi(useCallback(() => api.listCases(), []), []);

  return (
    <div>
      <h1 className="page-title">Cases</h1>
      <p className="page-subtitle">
        Themen/Verläufe, denen eingehende Mails automatisch zugeordnet werden.
      </p>

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={!cases?.length}
        emptyMessage="Noch keine Cases vorhanden."
      >
        <div className="list">
          {cases?.map((c) => (
            <a key={c.id} className="list-item" href={`/cases/${c.id}`}>
              <div className="row-between">
                <div>
                  <div className="subject">{c.title}</div>
                  <div className="snippet">
                    {c.contacts.map((ct) => ct.name || ct.email_address).join(", ") ||
                      "Keine Kontakte"}
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
      </AsyncState>
    </div>
  );
}
