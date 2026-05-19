"""
Stage E: Vision Extraction & AI Structuring（視覚抽出・AI構造化）

パイプライン（非表）:
  E-1（スカウト）→ E-5（ブロック認識）→ E-16（行消し）→ E-20（Vision OCR）→ E-21（Context）→ E-25（段落）
  ※ char_count により E-20 等の分岐あり

パイプライン（表・画像 SSOT）:
  E-1 → E-30 → E-31 → E-32 → E-37（監査）→ E-40（image SSOT）

パイプライン（表・構造化チェーン。F-53 表分岐から）:
  F-54 → F-55 → F-56 → F-57 → F-58（いずれも `stage_f`）

F-52 / F-10（プレフュージョン・E-1 出口。`stage_f`）:
  F-52 で UI 表に整形 → F-10 で上記表サブチェーンと同一配線（結合前の表に対しても Gemini 可）。

F-53（レビュー UI 用。`stage_f`。ワーカー上は F60 から呼ばれる）:
  F60 が組み立てた `blocks` を受け取り ui_data 組立と表チェーン分岐を行う（E-52 は廃止）。
"""

from dms.pipeline.substage_order import e_stage_export_sort_key

from .e11_table_structurer import E11TableStructurer
from .e1_ocr_scouter import E1OcrScouter
from .e20_non_table_vision_ocr import E20NonTableVisionOcr
from .e21_context_extractor import E21ContextExtractor
from .e25_paragraph_grouper import E25ParagraphGrouper
from .e30_table_structure_extractor import E30TableStructureExtractor
from .e31_table_vision_ocr import E31TableVisionOcr
from .e32_table_cell_merger import E32TableCellMerger
from .e37_embedded_cell_assigner import E37EmbeddedCellAssigner
from .e40_image_ssot_consolidator import E40ImageSsotConsolidator
from .e5_text_block_visualizer import E5TextBlockVisualizer
from .controller import E1Controller

__all__ = sorted(
    [
        'E11TableStructurer',
        'E1Controller',
        'E1OcrScouter',
        'E20NonTableVisionOcr',
        'E21ContextExtractor',
        'E25ParagraphGrouper',
        'E30TableStructureExtractor',
        'E31TableVisionOcr',
        'E32TableCellMerger',
        'E37EmbeddedCellAssigner',
        'E40ImageSsotConsolidator',
        'E5TextBlockVisualizer',
    ],
    key=e_stage_export_sort_key,
)
