"use client";

import type { EmailListEntry } from "@/lib/api";
import { formatDate } from "@/lib/labels";
import { StatusBadge, TypBadge, WichtigkeitBadge } from "./Badges";

interface Props {
  email: EmailListEntry;
  href: string;
  /** Optional line under the subject; defaults to sender (+ case). */
  subtitle?: string;
  /** Prefix for the sender line, e.g. "An: " in the drafts list. */
  senderPrefix?: string;
  /** Overrides the subject, e.g. a draft's own subject. */
  title?: string | null;
  /** The date to show; defaults to the mail's received_at. */
  timestamp?: string;
  showStatus?: boolean;
}

/**
 * One row in a mail list.
 *
 * The inbox, the drafts list, the case detail and the knowledge base each
 * had their own copy of this markup - four places to update for one visual
 * change, and they had already diverged on which badges they showed.
 */
export function EmailListItem({
  email,
  href,
  subtitle,
  senderPrefix = "",
  title,
  timestamp,
  showStatus = true,
}: Props) {
  const sender = email.sender_name || email.sender_address;
  const caseSuffix = email.case ? ` · Case: ${email.case.title}` : "";

  return (
    <a className="list-item" href={href}>
      <div className="row-between">
        <div>
          <div className="row">
            <WichtigkeitBadge value={email.wichtigkeits_kategorie} />
            <TypBadge value={email.typ} />
            {showStatus && <StatusBadge value={email.status} />}
          </div>
          <div className="subject" style={{ marginTop: 6 }}>
            {title ?? email.subject ?? "(kein Betreff)"}
          </div>
          <div className="snippet">
            {subtitle ?? `${senderPrefix}${sender}${caseSuffix}`}
          </div>
        </div>
        <div className="muted" style={{ fontSize: 13, whiteSpace: "nowrap" }}>
          {formatDate(timestamp ?? email.received_at)}
        </div>
      </div>
    </a>
  );
}
