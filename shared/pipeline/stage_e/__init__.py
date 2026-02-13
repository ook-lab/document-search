"""
Stage E: Vision Extraction & AI Structuring（視覚抽出・AI構造化）

パイプライン（非表）:
  E-21（条件付きOCR）→ E-22/E-20（Context Extractor - Gemini Flash-lite）

パイプライン（表）:
  E-30（構造専用 - Gemini Flash）→ E-31（セルOCR - Vision API）→ E-32（合成）
"""

from .e1_controller import E1Controller
from .e1_ocr_scouter import E1OcrScouter
from .e5_text_block_visualizer import E5TextBlockVisualizer
from .e20_context_extractor import E20ContextExtractor
from .e21_non_table_vision_ocr import E21NonTableVisionOcr
from .e30_table_structure_extractor import E30TableStructureExtractor
from .e31_table_vision_ocr import E31TableVisionOcr
from .e32_table_cell_merger import E32TableCellMerger

__all__ = [
    'E1Controller',
    'E1OcrScouter',
    'E5TextBlockVisualizer',
    'E20ContextExtractor',
    'E21NonTableVisionOcr',
    'E30TableStructureExtractor',
    'E31TableVisionOcr',
    'E32TableCellMerger',
]
