"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { ContactStatusBadge, FollowUpBadge } from "@/components/Badges";
import { formatDate } from "@/lib/labels";

type SortDirection = "asc" | "desc";

export default function ContactsPage() {
  const router = useRouter();
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");

  const { data: contacts, error, loading } = useApi(
    useCallback(() => api.listContacts(), []),
    []
  );

  const sorted = useMemo(() => {
    if (!contacts) return contacts;
    // Contacts with no prior mail have no last_contact_at - keep them at
    // the end regardless of sort direction, since neither "newest" nor
    // "oldest" means anything for them.
    const withDate = contacts.filter((c) => c.last_contact_at);
    const withoutDate = contacts.filter((c) => !c.last_contact_at);
    withDate.sort((a, b) => {
      const diff = new Date(a.last_contact_at!).getTime() - new Date(b.last_contact_at!).getTime();
      return sortDirection === "asc" ? diff : -diff;
    });
    return [...withDate, ...withoutDate];
  }, [contacts, sortDirection]);

  function toggleSort() {
    setSortDirection((d) => (d === "desc" ? "asc" : "desc"));
  }

  return (
    <div>
      <h1 className="page-title">Kontakte</h1>
      <p className="page-subtitle">
        Alle Kontakte mit ihrer Anfragen-Historie. Kontakte, deren letzte Anfrage über dem
        konfigurierten Follow-up-Zeitraum offen ist, sind als „Follow-up nötig“ markiert.
      </p>

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={!sorted?.length}
        emptyMessage="Noch keine Kontakte vorhanden."
      >
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                <th style={{ padding: "8px 10px" }}>Name</th>
                <th style={{ padding: "8px 10px" }}>Mail</th>
                <th style={{ padding: "8px 10px" }}>Anfragen gesamt</th>
                <th style={{ padding: "8px 10px" }}>Status</th>
                <th style={{ padding: "8px 10px" }}></th>
                <th
                  style={{ padding: "8px 10px", cursor: "pointer", userSelect: "none" }}
                  onClick={toggleSort}
                  title="Nach letztem Kontakt sortieren"
                >
                  Letzter Kontakt {sortDirection === "desc" ? "↓" : "↑"}
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted?.map((c) => (
                <tr
                  key={c.id}
                  style={{ borderBottom: "1px solid var(--border)", cursor: "pointer" }}
                  onClick={() => router.push(`/contacts/${c.id}`)}
                >
                  <td style={{ padding: "8px 10px" }}>
                    <div className="subject">{c.name || "–"}</div>
                    {c.company && <div className="snippet">{c.company}</div>}
                  </td>
                  <td style={{ padding: "8px 10px" }}>{c.email_address}</td>
                  <td style={{ padding: "8px 10px" }}>{c.total_inquiries}</td>
                  <td style={{ padding: "8px 10px" }}>
                    <ContactStatusBadge value={c.last_status} />
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    <FollowUpBadge show={c.needs_followup} />
                  </td>
                  <td style={{ padding: "8px 10px", whiteSpace: "nowrap" }}>
                    {c.last_contact_at ? formatDate(c.last_contact_at) : "–"}
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
