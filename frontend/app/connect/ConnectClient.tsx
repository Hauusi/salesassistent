"use client";

import { useEffect, useState } from "react";
import { api, ApiError, API_BASE_URL, type Mailbox } from "@/lib/api";
import { formatDate } from "@/lib/labels";

export default function ConnectClient({
  connected,
  oauthError,
}: {
  connected: string | null;
  oauthError: string | null;
}) {
  const [mailboxes, setMailboxes] = useState<Mailbox[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollingId, setPollingId] = useState<string | null>(null);
  const [pollMessage, setPollMessage] = useState<string | null>(null);

  function refresh() {
    api.listMailboxes().then(setMailboxes).catch((e: ApiError) => setError(e.message));
  }

  useEffect(refresh, []);

  async function handlePollNow(id: string) {
    setPollingId(id);
    setPollMessage(null);
    try {
      await api.pollMailboxNow(id);
      setPollMessage(
        "Abruf gestartet. Neue Mails erscheinen nach kurzer Verarbeitungszeit im Posteingang."
      );
    } catch (e) {
      setError((e as ApiError).message);
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
      {error && <div className="error-box" style={{ marginBottom: 16 }}>{error}</div>}
      {pollMessage && (
        <div className="card" style={{ marginBottom: 16 }}>
          {pollMessage}
        </div>
      )}

      <a className="btn btn-primary" href={`${API_BASE_URL}/api/auth/gmail/connect`}>
        Mit Gmail verbinden
      </a>

      <h2 style={{ fontSize: 16, marginTop: 32, marginBottom: 12 }}>Verbundene Postfächer</h2>
      {mailboxes === null && !error && <p className="muted">Lade…</p>}
      {mailboxes !== null && mailboxes.length === 0 && (
        <p className="muted">Noch kein Postfach verbunden.</p>
      )}
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
    </div>
  );
}
