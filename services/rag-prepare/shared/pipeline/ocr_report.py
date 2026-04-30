"""
OCR認識精度レポート生成

OCR処理の詳細な統計とレポートを生成
"""
from typing import Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime
import json


@dataclass
class OCRRegionStats:
    """領域ごとの統計"""
    region_id: int
    bbox: List[int]
    text_length: int
    confidence: float
    preprocessing_applied: bool
    reprocessed: bool = False
    improvement: float = 0.0


@dataclass
class OCRProcessingReport:
    """OCR処理レポート"""
    # 基本情報
    file_name: str
    processing_time: float
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # 領域統計
    total_regions: int = 0
    recognized_regions: int = 0
    low_confidence_regions: int = 0
    reprocessed_regions: int = 0
    improved_regions: int = 0

    # 認識統計
    total_chars: int = 0
    avg_confidence: float = 0.0
    min_confidence: float = 1.0
    max_confidence: float = 0.0

    # 前処理統計
    preprocessed_regions: int = 0
    preprocessing_time: float = 0.0

    # 再処理統計
    reprocessing_time: float = 0.0
    avg_improvement: float = 0.0

    # キャッシュ統計
    cache_hits: int = 0
    cache_misses: int = 0

    # 領域詳細
    regions: List[OCRRegionStats] = field(default_factory=list)

    def add_region(self, region_stats: OCRRegionStats):
        """領域統計を追加"""
        self.regions.append(region_stats)
        self.total_regions += 1

        if region_stats.text_length > 0:
            self.recognized_regions += 1
            self.total_chars += region_stats.text_length

        if region_stats.confidence < 0.7:
            self.low_confidence_regions += 1

        if region_stats.preprocessing_applied:
            self.preprocessed_regions += 1

        if region_stats.reprocessed:
            self.reprocessed_regions += 1
            if region_stats.improvement > 0:
                self.improved_regions += 1

        # 信頼度統計更新
        if region_stats.confidence > 0:
            self.min_confidence = min(self.min_confidence, region_stats.confidence)
            self.max_confidence = max(self.max_confidence, region_stats.confidence)

    def calculate_final_stats(self):
        """最終統計を計算"""
        if self.regions:
            confidences = [r.confidence for r in self.regions if r.confidence > 0]
            self.avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            improvements = [r.improvement for r in self.regions if r.reprocessed and r.improvement > 0]
            self.avg_improvement = sum(improvements) / len(improvements) if improvements else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """辞書形式に変換"""
        return {
            'file_name': self.file_name,
            'processing_time': self.processing_time,
            'timestamp': self.timestamp,
            'summary': {
                'total_regions': self.total_regions,
                'recognized_regions': self.recognized_regions,
                'recognition_rate': self.recognized_regions / self.total_regions if self.total_regions > 0 else 0,
                'low_confidence_regions': self.low_confidence_regions,
                'total_chars': self.total_chars,
            },
            'confidence': {
                'average': self.avg_confidence,
                'min': self.min_confidence,
                'max': self.max_confidence,
            },
            'preprocessing': {
                'preprocessed_regions': self.preprocessed_regions,
                'preprocessing_rate': self.preprocessed_regions / self.total_regions if self.total_regions > 0 else 0,
                'preprocessing_time': self.preprocessing_time,
            },
            'reprocessing': {
                'reprocessed_regions': self.reprocessed_regions,
                'improved_regions': self.improved_regions,
                'improvement_rate': self.improved_regions / self.reprocessed_regions if self.reprocessed_regions > 0 else 0,
                'avg_improvement': self.avg_improvement,
                'reprocessing_time': self.reprocessing_time,
            },
            'cache': {
                'cache_hits': self.cache_hits,
                'cache_misses': self.cache_misses,
                'cache_hit_rate': self.cache_hits / (self.cache_hits + self.cache_misses) if (self.cache_hits + self.cache_misses) > 0 else 0,
            }
        }

    def to_json(self, indent: int = 2) -> str:
        """JSON文字列に変換"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def print_summary(self):
        """サマリーを表示"""
        print("\n" + "=" * 60)
        print(f"OCR Processing Report: {self.file_name}")
        print("=" * 60)
        print(f"Total Regions: {self.total_regions}")
        print(f"Recognized: {self.recognized_regions}/{self.total_regions} ({self.recognized_regions/self.total_regions*100:.1f}%)")
        print(f"Total Characters: {self.total_chars}")
        print(f"Average Confidence: {self.avg_confidence:.2%}")
        print(f"Low Confidence Regions: {self.low_confidence_regions} (< 0.7)")
        print(f"\nPreprocessing:")
        print(f"  Preprocessed: {self.preprocessed_regions}/{self.total_regions} ({self.preprocessed_regions/self.total_regions*100:.1f}%)")
        print(f"  Time: {self.preprocessing_time:.2f}s")
        print(f"\nReprocessing:")
        print(f"  Reprocessed: {self.reprocessed_regions}")
        print(f"  Improved: {self.improved_regions}/{self.reprocessed_regions}")
        if self.reprocessed_regions > 0:
            print(f"  Improvement Rate: {self.improved_regions/self.reprocessed_regions*100:.1f}%")
            print(f"  Avg Improvement: +{self.avg_improvement:.2%}")
        print(f"  Time: {self.reprocessing_time:.2f}s")
        print(f"\nCache:")
        total_cache = self.cache_hits + self.cache_misses
        if total_cache > 0:
            print(f"  Hits: {self.cache_hits}/{total_cache} ({self.cache_hits/total_cache*100:.1f}%)")
        print(f"\nTotal Processing Time: {self.processing_time:.2f}s")
        print("=" * 60 + "\n")
