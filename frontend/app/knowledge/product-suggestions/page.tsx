"use client";

import { useCallback, useState } from "react";
import { api, type ProductSuggestion } from "@/lib/api";
import { errorMessage, useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { formatDate } from "@/lib/labels";

export default function ProductSuggestionsPage() {
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data: suggestions, error, loading, refresh } = useApi(
    useCallback(() => api.listProductSuggestions("vorgeschlagen"), []),
    []
  );

  async function handleApprove(suggestion: ProductSuggestion) {
    setActionError(null);
    setBusyId(suggestion.id);
    try {
      await api.approveProductSuggestion(suggestion.id);
      refresh();
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(suggestion: ProductSuggestion) {
    const reason = window.prompt(
      `Vorschlag "${suggestion.sku}" ablehnen — Begründung (optional):`,
      ""
    );
    if (reason === null) return; // Cancelled.
    setActionError(null);
    setBusyId(suggestion.id);
    try {
      await api.rejectProductSuggestion(suggestion.id, reason || undefined);
      refresh();
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <h1 className="page-title">Produktvorschläge</h1>
      <p className="page-subtitle">
        Der Assistent erkennt Artikelnummern mit zugehörigem Artikeltext in eingehenden Mails
        automatisch (z.B. Lieferantenankündigungen neuer Produkte). Jeder Vorschlag muss geprüft
        und freigegeben werden, bevor er in den Produktkatalog übernommen wird.
      </p>

      {actionError && (
        <div className="error-box" style={{ marginBottom: 16 }}>
          {actionError}
        </div>
      )}

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={!suggestions?.length}
        emptyMessage="Keine offenen Produktvorschläge."
      >
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                <th style={{ padding: "8px 10px" }}>Artikelnummer</th>
                <th style={{ padding: "8px 10px" }}>Name</th>
                <th style={{ padding: "8px 10px" }}>Artikeltext</th>
                <th style={{ padding: "8px 10px" }}>Ursprungs-Mail</th>
                <th style={{ padding: "8px 10px" }}></th>
              </tr>
            </thead>
            <tbody>
              {suggestions?.map((s) => (
                <tr key={s.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "8px 10px", fontFamily: "monospace" }}>{s.sku}</td>
                  <td style={{ padding: "8px 10px" }}>{s.name}</td>
                  <td style={{ padding: "8px 10px", maxWidth: 360 }}>
                    <div
                      style={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        display: "-webkit-box",
                        WebkitLineClamp: 3,
                        WebkitBoxOrient: "vertical",
                      }}
                    >
                      {s.description}
                    </div>
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    <a href={`/inbox/${s.email_message.id}`}>
                      {s.email_message.subject || "(kein Betreff)"}
                    </a>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {s.email_message.sender_address} · {formatDate(s.created_at)}
                    </div>
                  </td>
                  <td style={{ padding: "8px 10px", whiteSpace: "nowrap" }}>
                    <button
                      className="btn-primary"
                      disabled={busyId === s.id}
                      onClick={() => handleApprove(s)}
                      style={{ marginRight: 8 }}
                    >
                      Freigeben
                    </button>
                    <button disabled={busyId === s.id} onClick={() => handleReject(s)}>
                      Ablehnen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </AsyncState>
    </div>
  );
}
