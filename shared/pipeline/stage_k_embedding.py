"""
Stage K: Embedding (ベクトル化)

チャンクをベクトル化して search_index に保存
- 役割: チャンクをベクトル化
- モデル: OpenAI text-embedding-3-small (1536次元)
"""
from typing import Dict, Any, List
from loguru import logger

from shared.ai.llm_client.llm_client import LLMClient
from shared.common.database.client import DatabaseClient


class StageKEmbedding:
    """Stage K: ベクトル化"""

    def __init__(self, llm_client: LLMClient, db_client: DatabaseClient):
        """
        Args:
            llm_client: LLMクライアント
            db_client: データベースクライアント
        """
        self.llm_client = llm_client
        self.db = db_client

    def embed_and_save(
        self,
        document_id: str,
        chunks: List[Dict[str, Any]],
        delete_existing: bool = False,
        owner_id: str = None
    ) -> Dict[str, Any]:
        """
        チャンクをベクトル化して search_index に保存

        Args:
            document_id: ドキュメントID
            chunks: チャンクリスト
            delete_existing: 既存のチャンクを削除するか
            owner_id: オーナーID（省略時は親ドキュメントから継承）

        Returns:
            {
                'success': bool,
                'saved_count': int,
                'failed_count': int
            }
        """
        logger.info("[Stage K] ベクトル化 + search_index保存開始...")

        # Phase 3: owner_id を取得（指定がない場合は親ドキュメントから継承）
        if owner_id is None:
            try:
                parent_doc = self.db.client.table('Rawdata_FILE_AND_MAIL')\
                    .select('owner_id')\
                    .eq('id', document_id)\
                    .execute()
                if parent_doc.data:
                    owner_id = parent_doc.data[0].get('owner_id')
                    logger.debug(f"[Stage K] 親ドキュメントから owner_id 継承: {owner_id}")
            except Exception as e:
                logger.warning(f"[Stage K 警告] 親ドキュメントの owner_id 取得エラー: {e}")

        if owner_id is None:
            logger.error(f"[Stage K エラー] owner_id が取得できません: document_id={document_id}")
            return {
                'success': False,
                'saved_count': 0,
                'failed_count': len(chunks),
                'error': 'owner_id is required but not available'
            }

        # 既存ドキュメントの場合は、古いチャンクを削除
        if delete_existing:
            try:
                logger.info(f"[Stage K] 既存チャンク削除: document_id={document_id}")
                self.db.client.table('10_ix_search_index').delete().eq('document_id', document_id).execute()
            except Exception as e:
                logger.warning(f"[Stage K 警告] 既存チャンク削除エラー（継続）: {e}")

        saved_count = 0
        failed_count = 0
        errors = []

        for chunk in chunks:
            try:
                # null文字を除去
                chunk_text = chunk['chunk_text'].replace('\u0000', '') if chunk['chunk_text'] else ''

                if not chunk_text.strip():
                    logger.warning(f"[Stage K] 空チャンクをスキップ: type={chunk.get('chunk_type')}")
                    continue

                # Embedding生成
                embedding = self.llm_client.generate_embedding(chunk_text)

                # search_indexに保存
                chunk_data = {
                    'document_id': document_id,
                    'owner_id': owner_id,  # Phase 3: 親ドキュメントから継承
                    'chunk_content': chunk_text,
                    'chunk_size': len(chunk_text),
                    'chunk_type': chunk['chunk_type'],
                    'embedding': embedding,
                    'search_weight': chunk.get('search_weight', 1.0),
                    'chunk_index': chunk.get('chunk_index', 0),
                    'chunk_metadata': chunk.get('metadata')  # 構造化データを保存
                }

                self.db.client.table('10_ix_search_index').insert(chunk_data).execute()
                saved_count += 1

            except Exception as e:
                err_msg = f"type={chunk.get('chunk_type')}: {e}"
                logger.error(f"[Stage K エラー] チャンク保存失敗: {err_msg}")
                errors.append(err_msg)
                failed_count += 1

        logger.info(f"[Stage K完了] {saved_count}/{len(chunks)}チャンクを保存 (失敗: {failed_count})")

        # chunk_countを更新（カラムが存在しない場合はスキップ）
        if saved_count > 0:
            try:
                result = self.db.client.table('Rawdata_FILE_AND_MAIL')\
                    .update({'chunk_count': saved_count})\
                    .eq('id', document_id)\
                    .execute()
                logger.debug(f"[Stage K] chunk_count更新: {saved_count}個")
            except Exception:
                pass  # chunk_countカラムなし

        # 成功条件: 最低1チャンク以上保存 & 失敗なし
        is_success = saved_count > 0 and failed_count == 0

        return {
            'success': is_success,
            'saved_count': saved_count,
            'failed_count': failed_count,
            'errors': errors,
        }

    def process(self, chunks: List[Dict[str, Any]], document_id: str) -> None:
        """
        チャンクをベクトル化して保存（process() エイリアス）

        Args:
            chunks: チャンクリスト
            document_id: ドキュメントID
        """
        self.embed_and_save(document_id, chunks)
