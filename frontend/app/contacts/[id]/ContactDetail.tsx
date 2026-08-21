"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import { EmailListItem } from "@/components/EmailListItem";
import { ContactStatusBadge, FollowUpBadge } from "@/components/Badges";
import { formatDate } from "@/lib/labels";

export default function ContactDetail({ contactId }: { contactId: string }) {
  const router = useRouter();
  const { data: contact, error, loading } = useApi(
    useCallback(() => api.getContact(contactId), [contactId]),
    [contactId]
  );

  return (
    <div>
      <button onClick={() => router.push("/contacts")} style={{ marginBottom: 16 }}>
        ← Zurück zu Kontakten
      </button>

      <AsyncState loading={loading} error={error} isEmpty={false} emptyMessage="">
        {contact && (
          <>
            <h1 className="page-title">{contact.name || contact.email_address}</h1>
            <p className="page-subtitle">
              {contact.email_address}
              {contact.company && ` · ${contact.company}`}
            </p>

            <div className="card" style={{ marginBottom: 20 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div className="row">
                  <ContactStatusBadge value={contact.last_status} />
                  <FollowUpBadge show={contact.needs_followup} />
                </div>
                <div className="muted" style={{ fontSize: 13 }}>
                  {contact.total_inquiries} Anfrage(n) insgesamt
                  {contact.last_contact_at &&
                    ` · Letzter Kontakt: ${formatDate(contact.last_contact_at)}`}
                </div>
              </div>
            </div>

            <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>Historie</h2>
            <div className="list">
              {contact.emails.map((email) => (
                <EmailListItem key={email.id} email={email} href={`/inbox/${email.id}`} />
              ))}
            </div>
          </>
        )}
      </AsyncState>
    </div>
  );
}
