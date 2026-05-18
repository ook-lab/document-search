"""
F17: Stage F データ平面の出口。

- `consolidated_tables` … F11 の表リスト（結合・加工なし）
- `reading_stream` … 地の文ブロックと表を座標順に並べた読み順正本
- `non_table_text` … F13 用の地の文連結（読み順正本ではない）
"""

from typing import Dict, Any, List
from loguru import logger

from dms.pipeline.stage_f.f17_reading_stream import build_f17_reading_stream


class F17StageFFinalize:
    """F17: Stage F チェーン終端（統合 dict の整形のみ）"""

    def join(
        self,
        merge_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """F13 からの merge_result を Stage F 出口形式に整える。"""
        tables = merge_result.get('tables', [])
        join_result = self.join_tables(tables)
        consolidated_tables = join_result['joined_tables']
        logger.info(
            f"[F17] consolidated_tables={len(consolidated_tables)} "
            f"（配置前結合なし・結合は F59）"
        )

        merge_result['consolidated_tables'] = consolidated_tables

        doc_info = merge_result.get('document_info', {})
        reading_stream = build_f17_reading_stream(
            prose_blocks=merge_result.get('non_table_text_blocks') or [],
            tables=consolidated_tables,
            document_info=doc_info if isinstance(doc_info, dict) else None,
        )

        meta = dict(merge_result.get('metadata') or {})
        meta['f17_reading_stream_items'] = len(reading_stream)

        result = {
            'success': True,
            'document_info': doc_info,
            'normalized_events': merge_result.get('normalized_events', merge_result.get('events', [])),
            'tasks': merge_result.get('tasks', []),
            'notices': merge_result.get('notices', []),
            'consolidated_tables': consolidated_tables,
            'reading_stream': reading_stream,
            'raw_integrated_text': merge_result.get('raw_integrated_text', ''),
            'non_table_text': merge_result.get('non_table_text', ''),
            'non_table_text_blocks': merge_result.get('non_table_text_blocks') or [],
            'metadata': meta,
            'display_fields': merge_result.get('display_fields'),
            'all_dates': merge_result.get('all_dates', []),
            'stage_d_line_digest': merge_result.get('stage_d_line_digest'),
            'stage_d_cell_bundle': merge_result.get('stage_d_cell_bundle'),
        }

        logger.info("=" * 60)
        logger.info("[F17] Stage F データ平面完了")
        logger.info(f"  ├─ イベント: {len(result['normalized_events'])}件")
        logger.info(f"  ├─ タスク: {len(result['tasks'])}件")
        logger.info(f"  ├─ 注意事項: {len(result['notices'])}件")
        logger.info(f"  ├─ 表: {len(consolidated_tables)}個")
        logger.info(f"  ├─ reading_stream: {len(reading_stream)}件")
        logger.info(f"  └─ 総トークン: {result['metadata'].get('total_tokens', 0)}")
        logger.info("=" * 60)

        return result

    def join_tables(
        self,
        tables: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """配置前は結合せずそのまま返す。"""
        passed = list(tables or [])
        if passed:
            logger.info(f"[F17] 表 {len(passed)} 件をパススルー")
        return {
            'success': True,
            'joined_tables': passed,
            'join_count': 0,
        }
