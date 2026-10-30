# SPDX-License-Identifier: CPAL-1.0
"""Automatically refresh a written asset through the existing Core contract."""

from remember_me.core import ReindexEmbeddingsRequest


async def reindex_asset(service, asset_id):
    try:
        result = await service.reindex_embeddings(ReindexEmbeddingsRequest(asset_id=asset_id, limit=1))
        return result.failed == 0
    except Exception:
        # Metadata is durable and keyword-searchable even if indexing fails.
        return False
