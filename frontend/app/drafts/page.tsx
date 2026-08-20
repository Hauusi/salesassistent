"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { EmailListItem } from "@/components/EmailListItem";

export default function DraftsPage() {
  const { data: drafts, error, loading } = useApi(
    useCallback(() => api.listDrafts("entwurf"), []),
    []
  );

  return (
    <div>
      <h1 className="page-title">Freigaben</h1>
      <p className="page-subtitle">
        Vom Assistenten erstellte Antwortentwürfe, die auf Freigabe warten.
        Versand erfolgt erst nach expliziter Freigabe.
      </p>

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={!drafts?.length}
        emptyMessage="Keine offenen Entwürfe. 🎉"
      >
        <div className="list">
          {drafts?.map((draft) => (
            <EmailListItem
              key={draft.id}
              email={draft.email_message}
              href={`/drafts/${draft.id}`}
              title={draft.subject || draft.email_message.subject}
              senderPrefix="An: "
              timestamp={draft.created_at}
              showStatus={false}
            />
          ))}
        </div>
      </AsyncState>
    </div>
  );
}
