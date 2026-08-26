from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import ProductSuggestionStatus
from app.schemas.common import ORMBase
from app.schemas.email import EmailSummaryOut


class ProductSuggestionOut(ORMBase):
    id: uuid.UUID
    email_message_id: uuid.UUID
    sku: str
    name: str
    description: str
    status: ProductSuggestionStatus
    created_product_id: uuid.UUID | None
    reviewed_at: datetime | None
    rejected_reason: str | None
    created_at: datetime
    email_message: EmailSummaryOut


class ProductSuggestionRejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)
