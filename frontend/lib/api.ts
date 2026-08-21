/**
 * Typed client for the backend API.
 *
 * The domain types below are *derived* from lib/api-schema.ts, which is
 * generated from the backend's own OpenAPI document
 * (`npm run generate:api-types`). They used to be hand-written copies of
 * the Pydantic schemas, which had already drifted: the backend declared
 * `wichtigkeits_kategorie` as a plain string while this file claimed a
 * union, and the inbox page needed an `as Wichtigkeit` cast to bridge the
 * gap. Deriving them means a backend field that changes shape breaks the
 * build here instead of at runtime.
 */
import type { components } from "./api-schema";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Schemas = components["schemas"];

export type Wichtigkeit = Schemas["WichtigkeitsKategorie"];
export type Typ = Schemas["TypKategorie"];
export type EmailStatus = Schemas["EmailStatus"];
export type DraftStatus = Schemas["DraftStatus"];
export type CaseStatus = Schemas["CaseStatus"];

export type Contact = Schemas["ContactOut"];
export type ContactLastStatus = Schemas["ContactLastStatus"];
export type ContactListItem = Schemas["ContactListItemOut"];
export type ContactDetail = Schemas["ContactDetailOut"];
export type Case = Schemas["CaseOut"];
export type CaseListItem = Schemas["CaseListItemOut"];
export type CaseDetail = Schemas["CaseDetailOut"];
export type EmailMessage = Schemas["EmailOut"];
export type EmailSummary = Schemas["EmailSummaryOut"];
export type Draft = Schemas["DraftOut"];
export type Mailbox = Schemas["MailboxOut"];
export type Product = Schemas["ProductOut"];
export type ProductInput = Schemas["ProductCreateIn"];
export type ProductImportResult = Schemas["ProductImportResult"];
export type PollTrigger = Schemas["PollTriggerOut"];

/** The fields every list view renders, for either shape of email payload. */
export type EmailListEntry = EmailMessage | EmailSummary;

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      // Not JSON, or an empty body - keep statusText.
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch (cause) {
    // fetch rejects on network failure, DNS, CORS and abort. Without this
    // the UI surfaced a bare "Failed to fetch" with no indication that the
    // backend simply is not running.
    throw new ApiError(
      0,
      `Backend unter ${API_BASE_URL} nicht erreichbar (${
        cause instanceof Error ? cause.message : "unbekannter Fehler"
      }).`
    );
  }
  return handleResponse<T>(res);
}

/** For multipart/form-data uploads - the browser sets the boundary itself,
 * so no Content-Type header must be set explicitly here. */
async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      body: formData,
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(
      0,
      `Backend unter ${API_BASE_URL} nicht erreichbar (${
        cause instanceof Error ? cause.message : "unbekannter Fehler"
      }).`
    );
  }
  return handleResponse<T>(res);
}

function queryString(params: Record<string, string | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export const api = {
  listDrafts: (status?: DraftStatus) =>
    request<Draft[]>(`/api/drafts${queryString({ status })}`),
  getDraft: (id: string) => request<Draft>(`/api/drafts/${id}`),
  updateDraft: (id: string, subject: string | null, body: string) =>
    request<Draft>(`/api/drafts/${id}`, {
      method: "PUT",
      body: JSON.stringify({ subject, body }),
    }),
  approveDraft: (id: string) =>
    request<Draft>(`/api/drafts/${id}/approve`, { method: "POST" }),
  rejectDraft: (id: string, reason?: string) =>
    request<Draft>(`/api/drafts/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? null }),
    }),

  listEmails: (params?: {
    wichtigkeit?: Wichtigkeit;
    typ?: Typ;
    status?: EmailStatus;
    include_spam?: boolean;
  }) => request<EmailMessage[]>(`/api/emails${queryString({ ...params })}`),
  getEmail: (id: string) => request<EmailMessage>(`/api/emails/${id}`),

  listCases: () => request<CaseListItem[]>("/api/cases"),
  getCase: (id: string) => request<CaseDetail>(`/api/cases/${id}`),

  listContacts: (params?: { followup_days?: number }) =>
    request<ContactListItem[]>(
      `/api/contacts${queryString({
        followup_days: params?.followup_days?.toString(),
      })}`
    ),
  getContact: (id: string, params?: { followup_days?: number }) =>
    request<ContactDetail>(
      `/api/contacts/${id}${queryString({
        followup_days: params?.followup_days?.toString(),
      })}`
    ),

  searchKnowledge: (q?: string) =>
    request<EmailMessage[]>(`/api/knowledge/search${queryString({ q })}`),

  listMailboxes: () => request<Mailbox[]>("/api/mailboxes"),
  pollMailboxNow: (id: string) =>
    request<PollTrigger>(`/api/mailboxes/${id}/poll-now`, { method: "POST" }),

  listProducts: (params?: { q?: string; category?: string }) =>
    request<Product[]>(`/api/knowledge/products${queryString({ ...params })}`),
  createProduct: (payload: ProductInput) =>
    request<Product>("/api/knowledge/products", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateProduct: (id: string, payload: Partial<ProductInput>) =>
    request<Product>(`/api/knowledge/products/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteProduct: (id: string) =>
    request<void>(`/api/knowledge/products/${id}`, { method: "DELETE" }),
  importProductsCsv: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return requestForm<ProductImportResult>(
      "/api/knowledge/products/import",
      formData
    );
  },
};
