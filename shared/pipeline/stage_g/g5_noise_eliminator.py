"""
G-5: Noise Elimination（ノイズレス・デリバリー）

AIの推論過程、重複した座標データ、システムログなどを除去し、
表示に必要な「正解データ」のみを抽出する。

目的:
1. UI表示に不要なデータを完全除去
2. クリーンな表示用スキーマの完成
3. フロントエンドでの即時描画を保証
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger


class G5NoiseEliminator:
    """G-5: Noise Elimination（ノイズ除去）"""

    def __init__(self):
        """Noise Eliminator 初期化"""
        pass

    def eliminate(
        self,
        stage_f_result: Dict[str, Any],
        ui_tables: List[Dict[str, Any]],
        blocks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        ノイズを除去してクリーンなUI用データを生成

        Args:
            stage_f_result: Stage F の結果
            ui_tables: G-1 の UI用表データ
            blocks: G-3 のブロックデータ

        Returns:
            {
                'success': bool,
                'ui_data': dict,  # クリーンなUI用データ
                'size_reduction': str  # データサイズ削減率
            }
        """
        logger.info("[G-5] ノイズ除去開始")

        try:
            # ドキュメント情報（必要最小限）
            doc_info = stage_f_result.get('document_info', {})
            clean_doc_info = {
                'document_type': doc_info.get('document_type', 'unknown'),
                'year_context': doc_info.get('year_context')
            }

            # イベント（正規化済み）
            events = stage_f_result.get('normalized_events', [])
            clean_events = self._clean_events(events)

            # タスク
            tasks = stage_f_result.get('tasks', [])
            clean_tasks = self._clean_tasks(tasks)

            # 注意事項
            notices = stage_f_result.get('notices', [])
            clean_notices = self._clean_notices(notices)

            # UI用データを構築
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

            logger.info("[G-5] ノイズ除去完了:")
            logger.info(f"  ├─ セクション: {len(blocks)}")
            logger.info(f"  ├─ 表: {len(ui_tables)}")
            logger.info(f"  ├─ イベント: {len(clean_events)}")
            logger.info(f"  ├─ タスク: {len(clean_tasks)}")
            logger.info(f"  └─ 注意事項: {len(clean_notices)}")

            return {
                'success': True,
                'ui_data': ui_data,
                'size_reduction': 'N/A'
            }

        except Exception as e:
            logger.error(f"[G-5] ノイズ除去エラー: {e}", exc_info=True)
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
        """
        イベントデータをクリーニング

        Args:
            events: イベントリスト

        Returns:
            クリーンなイベントリスト
        """
        clean_events = []

        for event in events:
            clean_event = {
                'date': event.get('normalized_date') or event.get('date'),
                'time': event.get('normalized_time') or event.get('time'),
                'event': event.get('event') or event.get('original_text', ''),
                'location': event.get('location', '')
            }

            # 空のフィールドを除去
            clean_event = {k: v for k, v in clean_event.items() if v}

            clean_events.append(clean_event)

        return clean_events

    def _clean_tasks(
        self,
        tasks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        タスクデータをクリーニング

        Args:
            tasks: タスクリスト

        Returns:
            クリーンなタスクリスト
        """
        clean_tasks = []

        for task in tasks:
            clean_task = {
                'deadline': task.get('deadline', ''),
                'item': task.get('item', ''),
                'description': task.get('description', ''),
                'priority': task.get('priority', 'normal')
            }

            # 空のフィールドを除去
            clean_task = {k: v for k, v in clean_task.items() if v}

            clean_tasks.append(clean_task)

        return clean_tasks

    def _clean_notices(
        self,
        notices: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        注意事項をクリーニング

        Args:
            notices: 注意事項リスト

        Returns:
            クリーンな注意事項リスト
        """
        clean_notices = []

        for notice in notices:
            clean_notice = {
                'category': notice.get('category', ''),
                'content': notice.get('content', ''),
                'importance': notice.get('importance', 'normal')
            }

            # 空のフィールドを除去
            clean_notice = {k: v for k, v in clean_notice.items() if v}

            clean_notices.append(clean_notice)

        return clean_notices
