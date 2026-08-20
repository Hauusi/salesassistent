"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { EmailListItem } from "@/components/EmailListItem";

export default function CaseDetail({ caseId }: { caseId: string }) {
  const router = useRouter();
  const { data: caseData, error, loading } = useApi(
    useCallback(() => api.getCase(caseId), [caseId]),
    [caseId]
  );

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
            <h1 className="page-title">{caseData.title}</h1>
            <p className="page-subtitle">
              {caseData.contacts.map((c) => c.name || c.email_address).join(", ") ||
                "Keine Kontakte"}
              {" · "}
              {caseData.emails.length} Mail(s)
            </p>

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
