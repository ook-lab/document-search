"""
Stage F: Data Fusion & Normalization（データ統合・正規化）

Stage B（デジタル抽出）と Stage E（視覚抽出）の結果を統合し、
日付や表構造を機械が扱いやすい形式に正規化する。

パイプライン（チェーン）:
F-1 Merger → F-3 Normalizer → F-5 Joiner

出力:
- 正規化されたイベント（ISO 8601 形式の日付）
- 統合されたテキスト
- 結合された表データ
- メタデータ（トークン使用量等）
"""

from .f1_data_fusion_merger import F1DataFusionMerger
from .f3_smart_date_normalizer import F3SmartDateNormalizer
from .f5_logical_table_joiner import F5LogicalTableJoiner


class F1Controller:
    """Stage F チェーン（旧 Controller）"""

    def __init__(self, gemini_api_key=None):
        """
        チェーン構築: F-1 Merger → F-3 Normalizer → F-5 Joiner

        Args:
            gemini_api_key: Google AI API Key
        """
        # ★チェーンパターン: 逆順で構築
        table_joiner = F5LogicalTableJoiner()
        date_normalizer = F3SmartDateNormalizer(
            api_key=gemini_api_key,
            next_stage=table_joiner
        )
        self.merger = F1DataFusionMerger(next_stage=date_normalizer)

    def process(
        self,
        stage_a_result=None,
        stage_b_result=None,
        stage_d_result=None,
        stage_e_result=None,
        year_context=None
    ):
        """
        Stage F 処理実行（チェーン開始）

        Args:
            stage_a_result: Stage A の結果
            stage_b_result: Stage B の結果
            stage_d_result: Stage D の結果
            stage_e_result: Stage E の結果
            year_context: 年度コンテキスト

        Returns:
            F-5の最終結果（チェーン経由）
        """
        return self.merger.merge(
            stage_a_result=stage_a_result,
            stage_b_result=stage_b_result,
            stage_d_result=stage_d_result,
            stage_e_result=stage_e_result
        )


__all__ = [
    'F1Controller',
    'F1DataFusionMerger',
    'F3SmartDateNormalizer',
    'F5LogicalTableJoiner',
]
