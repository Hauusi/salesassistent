"""Shared response schema building blocks.

`ORMBase` carries the from_attributes config so the concrete schemas do
not each repeat it - which they all used to, one copy per file.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CaseStatus

# A free-form key/value map (product specs from a CSV import, say).
#
# A bare `dict` renders in OpenAPI as `{"type": "object"}` with no value
# type, which generators read as "an object with no permitted keys" -
# openapi-typescript emits Record<string, never>, so the frontend could not
# assign anything to it. Saying additionalProperties explicitly is what
# makes the generated type Record<string, unknown>.
FreeFormDict = Annotated[
    dict[str, Any], Field(json_schema_extra={"additionalProperties": True})
]


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ContactOut(ORMBase):
    id: uuid.UUID
    email_address: str
    name: str | None
    company: str | None


class CaseOut(ORMBase):
    id: uuid.UUID
    title: str
    summary: str | None
    status: CaseStatus
    created_at: datetime
