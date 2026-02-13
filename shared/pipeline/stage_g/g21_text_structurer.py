"""
G-21: Text Structurer（テキストの構造化）

Stage G で生成された sections を、metadata.articles 形式に構造化する。
AI不要、純粋な変換処理。

目的：
- 全文を articles 形式で保存
- UI で即座に表示可能
"""

from typing import Dict, Any, List
from loguru import logger


class G21TextStructurer:
    """G-21: Text Structurer（テキストの構造化）"""

    def __init__(self):
        """Text Structurer 初期化"""
        pass

    def structure(
        self,
        sections: List[Dict[str, Any]],
        timeline: List[Dict[str, Any]],
        actions: List[Dict[str, Any]],
        notices: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        sections を metadata.articles 形式に構造化

        Args:
            sections: Stage G の sections（ブロックリスト）
            timeline: イベント・予定
            actions: タスク
            notices: 注意事項

        Returns:
            {
                'success': bool,
                'metadata': dict  # articles, calendar_events, tasks, notices
            }
        """
        logger.info("[G-21] テキストの構造化開始")

        try:
            # sections から type='text' のブロックを抽出して articles に変換
            articles = []
            for section in sections:
                if section.get('type') == 'text':
                    body = section.get('content', '')
                    if body and body.strip():  # 空でない場合のみ追加
                        articles.append({
                            'title': section.get('label'),
                            'body': body
                        })

            # metadata を構築
            metadata = {
                'articles': articles,
                'calendar_events': timeline,
                'tasks': actions,
                'notices': notices
            }

            logger.info(f"[G-21] 構造化完了:")
            logger.info(f"  ├─ articles: {len(articles)}件")
            logger.info(f"  ├─ calendar_events: {len(timeline)}件")
            logger.info(f"  ├─ tasks: {len(actions)}件")
            logger.info(f"  └─ notices: {len(notices)}件")

            return {
                'success': True,
                'metadata': metadata
            }

        except Exception as e:
            logger.error(f"[G-21] 構造化エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'metadata': {}
            }
