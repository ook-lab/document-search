"""
Stage J: Chunking (チャンク化)

メタデータからチャンクを生成
- 役割: 検索用チャンクの作成
- 処理: MetadataChunker でメタデータチャンク生成
"""
from typing import Dict, Any, List
from loguru import logger

from shared.common.processing.metadata_chunker import MetadataChunker


class StageJChunking:
    """Stage J: チャンク化"""

    def __init__(self):
        """初期化"""
        self.chunker = MetadataChunker()

    def create_chunks(
        self,
        display_subject: str,
        summary: str,
        tags: List[str],
        document_date: str,
        metadata: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        メタデータからチャンクを生成

        Args:
            display_subject: 件名/ファイル名
            summary: 要約
            tags: タグリスト
            document_date: ドキュメント日付
            metadata: 構造化メタデータ

        Returns:
            チャンクリスト [
                {
                    'chunk_text': str,
                    'chunk_type': str,
                    'search_weight': float
                },
                ...
            ]
        """
        logger.info("[Stage J] チャンク化開始...")

        try:
            # metadata から構造化データを展開
            document_data = {
                'file_name': display_subject,
                'summary': summary,
                'tags': tags,
                'document_date': document_date,
                # metadata の中身を直接展開
                'persons': metadata.get('persons', []) if isinstance(metadata, dict) else [],
                'organizations': metadata.get('organizations', []) if isinstance(metadata, dict) else [],
                'people': metadata.get('people', []) if isinstance(metadata, dict) else [],
                # Stage H の構造化データを追加
                'text_blocks': metadata.get('text_blocks', []) if isinstance(metadata, dict) else [],
                'structured_tables': metadata.get('structured_tables', []) if isinstance(metadata, dict) else [],
                'weekly_schedule': metadata.get('weekly_schedule', []) if isinstance(metadata, dict) else [],
                'other_text': metadata.get('other_text', []) if isinstance(metadata, dict) else [],
                # Stage I の抽出データを追加
                'calendar_events': metadata.get('calendar_events', []) if isinstance(metadata, dict) else [],
                'tasks': metadata.get('tasks', []) if isinstance(metadata, dict) else [],
                # basic_info も展開（あれば）
                'doc_type': metadata.get('basic_info', {}).get('related_class', '') if isinstance(metadata, dict) else ''
            }

            chunks = self.chunker.create_metadata_chunks(document_data)

            logger.info(f"[Stage J完了] チャンク数: {len(chunks)}")

            return chunks

        except Exception as e:
            logger.error(f"[Stage J エラー] チャンク化失敗: {e}", exc_info=True)
            return []

    def process(
        self,
        display_subject: str,
        summary: str,
        tags: List[str],
        document_date: Any,
        metadata: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        チャンク生成（process() エイリアス）

        Args:
            display_subject: 表示件名
            summary: 要約
            tags: タグ
            document_date: ドキュメント日付
            metadata: メタデータ

        Returns:
            chunks: チャンクリスト
        """
        return self.create_chunks(
            display_subject=display_subject,
            summary=summary,
            tags=tags,
            document_date=document_date,
            metadata=metadata
        )
