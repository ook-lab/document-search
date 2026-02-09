"""
Stage G: UI Optimized Structuring（UI最適化構造化）

Stage F の統合データを、doc-review の UI が追加処理なしで
「完全再現」できる形式にパッケージングする。

パイプライン:
G-1: Controller（オーケストレーター）
  ├─ G-1: High-Fidelity Table Reproduction（表の完全再現）
  ├─ G-3: Semantic Block Arrangement（ブロック整頓）
  └─ G-5: Noise Elimination（ノイズ除去）

出力:
- UI用表データ（headers[], rows[][]）
- 意味的なテキストブロック
- クリーンな表示用データ（ノイズレス）
"""

from .g1_controller import G1Controller
from .g1_table_reproducer import G1TableReproducer
from .g3_block_arranger import G3BlockArranger
from .g5_noise_eliminator import G5NoiseEliminator

__all__ = [
    'G1Controller',
    'G1TableReproducer',
    'G3BlockArranger',
    'G5NoiseEliminator',
]
