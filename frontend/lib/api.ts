export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore - keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  return handleResponse<T>(res);
}

/** For multipart/form-data uploads - the browser sets the boundary itself,
 * so no Content-Type header must be set explicitly here. */
async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    body: formData,
    cache: "no-store",
  });
  return handleResponse<T>(res);
}

export interface Contact {
  id: string;
  email_address: string;
  name: string | null;
  company: string | null;
}

export interface Case {
  id: string;
  title: string;
  summary: string | null;
  status: string;
  created_at: string;
}

export interface CaseListItem extends Case {
  contacts: Contact[];
  email_count: number;
}

export interface CaseDetail extends CaseListItem {
  emails: EmailMessage[];
}

export type Wichtigkeit =
  | "antwort_erforderlich"
  | "information"
  | "newsletter"
  | "spam_verdacht";

export type Typ = "bestellung" | "anfrage" | "keiner";

export interface EmailMessage {
  id: string;
  subject: string | null;
  sender_address: string;
  sender_name: string | null;
  raw_content: string;
  snippet: string | null;
  received_at: string;
  wichtigkeits_kategorie: Wichtigkeit | null;
  typ: Typ;
  status: string;
  classification_confidence: number | null;
  classification_reasoning: string | null;
  contact: Contact | null;
  case: Case | null;
}

export interface EmailSummary {
  id: string;
  subject: string | null;
  sender_address: string;
  sender_name: string | null;
  snippet: string | null;
  received_at: string;
  wichtigkeits_kategorie: Wichtigkeit | null;
  typ: Typ;
  status: string;
  contact: Contact | null;
  case: Case | null;
}

export interface Draft {
  id: string;
  email_message_id: string;
  subject: string | null;
  body: string;
  status: "entwurf" | "freigegeben" | "abgelehnt" | "versendet";
  rag_context_summary: string | null;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
  email_message: EmailSummary;
}

export interface Mailbox {
  id: string;
  email_address: string;
  is_active: boolean;
  last_synced_at: string | null;
  created_at: string;
}

export interface Product {
  id: string;
  name: string;
  description: string | null;
  category: string | null;
  sku: string | null;
  price: string | null;
  currency: string;
  availability: string | null;
  specs: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProductInput {
  name: string;
  description: string | null;
  category: string | null;
  sku: string | null;
  price: number | null;
  currency: string;
  availability: string | null;
  specs: Record<string, unknown>;
}

export interface ProductImportResult {
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
}

export const api = {
  listDrafts: (status?: string) =>
    request<Draft[]>(`/api/drafts${status ? `?status=${status}` : ""}`),
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
    wichtigkeit?: string;
    typ?: string;
    status?: string;
    include_spam?: boolean;
  }) => {
    const search = new URLSearchParams();
    if (params?.wichtigkeit) search.set("wichtigkeit", params.wichtigkeit);
    if (params?.typ) search.set("typ", params.typ);
    if (params?.status) search.set("status", params.status);
    if (params?.include_spam) search.set("include_spam", "true");
    const qs = search.toString();
    return request<EmailMessage[]>(`/api/emails${qs ? `?${qs}` : ""}`);
  },
  getEmail: (id: string) => request<EmailMessage>(`/api/emails/${id}`),

  listCases: () => request<CaseListItem[]>("/api/cases"),
  getCase: (id: string) => request<CaseDetail>(`/api/cases/${id}`),

  searchKnowledge: (q?: string) =>
    request<EmailMessage[]>(
      `/api/knowledge/search${q ? `?q=${encodeURIComponent(q)}` : ""}`
    ),

  listMailboxes: () => request<Mailbox[]>("/api/mailboxes"),
  pollMailboxNow: (id: string) =>
    request<{ job_id: string }>(`/api/mailboxes/${id}/poll-now`, {
      method: "POST",
    }),

  listProducts: (params?: { q?: string; category?: string }) => {
    const search = new URLSearchParams();
    if (params?.q) search.set("q", params.q);
    if (params?.category) search.set("category", params.category);
    const qs = search.toString();
    return request<Product[]>(`/api/knowledge/products${qs ? `?${qs}` : ""}`);
  },
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
    return requestForm<ProductImportResult>("/api/knowledge/products/import", formData);
  },
};
