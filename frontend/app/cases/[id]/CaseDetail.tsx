"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { errorMessage, useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { DealStageBadge } from "@/components/Badges";
import { EmailListItem } from "@/components/EmailListItem";
import { formatDate } from "@/lib/labels";

export default function CaseDetail({ caseId }: { caseId: string }) {
  const router = useRouter();
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { data: caseData, error, loading, refresh } = useApi(
    useCallback(() => api.getCase(caseId), [caseId]),
    [caseId]
  );

  // Always offered, even on an already-closed case: a human correcting a
  // mistake (moving back out of GEWONNEN/VERLOREN, or between the two)
  // must always be possible - see PATCH /api/cases/{id}/stage.
  async function handleSetStage(stage: "gewonnen" | "verloren") {
    setActionError(null);
    setBusy(true);
    try {
      await api.setCaseStage(caseId, stage);
      refresh();
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <button onClick={() => router.push("/cases")} style={{ marginBottom: 16 }}>
        ← Zurück zu Cases
      </button>

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={false}
        emptyMessage=""
      >
        {caseData && (
          <>
            <div className="row-between" style={{ gap: 12 }}>
              <h1 className="page-title">{caseData.title}</h1>
              <DealStageBadge value={caseData.deal_stage} />
            </div>
            <p className="page-subtitle">
              {caseData.contacts.map((c) => c.name || c.email_address).join(", ") ||
                "Keine Kontakte"}
              {" · "}
              {caseData.emails.length} Mail(s)
              {" · "}
              Stufe seit {formatDate(caseData.deal_stage_changed_at)}
            </p>

            {actionError && (
              <div className="error-box" style={{ marginBottom: 16 }}>
                {actionError}
              </div>
            )}

            <div className="toolbar">
              <button
                className="btn-success"
                disabled={busy || caseData.deal_stage === "gewonnen"}
                onClick={() => handleSetStage("gewonnen")}
              >
                Als Gewonnen markieren
              </button>
              <button
                className="btn-danger"
                disabled={busy || caseData.deal_stage === "verloren"}
                onClick={() => handleSetStage("verloren")}
              >
                Als Verloren markieren
              </button>
            </div>

            <div className="list">
              {caseData.emails.map((email) => (
                <EmailListItem key={email.id} email={email} href={`/inbox/${email.id}`} />
              ))}
            </div>
          </>
        )}
      </AsyncState>
    </div>
  );
}
