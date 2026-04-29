"""
Stage D: Visual Structure Analysis（視覚構造解析）

埋め込みテキストがない、または画像化されたPDFに対して、
罫線解析からセルの座標を特定し、表領域と非表領域に分割する。

パイプライン:
D-1: Controller（オーケストレーター）
  ├─ D-3: Vector Line Extractor（pdfplumber でベクトル罫線抽出）
  ├─ D-5: Raster Line Detector（OpenCV で画像内罫線検出）
  ├─ D-8: Grid Analyzer（格子解析・交点計算）
  ├─ D-9: Cell Identifier（セル座標特定）
  └─ D-10: Image Slicer（表/非表画像への物理分割）

出力:
- 表領域の個別画像（table_T1.png, ...）
- 非表領域画像（background_only.png）
- セル座標マップ（cell_map）
"""

from .d1_controller import D1Controller
from .d3_vector_line_extractor import D3VectorLineExtractor
from .d5_raster_line_detector import D5RasterLineDetector
from .d8_grid_analyzer import D8GridAnalyzer
from .d9_cell_identifier import D9CellIdentifier
from .d10_image_slicer import D10ImageSlicer

__all__ = [
    'D1Controller',
    'D3VectorLineExtractor',
    'D5RasterLineDetector',
    'D8GridAnalyzer',
    'D9CellIdentifier',
    'D10ImageSlicer',
]
