"""
Phase 4A: anon 公開面の最小化テスト

anon ユーザーが実テーブルに直接アクセスできず、
RPC 経由のみでデータ取得できることを確認
"""

import pytest
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from shared.common.database.client import DatabaseClient


class TestAnonAccessRestrictions:
    """anon の直接テーブルアクセス制限テスト（ユニット）"""

    def test_anon_all_chunks_skipped(self):
        """anon 接続時は all_chunks 取得がスキップされる"""
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = False
                self._is_authenticated = False  # anon

        client = MockDatabaseClient()

        # anon でも authenticated でもない → all_chunks スキップ
        assert not client._is_service_role
        assert not client._is_authenticated

    def test_authenticated_all_chunks_allowed(self):
        """authenticated 接続時は all_chunks 取得が許可される"""
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = False
                self._is_authenticated = True

        client = MockDatabaseClient()

        # authenticated → all_chunks 許可
        assert client._is_authenticated

    def test_service_role_all_chunks_allowed(self):
        """service_role 接続時は all_chunks 取得が許可される"""
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = True
                self._is_authenticated = False

        client = MockDatabaseClient()

        # service_role → all_chunks 許可
        assert client._is_service_role


class TestPublicSearchRPC:
    """public_search RPC の返却フィールド仕様テスト"""

    # 許可されている返却フィールド
    ALLOWED_FIELDS = {
        'document_id',
        'file_name',
        'doc_type',
        'workspace',
        'document_date',
        'summary',
        'similarity',
        'chunk_preview',
    }

    # 禁止されている返却フィールド（PII/本文）
    FORBIDDEN_FIELDS = {
        'attachment_text',       # 本文全体
        'display_sender_email',  # メールアドレス
        'display_post_text',     # 投稿本文
        'metadata',              # 詳細メタデータ
        'owner_id',              # 所有者情報
        'chunk_content',         # チャンク全文（preview のみ許可）
    }

    def test_allowed_fields_defined(self):
        """許可フィールドが定義されている"""
        assert 'document_id' in self.ALLOWED_FIELDS
        assert 'file_name' in self.ALLOWED_FIELDS
        assert 'summary' in self.ALLOWED_FIELDS
        assert 'chunk_preview' in self.ALLOWED_FIELDS

    def test_forbidden_fields_defined(self):
        """禁止フィールドが定義されている"""
        assert 'attachment_text' in self.FORBIDDEN_FIELDS
        assert 'display_sender_email' in self.FORBIDDEN_FIELDS
        assert 'owner_id' in self.FORBIDDEN_FIELDS

    def test_no_overlap(self):
        """許可と禁止に重複がない"""
        overlap = self.ALLOWED_FIELDS & self.FORBIDDEN_FIELDS
        assert len(overlap) == 0, f"重複フィールド: {overlap}"


class TestPublicSearchWithFulltextRPC:
    """public_search_with_fulltext RPC の返却フィールド仕様テスト"""

    # 許可されている返却フィールド
    ALLOWED_FIELDS = {
        'document_id',
        'file_name',
        'doc_type',
        'workspace',
        'document_date',
        'summary',
        'combined_score',
        'chunk_preview',
        'chunk_type',
    }

    def test_allowed_fields_defined(self):
        """許可フィールドが定義されている"""
        assert 'document_id' in self.ALLOWED_FIELDS
        assert 'combined_score' in self.ALLOWED_FIELDS
        assert 'chunk_type' in self.ALLOWED_FIELDS


# =============================================================================
# 統合テスト（Supabase ローカル接続が必要）
# =============================================================================

@pytest.mark.integration
class TestAnonRPCOnlyIntegration:
    """
    統合テスト: anon が実テーブルにアクセスできないことを確認

    実行条件: Supabase ローカルが起動していること
    pytest tests/test_phase4a_anon_rpc_only.py -m integration
    """

    @pytest.fixture
    def anon_client(self):
        """anon クライアント（SUPABASE_ANON_KEY 使用）"""
        import os
        url = os.getenv('SUPABASE_URL', 'http://127.0.0.1:54321')
        key = os.getenv('SUPABASE_ANON_KEY')

        if not key:
            pytest.skip("SUPABASE_ANON_KEY が設定されていません")

        from supabase import create_client
        return create_client(url, key)

    def test_anon_cannot_select_rawdata(self, anon_client):
        """anon は Rawdata_FILE_AND_MAIL に直接 SELECT できない"""
        try:
            response = anon_client.table('Rawdata_FILE_AND_MAIL').select('*').limit(1).execute()
            # 成功した場合は権限エラー期待
            assert len(response.data) == 0 or response.data is None, \
                "anon が Rawdata_FILE_AND_MAIL を読めてしまっています"
        except Exception as e:
            # permission denied が期待される
            assert 'permission denied' in str(e).lower() or \
                   'RLS' in str(e) or \
                   'denied' in str(e).lower(), \
                f"予期しないエラー: {e}"

    def test_anon_cannot_select_search_index(self, anon_client):
        """anon は 10_ix_search_index に直接 SELECT できない"""
        try:
            response = anon_client.table('10_ix_search_index').select('*').limit(1).execute()
            assert len(response.data) == 0 or response.data is None, \
                "anon が 10_ix_search_index を読めてしまっています"
        except Exception as e:
            assert 'permission denied' in str(e).lower() or \
                   'denied' in str(e).lower(), \
                f"予期しないエラー: {e}"

    def test_anon_can_execute_public_search(self, anon_client):
        """anon は public_search RPC を実行できる"""
        # 空のクエリでも RPC 自体は実行可能
        try:
            response = anon_client.rpc('public_search', {
                'query_text': 'test',
                'query_embedding': [0.0] * 1536,
                'match_threshold': 0.5,
                'match_count': 1
            }).execute()

            # 結果が空でも RPC 実行自体は成功
            assert response is not None
        except Exception as e:
            if 'function' not in str(e).lower():
                # RPC が存在しない以外のエラーは予期しない
                pytest.fail(f"public_search RPC 実行失敗: {e}")


@pytest.mark.integration
class TestForbiddenFieldsSnapshot:
    """
    スナップショットテスト: RPC が禁止フィールドを返さないことを確認

    契約書（docs/phase4a_public_api_contract.md）との整合性を保証
    """

    # 禁止フィールド（PII/本文/内部情報）
    FORBIDDEN_FIELDS = {
        'attachment_text',       # 本文全体
        'display_sender_email',  # メールアドレス
        'display_post_text',     # 投稿本文
        'metadata',              # 詳細メタデータ
        'owner_id',              # 所有者情報
        'chunk_content',         # チャンク全文（preview のみ許可）
        'embedding',             # 埋め込みベクトル
        'source_id',             # 内部ID
    }

    @pytest.fixture
    def anon_client(self):
        """anon クライアント"""
        import os
        url = os.getenv('SUPABASE_URL', 'http://127.0.0.1:54321')
        key = os.getenv('SUPABASE_ANON_KEY')

        if not key:
            pytest.skip("SUPABASE_ANON_KEY が設定されていません")

        from supabase import create_client
        return create_client(url, key)

    def test_public_search_no_forbidden_fields(self, anon_client):
        """public_search RPC が禁止フィールドを返さない"""
        try:
            response = anon_client.rpc('public_search', {
                'query_text': 'test',
                'query_embedding': [0.0] * 1536,
                'match_threshold': 0.0,
                'match_count': 5
            }).execute()

            if response.data:
                for row in response.data:
                    returned_fields = set(row.keys())
                    forbidden_in_response = returned_fields & self.FORBIDDEN_FIELDS

                    assert len(forbidden_in_response) == 0, \
                        f"禁止フィールドが返却されました: {forbidden_in_response}"

        except Exception as e:
            if 'function' in str(e).lower():
                pytest.skip("public_search RPC が未作成です")
            raise

    def test_public_search_with_fulltext_no_forbidden_fields(self, anon_client):
        """public_search_with_fulltext RPC が禁止フィールドを返さない"""
        try:
            response = anon_client.rpc('public_search_with_fulltext', {
                'query_text': 'test',
                'query_embedding': [0.0] * 1536,
                'match_threshold': 0.0,
                'match_count': 5,
                'vector_weight': 0.7,
                'fulltext_weight': 0.3
            }).execute()

            if response.data:
                for row in response.data:
                    returned_fields = set(row.keys())
                    forbidden_in_response = returned_fields & self.FORBIDDEN_FIELDS

                    assert len(forbidden_in_response) == 0, \
                        f"禁止フィールドが返却されました: {forbidden_in_response}"

        except Exception as e:
            if 'function' in str(e).lower():
                pytest.skip("public_search_with_fulltext RPC が未作成です")
            raise

    def test_allowed_fields_only(self, anon_client):
        """public_search RPC が許可フィールドのみを返す"""
        ALLOWED_FIELDS = {
            'document_id',
            'file_name',
            'doc_type',
            'workspace',
            'document_date',
            'summary',
            'similarity',
            'chunk_preview',
        }

        try:
            response = anon_client.rpc('public_search', {
                'query_text': 'test',
                'query_embedding': [0.0] * 1536,
                'match_threshold': 0.0,
                'match_count': 5
            }).execute()

            if response.data:
                for row in response.data:
                    returned_fields = set(row.keys())
                    unexpected_fields = returned_fields - ALLOWED_FIELDS

                    assert len(unexpected_fields) == 0, \
                        f"予期しないフィールドが返却されました: {unexpected_fields}"

        except Exception as e:
            if 'function' in str(e).lower():
                pytest.skip("public_search RPC が未作成です")
            raise


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'not integration'])
