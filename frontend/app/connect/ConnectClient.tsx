"use client";

import { useCallback, useState } from "react";
import { api, API_BASE_URL, type Mailbox } from "@/lib/api";
import { errorMessage, useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { formatDate, formatRelativeTime } from "@/lib/labels";

/** "Letzter Abruf: vor 2 Min. - OK" / "... - fehlgeschlagen: <Grund>" /
 * "Noch nie abgerufen" for a mailbox that has never had a poll attempt
 * (last_poll_at is null - distinct from a poll that ran and succeeded). */
function PollStatus({ mailbox }: { mailbox: Mailbox }) {
  if (!mailbox.last_poll_at) {
    return <span className="muted">Noch nie abgerufen</span>;
  }
  const when = formatRelativeTime(mailbox.last_poll_at);
  if (mailbox.last_poll_status === "error") {
    return (
      <span className="badge badge-danger" title={formatDate(mailbox.last_poll_at)}>
        Letzter Abruf: {when} - fehlgeschlagen
        {mailbox.last_poll_error_message ? `: ${mailbox.last_poll_error_message}` : ""}
      </span>
    );
  }
  return (
    <span className="badge badge-success" title={formatDate(mailbox.last_poll_at)}>
      Letzter Abruf: {when} - OK
    </span>
  );
}

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
  const [deletingId, setDeletingId] = useState<string | null>(null);
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

  async function handleRemove(mb: Mailbox) {
    if (
      !confirm(
        `Postfach "${mb.email_address}" wirklich entfernen? Alle zugehörigen Mails, Entwürfe, ` +
          "Anhänge und Produktvorschläge werden unwiderruflich gelöscht."
      )
    ) {
      return;
    }
    setActionError(null);
    setDeletingId(mb.id);
    try {
      await api.deleteMailbox(mb.id);
      refresh();
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setDeletingId(null);
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
                    Synchronisiert bis:{" "}
                    {mb.last_synced_at ? formatDate(mb.last_synced_at) : "noch nie"}
                  </div>
                  <div style={{ marginTop: 6 }}>
                    <PollStatus mailbox={mb} />
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={() => handlePollNow(mb.id)} disabled={pollingId === mb.id}>
                    {pollingId === mb.id ? "Wird gestartet…" : "Jetzt abrufen"}
                  </button>
                  <button
                    className="btn-danger"
                    onClick={() => handleRemove(mb)}
                    disabled={deletingId === mb.id}
                  >
                    {deletingId === mb.id ? "Wird entfernt…" : "Postfach entfernen"}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </AsyncState>
    </div>
  );
}
