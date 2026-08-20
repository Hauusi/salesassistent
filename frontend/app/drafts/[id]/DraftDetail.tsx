"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type Draft } from "@/lib/api";
import { errorMessage, useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { WichtigkeitBadge, TypBadge, StatusBadge } from "@/components/Badges";
import { formatDate } from "@/lib/labels";

export default function DraftDetail({ draftId }: { draftId: string }) {
  const router = useRouter();
  const { data: loaded, error: loadError, loading } = useApi(
    useCallback(() => api.getDraft(draftId), [draftId]),
    [draftId]
  );

  const [draft, setDraft] = useState<Draft | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "approve" | "reject" | null>(null);
  const [savedNotice, setSavedNotice] = useState(false);

  // Seed the editable fields from the loaded draft, using React's
  // documented "adjust state when a prop changes" pattern rather than an
  // effect: an effect here would render once with empty inputs and then
  // again with the real text, which is a visible flash on every open.
  const [seededFrom, setSeededFrom] = useState<Draft | null>(null);
  if (loaded && loaded !== seededFrom) {
    setSeededFrom(loaded);
    setDraft(loaded);
    setSubject(loaded.subject ?? "");
    setBody(loaded.body);
  }

  /** Saves and returns the stored draft, or throws. */
  async function save(): Promise<Draft> {
    const updated = await api.updateDraft(draftId, subject, body);
    setDraft(updated);
    return updated;
  }

  async function handleSave() {
    setBusy("save");
    setActionError(null);
    try {
      await save();
      setSavedNotice(true);
      setTimeout(() => setSavedNotice(false), 2000);
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  async function handleApprove() {
    if (!confirm("Diese Antwort jetzt freigeben und über Gmail versenden?")) return;
    setBusy("approve");
    setActionError(null);
    try {
      // Save first, and let a failure here stop the send. Previously this
      // called the error-swallowing handleSave(), so a failed save was
      // invisible and the draft went out with the *old* text - silently
      // sending a customer something the user had just edited away.
      await save();
      setDraft(await api.approveDraft(draftId));
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  async function handleReject() {
    const reason = prompt("Grund für die Ablehnung (optional):") ?? undefined;
    setBusy("reject");
    setActionError(null);
    try {
      setDraft(await api.rejectDraft(draftId, reason));
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  if (!draft) {
    return (
      <AsyncState loading={loading} error={loadError} isEmpty={false} emptyMessage="">
        <p className="muted">Lade…</p>
      </AsyncState>
    );
  }

  const email = draft.email_message;
  const editable = draft.status === "entwurf";

  return (
    <div>
      <button onClick={() => router.push("/drafts")} style={{ marginBottom: 16 }}>
        ← Zurück zu Freigaben
      </button>

      <div className="row" style={{ marginBottom: 8 }}>
        <WichtigkeitBadge value={email.wichtigkeits_kategorie} />
        <TypBadge value={email.typ} />
        <StatusBadge value={draft.status} />
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

      <div className="card" style={{ marginBottom: 20 }}>
        <label>Ursprüngliche Mail</label>
        <div className="quote-block">{email.snippet}</div>
      </div>

      {actionError && (
        <div className="error-box" style={{ marginBottom: 16 }}>
          {actionError}
        </div>
      )}

      {draft.rag_context_summary && (
        <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
          Kontext für den Entwurf: {draft.rag_context_summary}
        </p>
      )}

      <div className="field">
        <label htmlFor="subject">Betreff</label>
        <input
          id="subject"
          type="text"
          value={subject}
          disabled={!editable}
          onChange={(e) => setSubject(e.target.value)}
        />
      </div>

      <div className="field">
        <label htmlFor="body">Antworttext</label>
        <textarea
          id="body"
          value={body}
          disabled={!editable}
          onChange={(e) => setBody(e.target.value)}
        />
      </div>

      {editable ? (
        <div className="toolbar" style={{ marginTop: 20 }}>
          <button onClick={handleSave} disabled={busy !== null}>
            {savedNotice ? "Gespeichert ✓" : "Änderungen speichern"}
          </button>
          <button className="btn-primary" onClick={handleApprove} disabled={busy !== null}>
            {busy === "approve" ? "Wird versendet…" : "Freigeben & senden"}
          </button>
          <button className="btn-danger" onClick={handleReject} disabled={busy !== null}>
            Ablehnen
          </button>
        </div>
      ) : (
        <p className="muted" style={{ marginTop: 20 }}>
          {draft.status === "versendet" &&
            `Versendet am ${draft.sent_at ? formatDate(draft.sent_at) : ""}.`}
          {draft.status === "abgelehnt" && "Dieser Entwurf wurde abgelehnt."}
          {draft.status === "freigegeben" &&
            "Freigegeben - der Versand läuft. Falls dieser Status bestehen bleibt, ist der Versand abgebrochen; bitte prüfen."}
        </p>
      )}
    </div>
  );
}
