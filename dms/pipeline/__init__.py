"""
新しいパイプラインアーキテクチャ: A→B→D→E→F→G→H→J→K

各ステージは独立したコントローラーとして実装されています。

使用方法:
    from dms.pipeline.stage_a import A3EntryPoint
    from dms.pipeline.stage_b import B1Controller
    from dms.pipeline.stage_d import D1Controller
    from dms.pipeline.stage_e import E1Controller
    from dms.pipeline.stage_f import F1Controller
    from dms.pipeline.stage_g import G1Controller

    stage_a = A3EntryPoint()
    stage_b = B1Controller()
    # ...
"""

from dms.pipeline.unified_document_pipeline import UnifiedDocumentPipeline

__all__ = ["UnifiedDocumentPipeline"]
__version__ = "2.0.0"
