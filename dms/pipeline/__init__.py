"""
新しいパイプラインアーキテクチャ: Worker 上では A→B→D→E→F→G（`PipelineManager`）。F は F11→F13→F17（データ平面）。G11（`dms.pipeline.stage_g.G11Controller`）でレビュー用 `ui_data` を組立。G 完了後に 09 統一書き込み。検索インデックス用チャンク・埋め込みは別経路。H/I はドメイン別オプション。

各ステージは独立したコントローラーとして実装されています。

使用方法:
    from dms.pipeline.stage_a import A3EntryPoint
    from dms.pipeline.stage_b import B1Controller
    from dms.pipeline.stage_d import D1Controller
    from dms.pipeline.stage_e import E1Controller
    from dms.pipeline.stage_f import F1Controller
    from dms.pipeline.stage_g import G11Controller

    stage_a = A3EntryPoint()
    stage_b = B1Controller()
    # ...
"""

from dms.pipeline.unified_document_pipeline import UnifiedDocumentPipeline

__all__ = ["UnifiedDocumentPipeline"]
__version__ = "2.0.0"
