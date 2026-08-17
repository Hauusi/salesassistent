from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    category: str | None
    sku: str | None
    price: Decimal | None
    currency: str
    availability: str | None
    specs: dict
    created_at: datetime
    updated_at: datetime


class ProductCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=255)
    sku: str | None = Field(default=None, max_length=100)
    price: Decimal | None = None
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    availability: str | None = Field(default=None, max_length=255)
    specs: dict = Field(default_factory=dict)


class ProductUpdateIn(BaseModel):
    """All fields optional - PUT applies a partial update (only supplied
    fields are changed), which is friendlier for an edit form that only
    resends what the user touched."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=255)
    sku: str | None = Field(default=None, max_length=100)
    price: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    availability: str | None = Field(default=None, max_length=255)
    specs: dict | None = None


class ProductImportResult(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: list[str]
