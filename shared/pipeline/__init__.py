"""
G_unified_pipeline: 統合ドキュメント処理パイプライン

Stage E-K を統合した、堅牢かつ高精度なドキュメント処理フロー

使用方法:
    from shared.pipeline import UnifiedDocumentPipeline

    pipeline = UnifiedDocumentPipeline()
    result = await pipeline.process_document(
        file_path=Path("document.pdf"),
        file_name="document.pdf",
        doc_type="invoice",
        workspace="personal",
        mime_type="application/pdf",
        source_id="drive_file_id"
    )
"""

from .pipeline import UnifiedDocumentPipeline

__all__ = ['UnifiedDocumentPipeline']
__version__ = '1.0.0'
