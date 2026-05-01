"""
Stage A: 入口（書類の判断）

PDFのメタデータから「正体」を突き止め、物理サイズを確定させる。

コンポーネント:
- A3EntryPoint: オーケストレーター
- A5TypeAnalyzer: 書類種類判断
- A6DimensionMeasurer: サイズ測定
"""

from .a3_entry_point import A3EntryPoint
from .a5_type_analyzer import A5TypeAnalyzer
from .a6_dimension_measurer import A6DimensionMeasurer

__all__ = [
    'A3EntryPoint',
    'A5TypeAnalyzer',
    'A6DimensionMeasurer',
]
