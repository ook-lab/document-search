"""
Legacy entry point used by data-ingestion (inbox monitor, flyer batch).

Historically imported as ``from shared.pipeline import UnifiedDocumentPipeline``.
The full file-to-document orchestration should be routed through ``PipelineManager``
and ``pipeline_meta``; this class keeps imports working and fails loudly at runtime
until a thin adapter is implemented.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


class UnifiedDocumentPipeline:
    """Compatibility shim (constructor matches legacy call sites)."""

    def __init__(self, db_client=None, db=None, **_kwargs):
        self._db = db_client or db

    async def process_document(
        self,
        *,
        file_path: Path,
        file_name: str,
        doc_type: str,
        workspace: str,
        mime_type: str,
        source_id: str,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        _ = (self._db, file_path, file_name, doc_type, workspace, mime_type, source_id, extra_metadata)
        logger.error(
            "UnifiedDocumentPipeline.process_document is not implemented. "
            "Queue work via pipeline_meta / PipelineManager instead."
        )
        return {
            "success": False,
            "error": "UnifiedDocumentPipeline.process_document is not implemented",
        }