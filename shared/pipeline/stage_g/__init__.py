"""
Stage G: Post-Processing Pipeline (Ver 9.0)

処理フロー:
  G3 → G4 → G5 → G6
   ↓    ↓    ↓    ↓
 Scrub Assemble Audit Packager
 書換   組立    確定   整形

分割構成:
  - g3_scrub.py: G3 Scrub（唯一の書き換えゾーン）
  - g4_assemble.py: G4 Assemble（read-only組み立て）
  - g5_audit.py: G5 Audit（検算・品質・確定 = 唯一の正本出口）
  - g6_packager.py: G6 Packager（用途別出力整形）

絶対ルール:
  1. 値を書き換えるのは G3 だけ
  2. G4/G5/G6 は read-only
  3. G5の出力 scrubbed_data が唯一の正本
  4. G6は用途別フォーマットだけ（AI禁止）
"""

from .g3_scrub import G3Scrub
from .g4_assemble import G4Assemble
from .g5_audit import G5Audit
from .g6_packager import G6Packager

__all__ = [
    'G3Scrub',
    'G4Assemble',
    'G5Audit',
    'G6Packager',
]
