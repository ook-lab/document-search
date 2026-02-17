"""
Stage E: Vision Extraction & AI Structuring（視覚抽出・AI構造化）

パイプライン（非表）:
  E-1（スカウト）→ E-21（Context Extractor - Gemini）
  ※ char_count >= 500 の場合は E-20（Vision OCR）も経由

パイプライン（表）:
  E-1（スカウト）→ E-30（構造 - Gemini Flash）→ E-31（中継）→ E-32（割当）→ E-40（image SSOT）
  ※ char_count >= 500 の場合のみ実行

E-37（B埋め込みテキスト監査）:
  stage_b_result 有り → E37 実行 → table_audit に格納（F には渡さない）
"""

from .controller import E1Controller

from .e1_ocr_scouter import E1OcrScouter
from .e5_text_block_visualizer import E5TextBlockVisualizer
from .e20_non_table_vision_ocr import E20NonTableVisionOcr
from .e21_context_extractor import E21ContextExtractor
from .e25_paragraph_grouper import E25ParagraphGrouper
from .e27_position_merger import E27PositionMerger
from .e30_table_structure_extractor import E30TableStructureExtractor
from .e31_table_vision_ocr import E31TableVisionOcr
from .e32_table_cell_merger import E32TableCellMerger
from .e37_embedded_cell_assigner import E37EmbeddedCellAssigner
from .e40_image_ssot_consolidator import E40ImageSsotConsolidator


__all__ = [
    'E1Controller',
    'E1OcrScouter',
    'E5TextBlockVisualizer',
    'E20NonTableVisionOcr',
    'E21ContextExtractor',
    'E25ParagraphGrouper',
    'E27PositionMerger',
    'E30TableStructureExtractor',
    'E31TableVisionOcr',
    'E32TableCellMerger',
    'E37EmbeddedCellAssigner',
    'E40ImageSsotConsolidator',
]
