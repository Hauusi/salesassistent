"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Product, type ProductInput } from "@/lib/api";
import { errorMessage, useApi } from "@/lib/useApi";
import { AsyncState } from "@/components/AsyncState";
import ProductForm from "./ProductForm";

function useDebounced<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

export default function ProductsPage() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [editing, setEditing] = useState<Product | null | "new">(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const debouncedQuery = useDebounced(query);

  // Filtering happens on the server. The page used to call listProducts()
  // with no arguments and re-filter client-side, which meant the server's
  // 200-row limit silently truncated the catalog *before* the filter ran -
  // so with 201 products the search returned wrong results.
  const { data: products, error, loading, refresh } = useApi(
    useCallback(
      () =>
        api.listProducts({
          q: debouncedQuery || undefined,
          category: category || undefined,
        }),
      [debouncedQuery, category]
    ),
    [debouncedQuery, category]
  );

  // Categories come from an unfiltered call, so the dropdown does not
  // shrink to whatever the current filter happens to match.
  const { data: allProducts } = useApi(useCallback(() => api.listProducts(), []), []);
  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const p of allProducts ?? []) if (p.category) set.add(p.category);
    return Array.from(set).sort();
  }, [allProducts]);

  async function handleCreate(payload: ProductInput) {
    await api.createProduct(payload);
    setEditing(null);
    refresh();
  }

  async function handleUpdate(id: string, payload: ProductInput) {
    await api.updateProduct(id, payload);
    setEditing(null);
    refresh();
  }

  async function handleDelete(product: Product) {
    if (!confirm(`"${product.name}" wirklich löschen?`)) return;
    setActionError(null);
    try {
      await api.deleteProduct(product.id);
      refresh();
    } catch (cause) {
      setActionError(errorMessage(cause));
    }
  }

  async function handleCsvSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true);
    setImportMessage(null);
    setActionError(null);
    try {
      const result = await api.importProductsCsv(file);
      const summary =
        `Import abgeschlossen: ${result.created} neu, ${result.updated} aktualisiert, ` +
        `${result.skipped} übersprungen`;
      setImportMessage(
        result.errors.length
          ? `${summary} — ${result.errors.length} Hinweis(e): ${result.errors.join(" / ")}`
          : `${summary}.`
      );
      refresh();
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  return (
    <div>
      <h1 className="page-title">Produkte</h1>
      <p className="page-subtitle">
        Produktkatalog als Wissensbasis für den Assistenten — bei Angebotsanfragen werden
        passende Produkte automatisch als Kontext für den Antwortentwurf herangezogen.
      </p>

      <div className="toolbar">
        <input
          type="text"
          placeholder="Suche nach Name, Beschreibung, Kategorie, SKU…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ minWidth: 280 }}
        />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">Alle Kategorien</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <button className="btn-primary" onClick={() => setEditing("new")}>
          + Neues Produkt
        </button>
        <button onClick={() => fileInputRef.current?.click()} disabled={importing}>
          {importing ? "Importiere…" : "CSV importieren"}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv"
          onChange={handleCsvSelected}
          style={{ display: "none" }}
        />
      </div>

      {actionError && (
        <div className="error-box" style={{ marginBottom: 16 }}>
          {actionError}
        </div>
      )}
      {importMessage && (
        <div className="card" style={{ marginBottom: 16 }}>
          {importMessage}
        </div>
      )}

      <p className="muted" style={{ fontSize: 13, marginTop: -8, marginBottom: 16 }}>
        CSV-Spalten: <code>name</code> (Pflicht), <code>description</code>, <code>category</code>,{" "}
        <code>sku</code>, <code>price</code>, <code>currency</code>, <code>availability</code>,{" "}
        <code>specs</code> (JSON-Objekt als Text). Zeilen mit vorhandener <code>sku</code> aktualisieren
        bestehende Produkte statt Duplikate anzulegen.
      </p>

      {editing === "new" && (
        <ProductForm initial={null} onCancel={() => setEditing(null)} onSubmit={handleCreate} />
      )}
      {editing && editing !== "new" && (
        <ProductForm
          initial={editing}
          onCancel={() => setEditing(null)}
          onSubmit={(payload) => handleUpdate(editing.id, payload)}
        />
      )}

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={!products?.length}
        emptyMessage="Keine Produkte gefunden."
      >
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                <th style={{ padding: "8px 10px" }}>Name</th>
                <th style={{ padding: "8px 10px" }}>Kategorie</th>
                <th style={{ padding: "8px 10px" }}>SKU</th>
                <th style={{ padding: "8px 10px" }}>Preis</th>
                <th style={{ padding: "8px 10px" }}>Verfügbarkeit</th>
                <th style={{ padding: "8px 10px" }}></th>
              </tr>
            </thead>
            <tbody>
              {products?.map((p) => (
                <tr key={p.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "8px 10px" }}>
                    <div className="subject">{p.name}</div>
                    {p.description && (
                      <div className="snippet" style={{ maxWidth: 360 }}>
                        {p.description}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: "8px 10px" }}>{p.category ?? "–"}</td>
                  <td style={{ padding: "8px 10px" }}>{p.sku ?? "–"}</td>
                  <td style={{ padding: "8px 10px", whiteSpace: "nowrap" }}>
                    {p.price ? `${p.price} ${p.currency}` : "–"}
                  </td>
                  <td style={{ padding: "8px 10px" }}>{p.availability ?? "–"}</td>
                  <td style={{ padding: "8px 10px", whiteSpace: "nowrap" }}>
                    <button onClick={() => setEditing(p)} style={{ marginRight: 8 }}>
                      Bearbeiten
                    </button>
                    <button className="btn-danger" onClick={() => handleDelete(p)}>
                      Löschen
                    </button>
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
