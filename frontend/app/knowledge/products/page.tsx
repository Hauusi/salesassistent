"use client";

import { useMemo, useRef, useState, useEffect } from "react";
import { api, ApiError, type Product, type ProductInput } from "@/lib/api";
import ProductForm from "./ProductForm";

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [editing, setEditing] = useState<Product | null | "new">(null);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function refresh() {
    api
      .listProducts()
      .then(setProducts)
      .catch((e: ApiError) => setError(e.message));
  }

  useEffect(refresh, []);

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const p of products ?? []) {
      if (p.category) set.add(p.category);
    }
    return Array.from(set).sort();
  }, [products]);

  const filtered = useMemo(() => {
    if (!products) return [];
    const q = query.trim().toLowerCase();
    return products.filter((p) => {
      if (category && p.category !== category) return false;
      if (!q) return true;
      return (
        p.name.toLowerCase().includes(q) ||
        (p.description ?? "").toLowerCase().includes(q) ||
        (p.category ?? "").toLowerCase().includes(q) ||
        (p.sku ?? "").toLowerCase().includes(q)
      );
    });
  }, [products, query, category]);

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
    try {
      await api.deleteProduct(product.id);
      refresh();
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  async function handleCsvSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true);
    setImportMessage(null);
    setError(null);
    try {
      const result = await api.importProductsCsv(file);
      setImportMessage(
        `Import abgeschlossen: ${result.created} neu, ${result.updated} aktualisiert, ` +
          `${result.skipped} übersprungen` +
          (result.errors.length ? ` — ${result.errors.length} Hinweis(e): ${result.errors.join(" / ")}` : ".")
      );
      refresh();
    } catch (e) {
      setError((e as ApiError).message);
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

      {error && <div className="error-box" style={{ marginBottom: 16 }}>{error}</div>}
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

      {products === null && !error && <p className="muted">Lade…</p>}
      {products !== null && filtered.length === 0 && (
        <div className="empty-state">Keine Produkte gefunden.</div>
      )}

      {filtered.length > 0 && (
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
              {filtered.map((p) => (
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
      )}
    </div>
  );
}
