"""CSV bulk import for the product catalog (Wissensbasis -> Produkte).

Expected CSV: a header row, then one product per row. Recognized columns
(case-insensitive, others ignored): name, description, category, sku,
price, currency, availability, specs. `specs`, if present, must be a JSON
object encoded as a string in that cell (e.g. '{"Gewicht": "2.4 kg"}') -
invalid JSON is reported as a per-row error, not a hard failure of the
whole import.

Rows with a `sku` that already exists for the tenant are updated in place
(so re-uploading an updated catalog export is idempotent); rows without a
`sku`, or with a new one, create a new product.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.schemas.product import ProductImportResult

REQUIRED_COLUMNS = {"name"}


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}


async def import_products_csv(
    db: AsyncSession, *, tenant_id: uuid.UUID, csv_text: str
) -> ProductImportResult:
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return ProductImportResult(
            created=0, updated=0, skipped=0,
            errors=["CSV ist leer oder hat keine Kopfzeile."],
        )

    header = {(h or "").strip().lower() for h in reader.fieldnames}
    missing = REQUIRED_COLUMNS - header
    if missing:
        return ProductImportResult(
            created=0,
            updated=0,
            skipped=0,
            errors=[f"Pflichtspalte(n) fehlen: {', '.join(sorted(missing))}"],
        )

    created = updated = skipped = 0
    errors: list[str] = []

    for line_number, raw_row in enumerate(reader, start=2):  # header is line 1
        row = _normalize_row(raw_row)

        name = row.get("name", "")
        if not name:
            skipped += 1
            errors.append(f"Zeile {line_number}: 'name' fehlt, Zeile übersprungen.")
            continue

        price: Decimal | None = None
        if row.get("price"):
            try:
                price = Decimal(row["price"].replace(",", "."))
            except InvalidOperation:
                errors.append(f"Zeile {line_number}: Preis '{row['price']}' ungültig, wird ignoriert.")

        specs: dict = {}
        if row.get("specs"):
            try:
                parsed = json.loads(row["specs"])
                if isinstance(parsed, dict):
                    specs = parsed
                else:
                    errors.append(f"Zeile {line_number}: 'specs' ist kein JSON-Objekt, wird ignoriert.")
            except json.JSONDecodeError:
                errors.append(f"Zeile {line_number}: 'specs' ist kein gültiges JSON, wird ignoriert.")

        sku = row.get("sku") or None
        existing: Product | None = None
        if sku:
            result = await db.execute(
                select(Product).where(Product.tenant_id == tenant_id, Product.sku == sku)
            )
            existing = result.scalar_one_or_none()

        fields = {
            "name": name,
            "description": row.get("description") or None,
            "category": row.get("category") or None,
            "sku": sku,
            "price": price,
            "currency": (row.get("currency") or "EUR").upper()[:3],
            "availability": row.get("availability") or None,
            "specs": specs,
        }

        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            updated += 1
        else:
            db.add(Product(tenant_id=tenant_id, **fields))
            created += 1

    await db.flush()
    return ProductImportResult(created=created, updated=updated, skipped=skipped, errors=errors)
