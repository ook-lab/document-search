"""
Stage F: Visual Analysis (Ver 10.6)

Fは物理：位置だけ。意味理解は次段。

処理フロー:
  E6 → E7 → E8 → F1 → F2 → F3 → G3 → G4 → G5 → G6

分割構成:
  - f1_grid_detector.py: F1 罫線観測（候補全件保持、モデル不要）
  - f2_structure_analyzer.py: F2 構造解析（物理条件のみ）
  - f3_cell_assigner.py: F3 物理仕分け（セル住所付け、モデル不要）
  - orchestrator.py: 司令塔
"""

from .orchestrator import StageFVisualAnalyzer
from .f1_grid_detector import F1GridDetector
from .f2_structure_analyzer import F2StructureAnalyzer
from .f3_cell_assigner import F3CellAssigner

__all__ = [
    'StageFVisualAnalyzer',
    'F1GridDetector',
    'F2StructureAnalyzer',
    'F3CellAssigner',
]
