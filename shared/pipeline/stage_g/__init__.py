"""
Stage G: Post-Processing Pipeline (Ver 10.0)

処理フロー:
  G3 → G4 → G5 → G6 → G7 → G8
   ↓    ↓    ↓    ↓    ↓    ↓
 Scrub Assemble Audit Packager HeaderDetect HeaderEnrich
 書換   組立    確定   整形    ヘッダー検出  ヘッダー紐付け

分割構成:
  - g3_scrub.py: G3 Scrub（唯一の書き換えゾーン）
  - g4_assemble.py: G4 Assemble（read-only組み立て）
  - g5_audit.py: G5 Audit（検算・品質・確定 = 唯一の正本出口）
  - g6_packager.py: G6 Packager（用途別出力整形）
  - g7_header_detector.py: G7 Header Detector（LLMでヘッダー位置を検出）
  - g8_header_enricher.py: G8 Header Enricher（ヘッダー値をセルに機械的付与）

絶対ルール:
  1. 値を書き換えるのは G3 だけ
  2. G4/G5/G6 は read-only
  3. G5の出力 scrubbed_data が唯一の正本
  4. G6は用途別フォーマットだけ（AI禁止）
  5. G7はLLMでヘッダー位置のみ検出（header_map）
  6. G8はPythonのみでヘッダー値をセルに紐付け（AI禁止）
"""

from .g3_scrub import G3Scrub
from .g4_assemble import G4Assemble
from .g5_audit import G5Audit
from .g6_packager import G6Packager
from .g7_header_detector import G7HeaderDetector
from .g8_header_enricher import G8HeaderEnricher

__all__ = [
    'G3Scrub',
    'G4Assemble',
    'G5Audit',
    'G6Packager',
    'G7HeaderDetector',
    'G8HeaderEnricher',
]
