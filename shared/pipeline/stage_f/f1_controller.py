"""
F-1: Stage F Controller（Orchestrator）

Stage F の各コンポーネントを統合し、データ統合・正規化を実行する。

パイプライン:
F-1: Data Fusion Merger（ハイブリッド統合）
  ↓
F-3: Smart Date/Time Normalizer（日付正規化）
  ↓
F-5: Logical Table Joiner（表結合）
  ↓
F-1: Controller（全体統合）
"""

from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

from .f1_data_fusion_merger import F1DataFusionMerger
from .f3_smart_date_normalizer import F3SmartDateNormalizer
from .f5_logical_table_joiner import F5LogicalTableJoiner


class F1Controller:
    """F-1: Stage F Controller（Orchestrator）"""

    def __init__(
        self,
        gemini_api_key: Optional[str] = None
    ):
        """
        F-1 コントローラー初期化

        Args:
            gemini_api_key: Google AI API Key
        """
        self.merger = F1DataFusionMerger()
        self.date_normalizer = F3SmartDateNormalizer(api_key=gemini_api_key)
        self.table_joiner = F5LogicalTableJoiner()

    def process(
        self,
        stage_a_result: Optional[Dict[str, Any]] = None,
        stage_b_result: Optional[Dict[str, Any]] = None,
        stage_d_result: Optional[Dict[str, Any]] = None,
        stage_e_result: Optional[Dict[str, Any]] = None,
        year_context: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Stage F データ統合・正規化を実行

        Args:
            stage_a_result: Stage A の結果
            stage_b_result: Stage B の結果
            stage_d_result: Stage D の結果
            stage_e_result: Stage E の結果
            year_context: 年度コンテキスト（オプション）

        Returns:
            {
                'success': bool,
                'document_info': dict,
                'normalized_events': list,
                'tasks': list,
                'notices': list,
                'consolidated_tables': list,
                'raw_integrated_text': str,
                'metadata': dict
            }
        """
        logger.info("=" * 60)
        logger.info("[F-1] Stage F データ統合・正規化開始")
        logger.info("=" * 60)

        try:
            # Step 1: Data Fusion Merger
            logger.info("\n[F-1] ステップ1: ハイブリッド統合（F-1 Merger）")
            merge_result = self.merger.merge(
                stage_a_result=stage_a_result,
                stage_b_result=stage_b_result,
                stage_d_result=stage_d_result,
                stage_e_result=stage_e_result
            )

            if not merge_result.get('success'):
                logger.error(f"[F-1] 統合失敗: {merge_result.get('error')}")
                return merge_result

            # Step 2: Date/Time Normalization
            logger.info("\n[F-1] ステップ2: 日付正規化（F-3）")

            # 年度コンテキストを決定
            if year_context is None:
                # document_info から年度を取得（あれば）
                doc_year = merge_result.get('document_info', {}).get('year_context')
                if doc_year:
                    year_context = doc_year

            events = merge_result.get('events', [])
            date_result = None
            if events:
                date_result = self.date_normalizer.normalize_dates(
                    events=events,
                    year_context=year_context
                )

                if date_result.get('success'):
                    normalized_events = date_result['normalized_events']
                    logger.info(f"[F-1] 日付正規化: {date_result['normalization_count']}件")
                else:
                    logger.warning("[F-1] 日付正規化失敗、元のイベントを使用")
                    normalized_events = events
            else:
                normalized_events = []
                logger.info("[F-1] 正規化するイベントなし")

            # Step 3: Table Joining
            logger.info("\n[F-1] ステップ3: 表結合（F-5）")

            tables = merge_result.get('tables', [])
            if tables:
                join_result = self.table_joiner.join_tables(tables)

                if join_result.get('success'):
                    consolidated_tables = join_result['joined_tables']
                    logger.info(f"[F-1] 表結合: {join_result['join_count']}個")
                else:
                    logger.warning("[F-1] 表結合失敗、元の表を使用")
                    consolidated_tables = tables
            else:
                consolidated_tables = []
                logger.info("[F-1] 結合する表なし")

            # 最終結果を構築
            result = {
                'success': True,
                'document_info': merge_result.get('document_info', {}),
                'normalized_events': normalized_events,
                'tasks': merge_result.get('tasks', []),
                'notices': merge_result.get('notices', []),
                'consolidated_tables': consolidated_tables,
                'raw_integrated_text': merge_result.get('raw_integrated_text', ''),
                'metadata': merge_result.get('metadata', {})
            }

            # メタデータを更新
            if 'metadata' not in result:
                result['metadata'] = {}

            # 日付正規化のトークン数を加算
            if date_result and date_result.get('success'):
                current_tokens = result['metadata'].get('total_tokens', 0)
                result['metadata']['total_tokens'] = current_tokens + date_result.get('tokens_used', 0)

            logger.info("=" * 60)
            logger.info("[F-1] Stage F 完了")
            logger.info(f"  ├─ イベント: {len(normalized_events)}件")
            logger.info(f"  ├─ タスク: {len(result['tasks'])}件")
            logger.info(f"  ├─ 注意事項: {len(result['notices'])}件")
            logger.info(f"  ├─ 表: {len(consolidated_tables)}個")
            logger.info(f"  └─ 総トークン: {result['metadata'].get('total_tokens', 0)}")
            logger.info("=" * 60)

            return result

        except Exception as e:
            logger.error(f"[F-1] 処理エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'document_info': {},
                'normalized_events': [],
                'tasks': [],
                'notices': [],
                'consolidated_tables': [],
                'raw_integrated_text': '',
                'metadata': {}
            }
