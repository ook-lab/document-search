"""
Stage F: Data Fusion & Normalization（データ統合・正規化）

Stage B（デジタル抽出）と Stage E（視覚抽出）の結果を統合し、
日付や表構造を機械が扱いやすい形式に正規化する。

パイプライン:
F-1: Controller（オーケストレーター）
  ├─ F-1: Data Fusion Merger（ハイブリッド統合）
  ├─ F-3: Smart Date/Time Normalizer（日付正規化 - Gemini 2.5 Flash-lite）
  └─ F-5: Logical Table Joiner（表結合）

出力:
- 正規化されたイベント（ISO 8601 形式の日付）
- 統合されたテキスト
- 結合された表データ
- メタデータ（トークン使用量等）
"""

from .f1_controller import F1Controller
from .f1_data_fusion_merger import F1DataFusionMerger
from .f3_smart_date_normalizer import F3SmartDateNormalizer
from .f5_logical_table_joiner import F5LogicalTableJoiner

__all__ = [
    'F1Controller',
    'F1DataFusionMerger',
    'F3SmartDateNormalizer',
    'F5LogicalTableJoiner',
]
