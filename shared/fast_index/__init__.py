"""Lightweight fast-index (10_ix_search_index only, no full OCR pipeline)."""

from shared.fast_index.indexer import FastIndexer
from shared.fast_index.queries import fetch_pending_fast_index_docs
from shared.fast_index.scope import FAST_INDEX_RAW_TABLES, resolve_pdf_toolbox_base

__all__ = [
    "FastIndexer",
    "FAST_INDEX_RAW_TABLES",
    "fetch_pending_fast_index_docs",
    "resolve_pdf_toolbox_base",
]
