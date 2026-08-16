"""Embeddings for semantic case matching / RAG (Voyage AI).

Anthropic's Claude API does not offer an embeddings endpoint; Voyage AI is
Anthropic's recommended embedding partner, so it is used here rather than
introducing a second, unrelated model provider. The provider is isolated
behind this module's functions so it can be swapped (e.g. for a local
sentence-transformers model) without touching callers.
"""
from __future__ import annotations

import asyncio

import voyageai

from app.config import get_settings

settings = get_settings()


def get_voyage_client() -> voyageai.Client:
    return voyageai.Client(api_key=settings.voyage_api_key)


async def embed_text(text: str, *, client: voyageai.Client | None = None) -> list[float]:
    """Embeds a single text (subject+body) for storage/case-matching."""
    client = client or get_voyage_client()
    text = text.strip() or " "  # Voyage rejects fully empty strings

    def _embed() -> list[float]:
        result = client.embed([text], model=settings.voyage_embedding_model, input_type="document")
        return result.embeddings[0]

    return await asyncio.to_thread(_embed)
