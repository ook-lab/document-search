"""
Stage E: Vision Extraction & AI Structuring（視覚抽出・AI構造化）

画像の文字密度を測定し、用途に応じて Gemini 2.5 Flash-lite と Flash を使い分ける。

パイプライン:
E-1: Controller（オーケストレーター）
  ├─ E-1: OCR Scouter（文字数測定 - Tesseract）
  ├─ E-5: Text Block Visualizer（ブロック認識 - OpenCV）
  ├─ E-20: Context Extractor（地の文用 - Gemini 2.5 Flash-lite）
  └─ E-30: Table Structure Extractor（表用 - Gemini 2.5 Flash）

ルーティング戦略:
- 高密度（500文字以上）: Vision API → テキスト → Gemini 2.5 Flash
- 低密度（500文字未満）: 画像 → 直接 Gemini 2.5 Flash-lite
- 表: 文字数に関わらず Gemini 2.5 Flash（座標ヒント付き）

出力:
- 地の文の構造化データ（予定、タスク、注意事項）
- 表のMarkdown/JSON形式
- トークン使用量・モデル情報
"""

from .e1_controller import E1Controller
from .e1_ocr_scouter import E1OcrScouter
from .e5_text_block_visualizer import E5TextBlockVisualizer
from .e20_context_extractor import E20ContextExtractor
from .e30_table_structure_extractor import E30TableStructureExtractor

__all__ = [
    'E1Controller',
    'E1OcrScouter',
    'E5TextBlockVisualizer',
    'E20ContextExtractor',
    'E30TableStructureExtractor',
]
