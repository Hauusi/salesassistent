"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, type Draft } from "@/lib/api";
import { WichtigkeitBadge, TypBadge, StatusBadge } from "@/components/Badges";
import { formatDate } from "@/lib/labels";

export default function DraftDetail({ draftId }: { draftId: string }) {
  const router = useRouter();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "approve" | "reject" | null>(null);
  const [savedNotice, setSavedNotice] = useState(false);

  useEffect(() => {
    api
      .getDraft(draftId)
      .then((d) => {
        setDraft(d);
        setSubject(d.subject ?? "");
        setBody(d.body);
      })
      .catch((e: ApiError) => setError(e.message));
  }, [draftId]);

  async function handleSave() {
    setBusy("save");
    setError(null);
    try {
      const updated = await api.updateDraft(draftId, subject, body);
      setDraft(updated);
      setSavedNotice(true);
      setTimeout(() => setSavedNotice(false), 2000);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(null);
    }
  }

  async function handleApprove() {
    if (!confirm("Diese Antwort jetzt freigeben und über Gmail versenden?")) return;
    setBusy("approve");
    setError(null);
    try {
      await handleSave();
      const updated = await api.approveDraft(draftId);
      setDraft(updated);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(null);
    }
  }

  async function handleReject() {
    const reason = prompt("Grund für die Ablehnung (optional):") ?? undefined;
    setBusy("reject");
    setError(null);
    try {
      const updated = await api.rejectDraft(draftId, reason);
      setDraft(updated);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(null);
    }
  }

  if (error && !draft) return <div className="error-box">Fehler: {error}</div>;
  if (!draft) return <p className="muted">Lade…</p>;

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
        Von {email.sender_name ? `${email.sender_name} <${email.sender_address}>` : email.sender_address}
        {" · "}
        {formatDate(email.received_at)}
        {email.case ? ` · Case: ${email.case.title}` : ""}
      </p>

      <div className="card" style={{ marginBottom: 20 }}>
        <label>Ursprüngliche Mail</label>
        <div className="quote-block">{email.snippet}</div>
      </div>

      {error && <div className="error-box" style={{ marginBottom: 16 }}>{error}</div>}

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
          {draft.status === "versendet" && `Versendet am ${draft.sent_at ? formatDate(draft.sent_at) : ""}.`}
          {draft.status === "abgelehnt" && "Dieser Entwurf wurde abgelehnt."}
        </p>
      )}
    </div>
  );
}
