"""Embeddings for semantic case matching / RAG (Voyage AI).

Anthropic's Claude API does not offer an embeddings endpoint; Voyage AI is
Anthropic's recommended embedding partner, so it is used here rather than
introducing a second, unrelated model provider. The provider is isolated
behind this module's functions so it can be swapped (e.g. for a local
sentence-transformers model) without touching callers.

The retry policy is shared with the Claude calls - see
app/services/llm_client.py.
"""
from __future__ import annotations

import voyageai

from app.config import get_settings
from app.services.llm_client import embed, get_voyage_client

__all__ = ["embed_text", "get_voyage_client"]


async def embed_text(text: str, *, client: voyageai.Client | None = None) -> list[float]:
    """Embeds a single text (subject+body) for storage/case-matching."""
    client = client or get_voyage_client()
    text = text.strip() or " "  # Voyage rejects fully empty strings
    return await embed(
        text,
        client=client,
        model=get_settings().voyage_embedding_model,
        input_type="document",
    )
