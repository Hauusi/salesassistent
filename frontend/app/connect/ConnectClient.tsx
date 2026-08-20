"use client";

import { useCallback, useState } from "react";
import { api, API_BASE_URL } from "@/lib/api";
import { errorMessage, useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { formatDate } from "@/lib/labels";

export default function ConnectClient({
  connected,
  oauthError,
}: {
  connected: string | null;
  oauthError: string | null;
}) {
  const { data: mailboxes, error, loading, refresh } = useApi(
    useCallback(() => api.listMailboxes(), []),
    []
  );
  const [pollingId, setPollingId] = useState<string | null>(null);
  const [pollMessage, setPollMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function handlePollNow(id: string) {
    setPollingId(id);
    setPollMessage(null);
    setActionError(null);
    try {
      const result = await api.pollMailboxNow(id);
      setPollMessage(
        result.status === "already_running"
          ? "Für dieses Postfach läuft bereits ein Abruf. Es wird kein zweiter gestartet."
          : "Abruf gestartet. Neue Mails erscheinen nach kurzer Verarbeitungszeit im Posteingang."
      );
      refresh();
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setPollingId(null);
    }
  }

  return (
    <div>
      <h1 className="page-title">Postfach verbinden</h1>
      <p className="page-subtitle">
        Verbindet ein Gmail-Postfach per OAuth2. Es werden keine Passwörter
        gespeichert.
      </p>

      {connected && (
        <div className="card" style={{ marginBottom: 16, borderColor: "var(--success-text)" }}>
          ✓ Postfach <strong>{connected}</strong> erfolgreich verbunden.
        </div>
      )}
      {oauthError && (
        <div className="error-box" style={{ marginBottom: 16 }}>
          Verbindung fehlgeschlagen: {oauthError}
        </div>
      )}
      {actionError && (
        <div className="error-box" style={{ marginBottom: 16 }}>
          {actionError}
        </div>
      )}
      {pollMessage && (
        <div className="card" style={{ marginBottom: 16 }}>
          {pollMessage}
        </div>
      )}

      <a className="btn btn-primary" href={`${API_BASE_URL}/api/auth/gmail/connect`}>
        Mit Gmail verbinden
      </a>

      <h2 style={{ fontSize: 16, marginTop: 32, marginBottom: 12 }}>Verbundene Postfächer</h2>

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={!mailboxes?.length}
        emptyMessage="Noch kein Postfach verbunden."
      >
        <div className="list">
          {mailboxes?.map((mb) => (
            <div key={mb.id} className="list-item">
              <div className="row-between">
                <div>
                  <div className="subject">{mb.email_address}</div>
                  <div className="snippet">
                    {mb.is_active ? "Aktiv" : "Inaktiv"}
                    {" · "}
                    Zuletzt abgerufen:{" "}
                    {mb.last_synced_at ? formatDate(mb.last_synced_at) : "noch nie"}
                  </div>
                </div>
                <button onClick={() => handlePollNow(mb.id)} disabled={pollingId === mb.id}>
                  {pollingId === mb.id ? "Wird gestartet…" : "Jetzt abrufen"}
                </button>
              </div>
            </div>
          ))}
        </div>
      </AsyncState>
    </div>
  );
}
