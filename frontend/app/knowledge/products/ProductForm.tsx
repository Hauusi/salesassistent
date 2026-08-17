"use client";

import { useState } from "react";
import { type Product, type ProductInput } from "@/lib/api";

interface Props {
  initial: Product | null;
  onCancel: () => void;
  onSubmit: (payload: ProductInput) => Promise<void>;
}

export default function ProductForm({ initial, onCancel, onSubmit }: Props) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [category, setCategory] = useState(initial?.category ?? "");
  const [sku, setSku] = useState(initial?.sku ?? "");
  const [price, setPrice] = useState(initial?.price ?? "");
  const [currency, setCurrency] = useState(initial?.currency ?? "EUR");
  const [availability, setAvailability] = useState(initial?.availability ?? "");
  const [specsText, setSpecsText] = useState(
    initial?.specs ? JSON.stringify(initial.specs, null, 2) : "{}"
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    let specs: Record<string, unknown>;
    try {
      specs = specsText.trim() ? JSON.parse(specsText) : {};
    } catch {
      setError("Technische Specs müssen gültiges JSON sein, z. B. {\"Gewicht\": \"2.4 kg\"}.");
      return;
    }

    let parsedPrice: number | null = null;
    if (price !== "" && price !== null) {
      const n = Number(String(price).replace(",", "."));
      if (Number.isNaN(n)) {
        setError("Preis muss eine Zahl sein.");
        return;
      }
      parsedPrice = n;
    }

    setSaving(true);
    try {
      await onSubmit({
        name,
        description: description || null,
        category: category || null,
        sku: sku || null,
        price: parsedPrice,
        currency: currency || "EUR",
        availability: availability || null,
        specs,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Speichern fehlgeschlagen.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card" style={{ marginBottom: 20 }}>
      <h3 style={{ marginTop: 0 }}>{initial ? "Produkt bearbeiten" : "Neues Produkt"}</h3>

      {error && <div className="error-box" style={{ marginBottom: 12 }}>{error}</div>}

      <div className="field">
        <label htmlFor="p-name">Name *</label>
        <input id="p-name" type="text" value={name} onChange={(e) => setName(e.target.value)} required />
      </div>

      <div className="field">
        <label htmlFor="p-description">Beschreibung</label>
        <textarea
          id="p-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ minHeight: 80 }}
        />
      </div>

      <div className="toolbar" style={{ marginTop: 16 }}>
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor="p-category">Kategorie</label>
          <input id="p-category" type="text" value={category} onChange={(e) => setCategory(e.target.value)} />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor="p-sku">SKU / Artikelnummer</label>
          <input id="p-sku" type="text" value={sku} onChange={(e) => setSku(e.target.value)} />
        </div>
      </div>

      <div className="toolbar">
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor="p-price">Preis</label>
          <input
            id="p-price"
            type="text"
            inputMode="decimal"
            value={price ?? ""}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="z. B. 12.50"
          />
        </div>
        <div className="field" style={{ width: 100 }}>
          <label htmlFor="p-currency">Währung</label>
          <input id="p-currency" type="text" value={currency} onChange={(e) => setCurrency(e.target.value)} />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor="p-availability">Verfügbarkeit / Lieferzeit</label>
          <input
            id="p-availability"
            type="text"
            value={availability}
            onChange={(e) => setAvailability(e.target.value)}
            placeholder="z. B. 3-5 Werktage"
          />
        </div>
      </div>

      <div className="field">
        <label htmlFor="p-specs">Technische Specs (JSON)</label>
        <textarea
          id="p-specs"
          value={specsText}
          onChange={(e) => setSpecsText(e.target.value)}
          style={{ minHeight: 100, fontFamily: "monospace", fontSize: 13 }}
        />
      </div>

      <div className="toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
        <button type="button" onClick={onCancel} disabled={saving}>
          Abbrechen
        </button>
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? "Speichern…" : "Speichern"}
        </button>
      </div>
    </form>
  );
}
