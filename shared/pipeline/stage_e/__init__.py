"""
Stage E: テキストフェーズ

E1-E3: 物理抽出（stage_e_preprocessing.py）
E6: Vision OCR
E7L: LLM差分抽出（接着候補の検出のみ）
E7P: Pythonパッチ適用（物理結合の実行）
E8: bbox正規化
"""

from .e6_vision_ocr import E6VisionOCR
from .e7l_llm_merger import E7LMergeDetector
from .e7p_patch_applier import E7PPatchApplier
from .e8_bbox_normalizer import E8BboxNormalizer

__all__ = ['E6VisionOCR', 'E7LMergeDetector', 'E7PPatchApplier', 'E8BboxNormalizer']
