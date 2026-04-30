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

    def __init__(self, table_chain=None, text_chain=None):
        """
        Noise Eliminator 初期化

        Args:
            table_chain: 表処理チェーン（G-11）のインスタンス
            text_chain: テキスト処理チェーン（G-21）のインスタンス
        """
        self.table_chain = table_chain
        self.text_chain = text_chain

    def eliminate(
        self,
        g3_result: Dict[str, Any],
        log_file=None,
    ) -> Dict[str, Any]:
        """
        ノイズを除去してクリーンなUI用データを生成

        Args:
            g3_result: G-3の結果（直前ステージのみ）
            log_file: ログファイルパス（オプション）

        Returns:
            {
                'success': bool,
                'ui_data': dict,  # クリーンなUI用データ
                'size_reduction': str  # データサイズ削減率
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G-5]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._eliminate_impl(g3_result)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _eliminate_impl(
        self,
        g3_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """eliminate() の実装本体"""
        logger.info("[G-5] ノイズ除去開始")

        try:
            # ★G-3の結果から必要なデータを取得（直前ステージのみ）
            blocks = g3_result.get('blocks', [])
            events = g3_result.get('events', [])
            tasks = g3_result.get('tasks', [])
            notices = g3_result.get('notices', [])
            document_info = g3_result.get('document_info', {})
            ui_tables = g3_result.get('ui_tables', [])
            display_fields = g3_result.get('display_fields')

            # ドキュメント情報（必要最小限）
            clean_doc_info = {
                'document_type': document_info.get('document_type', 'unknown'),
                'year_context': document_info.get('year_context')
            }

            # イベント（正規化済み）
            clean_events = self._clean_events(events)

            # タスク
            clean_tasks = self._clean_tasks(tasks)

            # 注意事項
            clean_notices = self._clean_notices(notices)

            # UI用データを構築
            # ★sectionsはG-3のblocksをそのまま渡す（地の文用）
            ui_data = {
                'document_info': clean_doc_info,
                'sections': blocks,  # G-21用：地の文ブロック
                'tables': ui_tables,  # G-11用：表データ
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

            logger.info(f"[G-5] ✓ G-21用sections: {len(blocks)}件")
            logger.info(f"[G-5] ✓ G-11用tables: {len(ui_tables)}件")

            logger.info("[G-5] ノイズ除去完了:")
            logger.info(f"  ├─ セクション: {len(blocks)}")
            logger.info(f"  ├─ 表: {len(ui_tables)}")
            logger.info(f"  ├─ イベント: {len(clean_events)}")
            logger.info(f"  ├─ タスク: {len(clean_tasks)}")
            logger.info(f"  └─ 注意事項: {len(clean_notices)}")

            result = {
                'success': True,
                'ui_data': ui_data,
                'size_reduction': 'N/A'
            }

            # ★チェーン: 次のステージを呼び出す（分岐）
            if self.table_chain or self.text_chain:
                logger.info("[G-5] → 次のステージを呼び出します（分岐）")

                # 表処理チェーン（G-11 → G-17）
                if self.table_chain:
                    logger.info("[G-5]   ├─ 表処理チェーン（G-11）")
                    table_result = self.table_chain.structure(ui_tables, year_context=document_info.get('year_context'))
                    result['table_result'] = table_result

                    # ★G-11の結果をui_dataに反映
                    if table_result.get('success'):
                        structured_tables = table_result.get('structured_tables', [])
                        logger.info(f"[G-5] ✓ G-11構造化表をui_dataに追加: {len(structured_tables)}表")
                        ui_data['g11_structured_tables'] = structured_tables

                # テキスト処理チェーン（G-21 → G-22）
                # ★G-6廃止: B プロセッサで表領域を除外済みのため、フィルター不要
                if self.text_chain:
                    logger.info("[G-5]   └─ テキスト処理チェーン（G-21）")
                    text_result = self.text_chain.structure(
                        sections=blocks,  # ★フィルター不要（B プロセッサで表領域除外済み）
                        timeline=ui_data.get('timeline', []),
                        actions=ui_data.get('actions', []),
                        notices=ui_data.get('notices', []),
                        year_context=document_info.get('year_context'),  # ★年情報を渡す
                        display_fields=display_fields,  # ★Supabase display_* 個別フィールド
                    )
                    result['text_result'] = text_result

                    # ★G-21の結果をui_dataに反映
                    if text_result.get('success'):
                        metadata = text_result.get('metadata', {})
                        articles = metadata.get('articles', [])
                        logger.info(f"[G-5] ✓ G-21 articlesをui_dataに追加: {len(articles)}件")
                        ui_data['g21_articles'] = articles

                        # G-22の結果も反映（text_resultはG-22の結果を含む）
                        if 'calendar_events' in text_result:
                            ui_data['timeline'] = text_result.get('calendar_events', ui_data['timeline'])
                        if 'tasks' in text_result:
                            ui_data['actions'] = text_result.get('tasks', ui_data['actions'])
                        if 'notices' in text_result:
                            ui_data['notices'] = text_result.get('notices', ui_data['notices'])

            return result

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
