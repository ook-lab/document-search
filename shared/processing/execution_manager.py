"""
ExecutionManager - ドキュメント処理の実行履歴管理

Phase 5: Non-Destructive Execution

【設計原則】
- AI推論結果は上書きしない（常に新しい execution を作成）
- documents.active_execution_id で「採用結果」を切り替え
- 失敗しても過去の成功結果は保持される

【使い方】
    from shared.processing.execution_manager import ExecutionManager

    manager = ExecutionManager(db_client)

    # 1. execution 作成
    execution_id = manager.create_execution(
        document_id=doc_id,
        owner_id=owner_id,
        input_text=combined_text,
        model_version="gemini-2.5-flash"
    )

    # 2. 処理実行
    try:
        result = await process_document(...)
        # 3a. 成功時: succeeded に更新 + active 切り替え
        manager.mark_succeeded(
            execution_id=execution_id,
            result_data={'summary': ..., 'metadata': ...},
            processing_duration_ms=duration
        )
    except Exception as e:
        # 3b. 失敗時: failed に更新（active は変更しない）
        manager.mark_failed(
            execution_id=execution_id,
            error_code='PROCESSING_ERROR',
            error_message=str(e)
        )
"""
import hashlib
import json
from typing import Dict, Any, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class ExecutionContext:
    """実行コンテキスト（処理中に保持する情報）"""
    execution_id: str
    document_id: str
    owner_id: str
    input_hash: str
    model_version: Optional[str] = None


class ExecutionManager:
    """
    ドキュメント処理の実行履歴管理

    Phase 5: 非破壊的実行の実現
    """

    def __init__(self, db_client=None):
        """
        Args:
            db_client: DatabaseClient インスタンス（省略時は内部で作成）
        """
        if db_client is None:
            from shared.common.database.client import DatabaseClient
            db_client = DatabaseClient(use_service_role=True)
        self.db = db_client

    @staticmethod
    def compute_input_hash(input_text: str, metadata: Optional[Dict] = None) -> str:
        """
        入力のハッシュを計算

        Args:
            input_text: 推論入力テキスト
            metadata: 追加メタデータ（ハッシュに含める場合）

        Returns:
            SHA-256 ハッシュ（hex）
        """
        content = input_text or ""
        if metadata:
            # メタデータを安定した形式で追加
            content += "\n---METADATA---\n"
            content += json.dumps(metadata, sort_keys=True, ensure_ascii=False)

        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    @staticmethod
    def compute_normalized_hash(normalized_text: str) -> str:
        """
        正規化後テキストのハッシュを計算

        Args:
            normalized_text: 前処理後のテキスト

        Returns:
            SHA-256 ハッシュ（hex）
        """
        content = normalized_text or ""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def create_execution(
        self,
        document_id: str,
        owner_id: str,
        input_text: str,
        model_version: Optional[str] = None,
        prompt_hash: Optional[str] = None,
        normalized_text: Optional[str] = None,
        retry_of_execution_id: Optional[str] = None,
        input_metadata: Optional[Dict] = None
    ) -> ExecutionContext:
        """
        新しい execution を作成

        Args:
            document_id: 対象ドキュメントID
            owner_id: データ所有者ID
            input_text: 推論入力テキスト
            model_version: 使用モデル
            prompt_hash: プロンプトのハッシュ
            normalized_text: 正規化後テキスト（省略時は input_text と同じ）
            retry_of_execution_id: リトライ元の execution_id
            input_metadata: 入力ハッシュに含めるメタデータ

        Returns:
            ExecutionContext: 実行コンテキスト
        """
        input_hash = self.compute_input_hash(input_text, input_metadata)
        normalized_hash = self.compute_normalized_hash(normalized_text or input_text)

        execution_data = {
            'document_id': document_id,
            'owner_id': owner_id,
            'status': 'running',
            'model_version': model_version,
            'prompt_hash': prompt_hash,
            'input_hash': input_hash,
            'normalized_hash': normalized_hash,
            'retry_of_execution_id': retry_of_execution_id,
            'result_data': {}
        }

        result = self.db.client.table('document_executions').insert(execution_data).execute()

        if not result.data or len(result.data) == 0:
            raise RuntimeError("Failed to create execution record")

        execution_id = result.data[0]['id']
        logger.info(f"[Execution] 作成: {execution_id[:8]}... (doc={document_id[:8]}...)")

        return ExecutionContext(
            execution_id=execution_id,
            document_id=document_id,
            owner_id=owner_id,
            input_hash=input_hash,
            model_version=model_version
        )

    def mark_succeeded(
        self,
        execution_id: str,
        result_data: Dict[str, Any],
        processing_duration_ms: Optional[int] = None,
        update_active: bool = True
    ) -> bool:
        """
        execution を成功としてマーク

        Args:
            execution_id: 対象 execution ID
            result_data: AI 推論結果（summary, metadata, chunks など）
            processing_duration_ms: 処理時間（ミリ秒）
            update_active: True の場合、documents.active_execution_id を更新

        Returns:
            成功したかどうか
        """
        try:
            # execution を succeeded に更新
            update_data = {
                'status': 'succeeded',
                'result_data': result_data,
                'processing_duration_ms': processing_duration_ms
            }

            result = self.db.client.table('document_executions') \
                .update(update_data) \
                .eq('id', execution_id) \
                .execute()

            if not result.data:
                logger.error(f"[Execution] 更新失敗: {execution_id}")
                return False

            document_id = result.data[0]['document_id']
            logger.info(f"[Execution] 成功: {execution_id[:8]}...")

            # active_execution_id を更新
            if update_active:
                self._update_active_execution(document_id, execution_id)

            return True

        except Exception as e:
            logger.error(f"[Execution] mark_succeeded エラー: {e}")
            return False

    def mark_failed(
        self,
        execution_id: str,
        error_code: str,
        error_message: str,
        processing_duration_ms: Optional[int] = None
    ) -> bool:
        """
        execution を失敗としてマーク

        注: active_execution_id は変更しない（前の成功結果を保持）

        Args:
            execution_id: 対象 execution ID
            error_code: エラーコード
            error_message: エラーメッセージ
            processing_duration_ms: 処理時間（ミリ秒）

        Returns:
            成功したかどうか
        """
        try:
            update_data = {
                'status': 'failed',
                'error_code': error_code,
                'error_message': error_message,
                'processing_duration_ms': processing_duration_ms
            }

            result = self.db.client.table('document_executions') \
                .update(update_data) \
                .eq('id', execution_id) \
                .execute()

            if not result.data:
                logger.error(f"[Execution] 更新失敗: {execution_id}")
                return False

            logger.warning(f"[Execution] 失敗: {execution_id[:8]}... ({error_code})")
            return True

        except Exception as e:
            logger.error(f"[Execution] mark_failed エラー: {e}")
            return False

    def mark_canceled(self, execution_id: str) -> bool:
        """
        execution をキャンセルとしてマーク

        Args:
            execution_id: 対象 execution ID

        Returns:
            成功したかどうか
        """
        try:
            result = self.db.client.table('document_executions') \
                .update({'status': 'canceled'}) \
                .eq('id', execution_id) \
                .execute()

            if not result.data:
                return False

            logger.info(f"[Execution] キャンセル: {execution_id[:8]}...")
            return True

        except Exception as e:
            logger.error(f"[Execution] mark_canceled エラー: {e}")
            return False

    def _update_active_execution(self, document_id: str, execution_id: str) -> bool:
        """
        documents.active_execution_id を更新

        Args:
            document_id: 対象ドキュメントID
            execution_id: 新しい active execution ID

        Returns:
            成功したかどうか
        """
        try:
            result = self.db.client.table('Rawdata_FILE_AND_MAIL') \
                .update({'active_execution_id': execution_id}) \
                .eq('id', document_id) \
                .execute()

            if result.data:
                logger.info(f"[Execution] active 切り替え: doc={document_id[:8]}... -> exec={execution_id[:8]}...")
                return True

            return False

        except Exception as e:
            logger.error(f"[Execution] active 更新エラー: {e}")
            return False

    def find_existing_execution(
        self,
        document_id: str,
        input_hash: str
    ) -> Optional[Dict[str, Any]]:
        """
        同一入力の成功 execution を検索（冪等性のため）

        Args:
            document_id: 対象ドキュメントID
            input_hash: 入力ハッシュ

        Returns:
            既存の execution（なければ None）
        """
        try:
            result = self.db.client.table('document_executions') \
                .select('*') \
                .eq('document_id', document_id) \
                .eq('input_hash', input_hash) \
                .eq('status', 'succeeded') \
                .order('created_at', desc=True) \
                .limit(1) \
                .execute()

            if result.data:
                return result.data[0]
            return None

        except Exception as e:
            logger.warning(f"[Execution] 既存検索エラー: {e}")
            return None

    def get_execution_history(
        self,
        document_id: str,
        limit: int = 10
    ) -> list:
        """
        ドキュメントの execution 履歴を取得

        Args:
            document_id: 対象ドキュメントID
            limit: 最大件数

        Returns:
            execution のリスト（最新順）
        """
        try:
            result = self.db.client.table('document_executions') \
                .select('*') \
                .eq('document_id', document_id) \
                .order('created_at', desc=True) \
                .limit(limit) \
                .execute()

            return result.data or []

        except Exception as e:
            logger.error(f"[Execution] 履歴取得エラー: {e}")
            return []

    def get_active_execution(self, document_id: str) -> Optional[Dict[str, Any]]:
        """
        ドキュメントの active execution を取得

        Args:
            document_id: 対象ドキュメントID

        Returns:
            active execution（なければ None）
        """
        try:
            # documents から active_execution_id を取得
            doc_result = self.db.client.table('Rawdata_FILE_AND_MAIL') \
                .select('active_execution_id') \
                .eq('id', document_id) \
                .limit(1) \
                .execute()

            if not doc_result.data or not doc_result.data[0].get('active_execution_id'):
                return None

            active_id = doc_result.data[0]['active_execution_id']

            # execution を取得
            exec_result = self.db.client.table('document_executions') \
                .select('*') \
                .eq('id', active_id) \
                .limit(1) \
                .execute()

            if exec_result.data:
                return exec_result.data[0]
            return None

        except Exception as e:
            logger.error(f"[Execution] active 取得エラー: {e}")
            return None


# シングルトンインスタンス
_execution_manager_instance: Optional[ExecutionManager] = None


def get_execution_manager() -> ExecutionManager:
    """ExecutionManager のシングルトンインスタンスを取得"""
    global _execution_manager_instance
    if _execution_manager_instance is None:
        _execution_manager_instance = ExecutionManager()
    return _execution_manager_instance
