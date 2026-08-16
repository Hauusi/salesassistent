"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, type CaseDetail as CaseDetailType } from "@/lib/api";
import { WichtigkeitBadge, TypBadge, StatusBadge } from "@/components/Badges";
import { formatDate } from "@/lib/labels";

export default function CaseDetail({ caseId }: { caseId: string }) {
  const router = useRouter();
  const [caseData, setCaseData] = useState<CaseDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getCase(caseId).then(setCaseData).catch((e: ApiError) => setError(e.message));
  }, [caseId]);

  if (error) return <div className="error-box">Fehler: {error}</div>;
  if (!caseData) return <p className="muted">Lade…</p>;

  return (
    <div>
      <button onClick={() => router.push("/cases")} style={{ marginBottom: 16 }}>
        ← Zurück zu Cases
      </button>

      <h1 className="page-title">{caseData.title}</h1>
      <p className="page-subtitle">
        {caseData.contacts.map((c) => c.name || c.email_address).join(", ") || "Keine Kontakte"}
        {" · "}
        {caseData.emails.length} Mail(s)
      </p>

      <div className="list">
        {caseData.emails.map((email) => (
          <a key={email.id} className="list-item" href={`/inbox/${email.id}`}>
            <div className="row-between">
              <div>
                <div className="row">
                  <WichtigkeitBadge value={email.wichtigkeits_kategorie} />
                  <TypBadge value={email.typ} />
                  <StatusBadge value={email.status} />
                </div>
                <div className="subject" style={{ marginTop: 6 }}>
                  {email.subject || "(kein Betreff)"}
                </div>
                <div className="snippet">{email.sender_name || email.sender_address}</div>
              </div>
              <div className="muted" style={{ fontSize: 13, whiteSpace: "nowrap" }}>
                {formatDate(email.received_at)}
              </div>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
