"""
F53: Noise Elimination（レビュー UI 用ノイズ除去・配線）

旧 G-5 / 旧 F-43。呼び出し側（F60）が組み立てた `sections` 用 `blocks` と Stage F（F17 まで）由来フィールドを受け取り、
クリーンな `ui_data` を生成し、表チェーン・テキストチェーンへ分岐する。

※ イベント・タスク・注意・本文を sections に並べる処理は **G19 外（F60）** で行う。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class G19UIAssembly:
    """F53: Noise Elimination（ノイズ除去・表チェーン分岐）"""

    def __init__(self, table_chain=None, text_chain=None):
        """
        Args:
            table_chain: 表処理チェーン（例: G24 起点）
            text_chain: テキスト処理チェーン（例: G-21 起点・呼び出し側で注入）
        """
        self.table_chain = table_chain
        self.text_chain = text_chain

    def eliminate(
        self,
        delivery: Dict[str, Any],
        log_file=None,
        table_log_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G19]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._eliminate_impl(delivery, table_log_dir=table_log_dir)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _eliminate_impl(
        self,
        delivery: Dict[str, Any],
        *,
        table_log_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        logger.info("[G19] ノイズ除去開始")

        try:
            blocks = delivery.get("blocks")
            if not isinstance(blocks, list):
                logger.error("[G19] delivery['blocks'] が list ではありません（G11 で sections を組み立ててください）")
                blocks = []
            raw_text = delivery.get("raw_text", "")
            logger.info(
                f"[G19] raw_text 全文({len(raw_text)}文字):\n{raw_text if raw_text else '（空）'}"
            )
            for block_idx, block in enumerate(blocks):
                logger.info(
                    f"[G19]   ブロック{block_idx}: type={block.get('type')} content={block.get('content')}"
                )

            events = delivery.get("events", [])
            tasks = delivery.get("tasks", [])
            notices = delivery.get("notices", [])
            document_info = delivery.get("document_info", {})
            ui_tables = delivery.get("ui_tables", [])
            display_fields = delivery.get("display_fields")
            stage_d_line_digest = delivery.get("stage_d_line_digest")

            clean_doc_info = {
                'document_type': document_info.get('document_type', 'unknown'),
                'year_context': document_info.get('year_context')
            }

            clean_events = self._clean_events(events)
            clean_tasks = self._clean_tasks(tasks)
            clean_notices = self._clean_notices(notices)

            ui_data = {
                'document_info': clean_doc_info,
                'sections': blocks,
                'tables': ui_tables,
                'timeline': clean_events,
                'actions': clean_tasks,
                'notices': clean_notices,
                'metadata': {
                    'section_count': len(blocks),
                    'table_count': len(ui_tables),
                    'event_count': len(clean_events),
                    'task_count': len(clean_tasks),
                    'notice_count': len(clean_notices)
                }
            }

            logger.info(f"[G19] ✓ テキストチェーン用 sections: {len(blocks)}件")
            logger.info(f"[G19] ✓ 表チェーン用 tables: {len(ui_tables)}件")

            logger.info("[G19] ノイズ除去完了:")
            logger.info(f"  ├─ セクション: {len(blocks)}")
            logger.info(f"  ├─ 表: {len(ui_tables)}")
            logger.info(f"  ├─ イベント: {len(clean_events)}")
            logger.info(f"  ├─ タスク: {len(clean_tasks)}")
            logger.info(f"  └─ 注意事項: {len(clean_notices)}")

            result = {
                'success': True,
                'ui_data': ui_data,
                'size_reduction': None
            }

            if self.table_chain or self.text_chain:
                logger.info("[G19] → 次のステージを呼び出します（分岐）")

                if self.table_chain:
                    logger.info("[G19]   ├─ 表処理チェーン")
                    tctx: Dict[str, Any] = {}
                    if stage_d_line_digest:
                        tctx['stage_d_line_digest'] = stage_d_line_digest
                    purged = delivery.get('purged_pdf_path')
                    if purged:
                        tctx['purged_pdf_path'] = purged
                    table_result = self.table_chain.structure(
                        ui_tables,
                        year_context=document_info.get('year_context'),
                        table_log_dir=table_log_dir,
                        chain_context=tctx,
                    )
                    result['table_result'] = table_result

                    if table_result.get('success'):
                        structured_tables = table_result.get('structured_tables', [])
                        logger.info(f"[G19] ✓ 構造化表を ui_data に追加: {len(structured_tables)}表")
                        ui_data['g11_structured_tables'] = structured_tables

                if self.text_chain:
                    logger.info("[G19]   └─ テキスト処理チェーン")
                    text_result = self.text_chain.structure(
                        sections=blocks,
                        timeline=ui_data.get('timeline', []),
                        actions=ui_data.get('actions', []),
                        notices=ui_data.get('notices', []),
                        year_context=document_info.get('year_context'),
                        display_fields=display_fields,
                    )
                    result['text_result'] = text_result

                    if text_result.get('success'):
                        metadata = text_result.get('metadata', {})
                        articles = metadata.get('articles', [])
                        logger.info(f"[G19] ✓ articles を ui_data に追加: {len(articles)}件")
                        ui_data['g21_articles'] = articles

                        if 'calendar_events' in text_result:
                            ui_data['timeline'] = text_result.get('calendar_events', ui_data['timeline'])
                        if 'tasks' in text_result:
                            ui_data['actions'] = text_result.get('tasks', ui_data['actions'])
                        if 'notices' in text_result:
                            ui_data['notices'] = text_result.get('notices', ui_data['notices'])

            return result

        except Exception as e:
            logger.error(f"[G19] ノイズ除去エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'ui_data': {},
                'size_reduction': '0%'
            }

    def _clean_events(
        self,
        events: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        clean_events = []

        for event in events:
            clean_event = {
                'date': event.get('normalized_date') or event.get('date'),
                'time': event.get('normalized_time') or event.get('time'),
                'event': event.get('event') or event.get('original_text', ''),
                'location': event.get('location', '')
            }

            clean_event = {k: v for k, v in clean_event.items() if v}

            clean_events.append(clean_event)

        return clean_events

    def _clean_tasks(
        self,
        tasks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        clean_tasks = []

        for task in tasks:
            clean_task = {
                'deadline': task.get('deadline', ''),
                'item': task.get('item', ''),
                'description': task.get('description', ''),
                'priority': task.get('priority', 'normal')
            }

            clean_task = {k: v for k, v in clean_task.items() if v}

            clean_tasks.append(clean_task)

        return clean_tasks

    def _clean_notices(
        self,
        notices: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        clean_notices = []

        for notice in notices:
            clean_notice = {
                'category': notice.get('category', ''),
                'content': notice.get('content', ''),
                'importance': notice.get('importance', 'normal')
            }

            clean_notice = {k: v for k, v in clean_notice.items() if v}

            clean_notices.append(clean_notice)

        return clean_notices
