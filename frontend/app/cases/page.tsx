"use client";

import { useCallback, useMemo, useState } from "react";
import { api, type CaseListItem, type DealStage } from "@/lib/api";
import { useApi, errorMessage } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { DealStageBadge } from "@/components/Badges";
import { DEAL_STAGE_LABELS, DEAL_STAGE_ORDER, formatDate } from "@/lib/labels";

/** Stages a human can still move a case out of - see
 * app/services/case_stage_service.py (GEWONNEN/VERLOREN are terminal). */
const CLOSED_STAGES = new Set<DealStage>(["gewonnen", "verloren"]);

export default function CasesPage() {
  const [stageFilter, setStageFilter] = useState<DealStage | "">("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data: cases, error, loading, refresh } = useApi(
    useCallback(
      () => api.listCases(stageFilter ? { deal_stage: stageFilter } : undefined),
      [stageFilter]
    ),
    [stageFilter]
  );

  // Only relevant when no filter is active - grouping a single-stage list
  // would just recreate the flat list with an extra heading.
  const groups = useMemo(() => {
    if (!cases || stageFilter) return null;
    const byStage = new Map<DealStage, CaseListItem[]>();
    for (const stage of DEAL_STAGE_ORDER) byStage.set(stage, []);
    for (const c of cases) byStage.get(c.deal_stage)?.push(c);
    return DEAL_STAGE_ORDER.map((stage) => ({ stage, items: byStage.get(stage) ?? [] }));
  }, [cases, stageFilter]);

  async function handleSetStage(c: CaseListItem, stage: "gewonnen" | "verloren") {
    setActionError(null);
    setBusyId(c.id);
    try {
      await api.setCaseStage(c.id, stage);
      refresh();
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setBusyId(null);
    }
  }

  function renderCase(c: CaseListItem) {
    return (
      <div key={c.id} className="list-item">
        <div className="row-between">
          <a href={`/cases/${c.id}`} style={{ flex: 1, minWidth: 0 }}>
            <div className="row-between" style={{ gap: 8 }}>
              <div className="subject">{c.title}</div>
              <DealStageBadge value={c.deal_stage} />
            </div>
            <div className="snippet">
              {c.contacts.map((ct) => ct.name || ct.email_address).join(", ") ||
                "Keine Kontakte"}
              {" · "}
              {c.email_count} Mail(s)
              {" · "}
              {formatDate(c.deal_stage_changed_at)}
            </div>
          </a>
          {!CLOSED_STAGES.has(c.deal_stage) && (
            <div style={{ display: "flex", gap: 8, whiteSpace: "nowrap" }}>
              <button
                className="btn-success"
                disabled={busyId === c.id}
                onClick={() => handleSetStage(c, "gewonnen")}
              >
                Gewonnen
              </button>
              <button
                className="btn-danger"
                disabled={busyId === c.id}
                onClick={() => handleSetStage(c, "verloren")}
              >
                Verloren
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="page-title">Cases</h1>
      <p className="page-subtitle">
        Themen/Verläufe, denen eingehende Mails automatisch zugeordnet werden - mit ihrer
        Stufe im Verkaufsprozess.
      </p>

      <div className="toolbar">
        <select
          value={stageFilter}
          onChange={(e) => setStageFilter(e.target.value as DealStage | "")}
        >
          <option value="">Alle Stufen</option>
          {DEAL_STAGE_ORDER.map((stage) => (
            <option key={stage} value={stage}>
              {DEAL_STAGE_LABELS[stage]}
            </option>
          ))}
        </select>
      </div>

      {actionError && (
        <div className="error-box" style={{ marginBottom: 16 }}>
          {actionError}
        </div>
      )}

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={!cases?.length}
        emptyMessage="Noch keine Cases vorhanden."
      >
        {groups ? (
          groups.map(
            (group) =>
              group.items.length > 0 && (
                <div key={group.stage} style={{ marginBottom: 24 }}>
                  <h3 style={{ fontSize: 14, color: "var(--text-muted)", marginBottom: 8 }}>
                    {DEAL_STAGE_LABELS[group.stage]} ({group.items.length})
                  </h3>
                  <div className="list">{group.items.map(renderCase)}</div>
                </div>
              )
          )
        ) : (
          <div className="list">{cases?.map(renderCase)}</div>
        )}
      </AsyncState>
    </div>
  );
}
