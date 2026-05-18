"""
Stage F: Data Fusion & Normalization（データ統合・正規化）

Stage B（デジタル抽出）と Stage E（視覚抽出）の結果を統合し、
日付を正規化し、F17 でデータ平面出口（reading_stream 正本）を確定する。

パイプライン: F11 → F13 → F17 → Stage G（G11 でレビュー用 ui_data）

生成モデル（LLM）: F13 のみ（日付正規化・Gemini Flash-lite）。
表 UI チェーンは Stage G（G15→G22→…→G65）。
"""

from .f11_data_fusion_merger import F11DataFusionMerger
from .f13_smart_date_normalizer import F13SmartDateNormalizer
from .f17_stage_f_finalize import F17StageFFinalize
from .f5_logical_table_joiner import F5LogicalTableJoiner
from .substage_ids import STAGE_F_SUBSTAGES
from .review_tables_payload import build_tables_review_html, build_tables_ssot
from dms.common.config.settings import settings

# 後方互換エイリアス（旧モジュール名・旧サブステージ ID）
F1DataFusionMerger = F11DataFusionMerger
F3SmartDateNormalizer = F13SmartDateNormalizer
F40StageFFinalize = F17StageFFinalize


class F1Controller:
    """Stage F チェーン: F11 → F13 → F17"""

    def __init__(self):
        table_joiner = F17StageFFinalize()
        date_normalizer = F13SmartDateNormalizer(
            api_key=settings.GOOGLE_AI_API_KEY or None,
            next_stage=table_joiner,
        )
        self.merger = F11DataFusionMerger(next_stage=date_normalizer)

    def process(
        self,
        stage_a_result=None,
        stage_b_result=None,
        stage_d_result=None,
        stage_e_result=None,
        year_context=None,
        rawdata_record=None,
        session_id=None,
    ):
        return self.merger.merge(
            stage_a_result=stage_a_result,
            stage_b_result=stage_b_result,
            stage_d_result=stage_d_result,
            stage_e_result=stage_e_result,
            rawdata_record=rawdata_record,
            session_id=session_id,
        )


__all__ = [
    "F1Controller",
    "F11DataFusionMerger",
    "F1DataFusionMerger",
    "F13SmartDateNormalizer",
    "F3SmartDateNormalizer",
    "F17StageFFinalize",
    "F40StageFFinalize",
    "F5LogicalTableJoiner",
    "STAGE_F_SUBSTAGES",
    "build_tables_review_html",
    "build_tables_ssot",
]
