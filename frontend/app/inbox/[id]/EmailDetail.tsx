"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { WichtigkeitBadge, TypBadge, StatusBadge } from "@/components/Badges";
import { formatDate } from "@/lib/labels";

export default function EmailDetail({ emailId }: { emailId: string }) {
  const router = useRouter();
  const { data: email, error, loading } = useApi(
    useCallback(() => api.getEmail(emailId), [emailId]),
    [emailId]
  );

  return (
    <div>
      <button onClick={() => router.back()} style={{ marginBottom: 16 }}>
        ← Zurück
      </button>

      <AsyncState loading={loading} error={error} isEmpty={false} emptyMessage="">
        {email && (
          <>
            <div className="row" style={{ marginBottom: 8 }}>
              <WichtigkeitBadge value={email.wichtigkeits_kategorie} />
              <TypBadge value={email.typ} />
              <StatusBadge value={email.status} />
            </div>

            <h1 className="page-title">{email.subject || "(kein Betreff)"}</h1>
            <p className="page-subtitle">
              Von{" "}
              {email.sender_name
                ? `${email.sender_name} <${email.sender_address}>`
                : email.sender_address}
              {" · "}
              {formatDate(email.received_at)}
              {email.case ? ` · Case: ${email.case.title}` : ""}
            </p>

            {email.classification_reasoning && (
              <div className="card" style={{ marginBottom: 16 }}>
                <label>Klassifikations-Begründung</label>
                <p style={{ margin: 0 }}>{email.classification_reasoning}</p>
                {email.classification_confidence !== null && (
                  <p className="muted" style={{ fontSize: 13, marginTop: 6 }}>
                    Konfidenz: {Math.round(email.classification_confidence * 100)}%
                  </p>
                )}
              </div>
            )}

            <div className="card">
              <label>Inhalt</label>
              <div className="quote-block" style={{ maxHeight: "none" }}>
                {email.raw_content}
              </div>
            </div>
          </>
        )}
      </AsyncState>
    </div>
  );
}
