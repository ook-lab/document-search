"""
G-21: Text Structurer（テキストの構造化）

Stage G で生成された sections を、metadata.articles 形式に構造化する。
AI不要、純粋な変換処理。

目的：
- 全文を articles 形式で保存
- UI で即座に表示可能
"""

from typing import Dict, Any, List, Optional
from loguru import logger
from shared.common.database.client import DatabaseClient


class G21TextStructurer:
    """G-21: Text Structurer（テキストの構造化）"""

    def __init__(self, document_id=None, next_stage=None):
        """
        Text Structurer 初期化

        Args:
            document_id: ドキュメントID（Supabase保存用）
            next_stage: 次のステージ（G-22）のインスタンス
        """
        self.document_id = document_id
        self.next_stage = next_stage

    def structure(
        self,
        sections: List[Dict[str, Any]],
        timeline: List[Dict[str, Any]],
        actions: List[Dict[str, Any]],
        notices: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        log_file=None,
        display_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        sections を metadata.articles 形式に構造化

        Args:
            sections: Stage G の sections（ブロックリスト）
            timeline: イベント・予定
            actions: タスク
            notices: 注意事項
            year_context: 年度コンテキスト（G-22に渡す）
            log_file: ログファイルパス（オプション）

        Returns:
            {
                'success': bool,
                'metadata': dict  # articles, calendar_events, tasks, notices
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G-21]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._structure_impl(sections, timeline, actions, notices, year_context, display_fields)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _structure_impl(
        self,
        sections: List[Dict[str, Any]],
        timeline: List[Dict[str, Any]],
        actions: List[Dict[str, Any]],
        notices: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        display_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """structure() の実装本体"""
        logger.info("[G-21] ========================================")
        logger.info("[G-21] テキストの構造化開始")
        logger.info("[G-21] ========================================")

        try:
            # 入力データのサマリーをログ出力
            logger.info("[G-21]")
            logger.info("[G-21] ========== 入力データ詳細 ==========")
            logger.info(f"[G-21] sections: {len(sections)}件")
            logger.info(f"[G-21] timeline: {len(timeline)}件")
            logger.info(f"[G-21] actions: {len(actions)}件")
            logger.info(f"[G-21] notices: {len(notices)}件")
            logger.info("[G-21]")

            # 各 section の詳細をログ出力
            if sections:
                logger.info("[G-21] ===== sections の詳細 =====")
                for i, section in enumerate(sections, 1):
                    section_type = section.get('type', 'unknown')
                    label = section.get('label', '(ラベルなし)')
                    content = section.get('content', '')

                    logger.info(f"[G-21] Section {i}:")
                    logger.info(f"[G-21]   type: {section_type}")
                    logger.info(f"[G-21]   label: {label}")
                    logger.info(f"[G-21]   content length: {len(content)}文字")
                    logger.info(f"[G-21]   content: {content}")
                    logger.info("[G-21]")
                logger.info("[G-21] " + "=" * 60)

            articles = []

            # ─────────────────────────────────────────────────────
            # ① display_* フィールドを個別 article として先頭に追加
            #    投稿文・添付テキストとは混ぜない
            # ─────────────────────────────────────────────────────
            logger.info("[G-21]")
            logger.info("[G-21] display_* フィールドから articles を生成中...")
            DISPLAY_FIELD_ORDER = ['送信者', 'メール', '送信日時', '件名', '本文']
            if display_fields:
                for label in DISPLAY_FIELD_ORDER:
                    value = display_fields.get(label)
                    if value:
                        articles.append({'title': label, 'body': str(value)})
                        logger.info(f"[G-21]   ✓ display_fields['{label}'] → article: {len(str(value))}文字")
            else:
                logger.info("[G-21]   (display_fields なし)")

            # ─────────────────────────────────────────────────────
            # ② sections から type='text' のブロックを添付テキスト article として追加
            # ─────────────────────────────────────────────────────
            logger.info("[G-21]")
            logger.info("[G-21] type='text' のセクションから添付テキスト articles を生成中...")
            for i, section in enumerate(sections, 1):
                if section.get('type') == 'text':
                    body = section.get('content', '')
                    if body and body.strip():
                        title = section.get('label')
                        articles.append({'title': title, 'body': body})
                        logger.info(f"[G-21]   ✓ Section {i} を article に変換: title='{title}', body={len(body)}文字")

            logger.info(f"[G-21] articles 生成完了: {len(articles)}件")

            # metadata を構築
            metadata = {
                'articles': articles,
                'calendar_events': timeline,
                'tasks': actions,
                'notices': notices
            }

            # 生成された articles の詳細をログ出力
            logger.info("[G-21]")
            logger.info("[G-21] ========== 生成された articles ==========")
            if articles:
                for i, article in enumerate(articles, 1):
                    title = article.get('title', '(タイトルなし)')
                    body = article.get('body', '')
                    logger.info(f"[G-21] Article {i}:")
                    logger.info(f"[G-21]   title: {title}")
                    logger.info(f"[G-21]   body length: {len(body)}文字")
                    logger.info(f"[G-21]   body:")
                    logger.info(f"[G-21]     {body}")
                    logger.info("[G-21]")
            else:
                logger.info("[G-21] (articles なし)")
            logger.info("[G-21] " + "=" * 60)

            # 最終メタデータのサマリー
            logger.info("[G-21]")
            logger.info("[G-21] ========== 最終メタデータサマリー ==========")
            logger.info(f"[G-21]   ├─ articles: {len(articles)}件")
            logger.info(f"[G-21]   ├─ calendar_events: {len(timeline)}件")
            logger.info(f"[G-21]   ├─ tasks: {len(actions)}件")
            logger.info(f"[G-21]   └─ notices: {len(notices)}件")
            logger.info("[G-21] " + "=" * 60)

            logger.info("[G-21]")
            logger.info("[G-21] ========================================")
            logger.info("[G-21] 構造化完了")
            logger.info("[G-21] ========================================")

            result = {
                'success': True,
                'metadata': metadata
            }

            # Supabaseに保存
            if self.document_id:
                try:
                    db = DatabaseClient(use_service_role=True)
                    db.client.table('Rawdata_FILE_AND_MAIL').update({
                        'g21_articles': articles
                    }).eq('id', self.document_id).execute()
                    logger.info(f"[G-21] ✓ g21_articles を Supabase に保存: {len(articles)}件")
                except Exception as e:
                    logger.error(f"[G-21] Supabase保存エラー: {e}")

            # ★チェーン: 次のステージ（G-22）を呼び出す
            if self.next_stage:
                logger.info("[G-21] → 次のステージ（G-22）を呼び出します")
                g22_result = self.next_stage.process(articles, year_context=year_context)

                # G-22の結果を result にマージ（metadata は G-21 のものを保持）
                if g22_result.get('success'):
                    # G-22の抽出結果を result に追加
                    result['calendar_events'] = g22_result.get('calendar_events', [])
                    result['tasks'] = g22_result.get('tasks', [])
                    result['notices'] = g22_result.get('notices', [])
                    # G-22のtopic_sectionsをresultに追加（後段ステージへの伝達用）
                    if g22_result.get('topic_sections'):
                        result['topic_sections'] = g22_result['topic_sections']
                    logger.info(f"[G-21] ✓ G-22の結果をマージ: イベント{len(result['calendar_events'])}件, タスク{len(result['tasks'])}件, topic_sections{len(result.get('topic_sections', []))}件")

            return result

        except Exception as e:
            logger.error("[G-21] ========================================")
            logger.error(f"[G-21] 構造化エラー: {e}")
            logger.error("[G-21] ========================================")
            logger.error("", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'metadata': {}
            }
