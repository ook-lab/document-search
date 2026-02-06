"""
Phase 5: Execution Versioning テスト

目的:
1. AI推論結果を上書きしない（再処理しても過去が残る）
2. documents と executions を分離し、active_execution_id で切り替え
3. 失敗してもデータが残る

実行方法:
  # ユニットテスト（Supabase不要）
  pytest tests/test_phase5_execution_versioning.py -v -m "not integration"

  # 統合テスト（Supabaseローカル起動）
  pytest tests/test_phase5_execution_versioning.py -v -m integration
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))


# =============================================================================
# ユニットテスト（Supabase 不要）
# =============================================================================

class TestInputHashComputation:
    """input_hash の計算テスト"""

    def test_same_input_same_hash(self):
        """同一入力は同一ハッシュを生成"""
        from shared.processing.execution_manager import ExecutionManager

        input_text = "これはテスト入力です"
        hash1 = ExecutionManager.compute_input_hash(input_text)
        hash2 = ExecutionManager.compute_input_hash(input_text)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    def test_different_input_different_hash(self):
        """異なる入力は異なるハッシュを生成"""
        from shared.processing.execution_manager import ExecutionManager

        hash1 = ExecutionManager.compute_input_hash("入力A")
        hash2 = ExecutionManager.compute_input_hash("入力B")

        assert hash1 != hash2

    def test_empty_input_valid_hash(self):
        """空入力でも有効なハッシュを生成"""
        from shared.processing.execution_manager import ExecutionManager

        hash1 = ExecutionManager.compute_input_hash("")
        hash2 = ExecutionManager.compute_input_hash(None)

        assert len(hash1) == 64
        assert hash1 == hash2  # 両方とも空文字列として扱われる

    def test_metadata_affects_hash(self):
        """メタデータがハッシュに影響する"""
        from shared.processing.execution_manager import ExecutionManager

        input_text = "テスト"
        hash_without_meta = ExecutionManager.compute_input_hash(input_text)
        hash_with_meta = ExecutionManager.compute_input_hash(
            input_text,
            metadata={'key': 'value'}
        )

        assert hash_without_meta != hash_with_meta


class TestNormalizedHashComputation:
    """normalized_hash の計算テスト"""

    def test_normalized_hash_computation(self):
        """正規化ハッシュが計算できる"""
        from shared.processing.execution_manager import ExecutionManager

        text = "正規化されたテキスト"
        hash1 = ExecutionManager.compute_normalized_hash(text)

        assert len(hash1) == 64
        assert hash1 == ExecutionManager.compute_normalized_hash(text)


class TestExecutionContextDataclass:
    """ExecutionContext データクラスのテスト"""

    def test_execution_context_creation(self):
        """ExecutionContext が正しく作成される"""
        from shared.processing.execution_manager import ExecutionContext

        ctx = ExecutionContext(
            execution_id="exec-123",
            document_id="doc-456",
            owner_id="owner-789",
            input_hash="hash-abc",
            model_version="gemini-2.5-flash"
        )

        assert ctx.execution_id == "exec-123"
        assert ctx.document_id == "doc-456"
        assert ctx.owner_id == "owner-789"
        assert ctx.input_hash == "hash-abc"
        assert ctx.model_version == "gemini-2.5-flash"


class TestExecutionLineage:
    """実行系譜（retry_of_execution_id）のテスト"""

    def test_lineage_preserved(self):
        """リトライ時に系譜が保持される"""
        # このテストはモックを使用
        from shared.processing.execution_manager import ExecutionManager

        # retry_of_execution_id が正しく渡されることを確認
        manager = ExecutionManager.__new__(ExecutionManager)
        manager.db = MagicMock()

        # create_execution に retry_of_execution_id を渡すシグネチャを確認
        import inspect
        sig = inspect.signature(manager.create_execution)
        params = list(sig.parameters.keys())

        assert 'retry_of_execution_id' in params


# =============================================================================
# 統合テスト（Supabase ローカル起動が必要）
# =============================================================================

@pytest.mark.integration
class TestExecutionVersioningIntegration:
    """
    execution versioning の統合テスト

    実行条件: Supabase ローカルが起動していること
    pytest tests/test_phase5_execution_versioning.py -m integration -v
    """

    @pytest.fixture
    def service_client(self):
        """service_role クライアント"""
        import os
        url = os.getenv('SUPABASE_URL', 'http://127.0.0.1:54321')
        key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

        if not key:
            pytest.skip("SUPABASE_SERVICE_ROLE_KEY が設定されていません")

        from supabase import create_client
        return create_client(url, key)

    @pytest.fixture
    def execution_manager(self, service_client):
        """ExecutionManager インスタンス"""
        from shared.processing.execution_manager import ExecutionManager
        from shared.common.database.client import DatabaseClient

        # DatabaseClient をモック的に作成
        class MockDBClient:
            def __init__(self, client):
                self.client = client

        db = MockDBClient(service_client)
        return ExecutionManager(db)

    @pytest.fixture
    def test_document(self, service_client):
        """テスト用ドキュメントを作成"""
        import uuid
        test_owner_id = str(uuid.uuid4())
        test_doc_id = None

        # テストドキュメントを作成
        doc_data = {
            'file_name': f'test_phase5_{uuid.uuid4().hex[:8]}.pdf',
            'source_id': f'test_{uuid.uuid4().hex}',
            'source_type': 'test',
            'workspace': 'test_workspace',
            'doc_type': 'test',
            'owner_id': test_owner_id,
            'processing_status': 'pending'
        }

        result = service_client.table('Rawdata_FILE_AND_MAIL').insert(doc_data).execute()
        if result.data:
            test_doc_id = result.data[0]['id']

        yield {
            'document_id': test_doc_id,
            'owner_id': test_owner_id
        }

        # クリーンアップ
        if test_doc_id:
            try:
                # 関連する executions を削除
                service_client.table('document_executions') \
                    .delete() \
                    .eq('document_id', test_doc_id) \
                    .execute()
                # ドキュメントを削除
                service_client.table('Rawdata_FILE_AND_MAIL') \
                    .delete() \
                    .eq('id', test_doc_id) \
                    .execute()
            except Exception:
                pass

    def test_initial_processing(self, execution_manager, test_document, service_client):
        """初回処理: execution 作成 → succeeded → active 設定"""
        doc_id = test_document['document_id']
        owner_id = test_document['owner_id']

        if not doc_id:
            pytest.skip("テストドキュメント作成失敗")

        # 1. execution 作成
        ctx = execution_manager.create_execution(
            document_id=doc_id,
            owner_id=owner_id,
            input_text="テスト入力テキスト",
            model_version="test-model-1.0"
        )

        assert ctx.execution_id is not None
        assert ctx.document_id == doc_id

        # 2. succeeded にマーク
        result_data = {
            'summary': 'テスト要約',
            'tags': ['test', 'phase5']
        }
        success = execution_manager.mark_succeeded(
            execution_id=ctx.execution_id,
            result_data=result_data,
            processing_duration_ms=1000
        )

        assert success is True

        # 3. active_execution_id が設定されている
        doc = service_client.table('Rawdata_FILE_AND_MAIL') \
            .select('active_execution_id') \
            .eq('id', doc_id) \
            .execute()

        assert doc.data[0]['active_execution_id'] == ctx.execution_id

    def test_reprocessing_success(self, execution_manager, test_document, service_client):
        """再処理（成功）: 新しい execution が作成され、active が切り替わる"""
        doc_id = test_document['document_id']
        owner_id = test_document['owner_id']

        if not doc_id:
            pytest.skip("テストドキュメント作成失敗")

        # 初回処理
        ctx1 = execution_manager.create_execution(
            document_id=doc_id,
            owner_id=owner_id,
            input_text="初回入力",
            model_version="v1"
        )
        execution_manager.mark_succeeded(
            execution_id=ctx1.execution_id,
            result_data={'version': 1},
            processing_duration_ms=100
        )

        # 再処理
        ctx2 = execution_manager.create_execution(
            document_id=doc_id,
            owner_id=owner_id,
            input_text="再処理入力",
            model_version="v2"
        )
        execution_manager.mark_succeeded(
            execution_id=ctx2.execution_id,
            result_data={'version': 2},
            processing_duration_ms=200
        )

        # 検証: 古い execution が残っている
        history = execution_manager.get_execution_history(doc_id)
        assert len(history) >= 2

        # 検証: active が新しい execution を指している
        doc = service_client.table('Rawdata_FILE_AND_MAIL') \
            .select('active_execution_id') \
            .eq('id', doc_id) \
            .execute()

        assert doc.data[0]['active_execution_id'] == ctx2.execution_id

    def test_reprocessing_failure(self, execution_manager, test_document, service_client):
        """再処理（失敗）: active は前の成功結果のまま"""
        doc_id = test_document['document_id']
        owner_id = test_document['owner_id']

        if not doc_id:
            pytest.skip("テストドキュメント作成失敗")

        # 初回処理（成功）
        ctx1 = execution_manager.create_execution(
            document_id=doc_id,
            owner_id=owner_id,
            input_text="成功入力",
            model_version="v1"
        )
        execution_manager.mark_succeeded(
            execution_id=ctx1.execution_id,
            result_data={'status': 'success'},
            processing_duration_ms=100
        )

        # 再処理（失敗）
        ctx2 = execution_manager.create_execution(
            document_id=doc_id,
            owner_id=owner_id,
            input_text="失敗入力",
            model_version="v2"
        )
        execution_manager.mark_failed(
            execution_id=ctx2.execution_id,
            error_code='TEST_ERROR',
            error_message='テストエラー',
            processing_duration_ms=50
        )

        # 検証: failed execution が残っている
        history = execution_manager.get_execution_history(doc_id)
        failed_execs = [e for e in history if e['status'] == 'failed']
        assert len(failed_execs) >= 1

        # 検証: active は前の成功結果のまま
        doc = service_client.table('Rawdata_FILE_AND_MAIL') \
            .select('active_execution_id') \
            .eq('id', doc_id) \
            .execute()

        assert doc.data[0]['active_execution_id'] == ctx1.execution_id

    def test_owner_consistency(self, execution_manager, test_document, service_client):
        """owner 整合: execution.owner_id と document.owner_id が一致"""
        doc_id = test_document['document_id']
        owner_id = test_document['owner_id']

        if not doc_id:
            pytest.skip("テストドキュメント作成失敗")

        # execution 作成
        ctx = execution_manager.create_execution(
            document_id=doc_id,
            owner_id=owner_id,
            input_text="テスト",
            model_version="v1"
        )

        # 検証: owner_id が一致
        exec_result = service_client.table('document_executions') \
            .select('owner_id') \
            .eq('id', ctx.execution_id) \
            .execute()

        doc_result = service_client.table('Rawdata_FILE_AND_MAIL') \
            .select('owner_id') \
            .eq('id', doc_id) \
            .execute()

        assert exec_result.data[0]['owner_id'] == doc_result.data[0]['owner_id']


@pytest.mark.integration
class TestIdempotency:
    """冪等性のテスト"""

    @pytest.fixture
    def service_client(self):
        """service_role クライアント"""
        import os
        url = os.getenv('SUPABASE_URL', 'http://127.0.0.1:54321')
        key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

        if not key:
            pytest.skip("SUPABASE_SERVICE_ROLE_KEY が設定されていません")

        from supabase import create_client
        return create_client(url, key)

    @pytest.fixture
    def execution_manager(self, service_client):
        """ExecutionManager インスタンス"""
        from shared.processing.execution_manager import ExecutionManager

        class MockDBClient:
            def __init__(self, client):
                self.client = client

        db = MockDBClient(service_client)
        return ExecutionManager(db)

    def test_find_existing_execution(self, execution_manager, service_client):
        """同一入力の既存 execution を検索できる"""
        import uuid

        # テストドキュメント作成
        test_owner_id = str(uuid.uuid4())
        doc_data = {
            'file_name': f'test_idempotency_{uuid.uuid4().hex[:8]}.pdf',
            'source_id': f'test_{uuid.uuid4().hex}',
            'source_type': 'test',
            'workspace': 'test',
            'doc_type': 'test',
            'owner_id': test_owner_id,
            'processing_status': 'pending'
        }

        result = service_client.table('Rawdata_FILE_AND_MAIL').insert(doc_data).execute()
        doc_id = result.data[0]['id']

        try:
            input_text = "冪等性テスト入力"

            # 初回処理
            ctx1 = execution_manager.create_execution(
                document_id=doc_id,
                owner_id=test_owner_id,
                input_text=input_text,
                model_version="v1"
            )
            execution_manager.mark_succeeded(
                execution_id=ctx1.execution_id,
                result_data={'test': True},
                processing_duration_ms=100
            )

            # 同一入力の既存 execution を検索
            input_hash = execution_manager.compute_input_hash(input_text)
            existing = execution_manager.find_existing_execution(doc_id, input_hash)

            assert existing is not None
            assert existing['id'] == ctx1.execution_id

        finally:
            # クリーンアップ
            service_client.table('document_executions') \
                .delete() \
                .eq('document_id', doc_id) \
                .execute()
            service_client.table('Rawdata_FILE_AND_MAIL') \
                .delete() \
                .eq('id', doc_id) \
                .execute()


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'not integration'])
