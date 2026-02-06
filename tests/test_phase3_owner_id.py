"""
Phase 3: owner_id 必須化テスト

DatabaseClient の owner_id バリデーション（第三防衛線）をテスト
"""

import pytest
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from shared.common.database.client import (
    DatabaseClient,
    OwnerIdRequiredError,
    OWNER_ID_REQUIRED_TABLES
)


class TestOwnerIdValidation:
    """owner_id バリデーションのユニットテスト"""

    def test_owner_id_required_tables_defined(self):
        """必須テーブルが正しく定義されている"""
        assert 'Rawdata_FILE_AND_MAIL' in OWNER_ID_REQUIRED_TABLES
        assert OWNER_ID_REQUIRED_TABLES['Rawdata_FILE_AND_MAIL'] == 'owner_id'

        assert '10_ix_search_index' in OWNER_ID_REQUIRED_TABLES
        assert OWNER_ID_REQUIRED_TABLES['10_ix_search_index'] == 'owner_id'

        assert 'Rawdata_RECEIPT_shops' in OWNER_ID_REQUIRED_TABLES
        assert OWNER_ID_REQUIRED_TABLES['Rawdata_RECEIPT_shops'] == 'owner_id'

        assert 'MASTER_Rules_transaction_dict' in OWNER_ID_REQUIRED_TABLES
        assert OWNER_ID_REQUIRED_TABLES['MASTER_Rules_transaction_dict'] == 'created_by'

        assert '99_lg_correction_history' in OWNER_ID_REQUIRED_TABLES
        assert OWNER_ID_REQUIRED_TABLES['99_lg_correction_history'] == 'corrector_id'

    def test_validate_owner_id_service_role_missing(self):
        """service_role で owner_id 欠落時にエラー"""
        # モックの DatabaseClient を作成（実際のDB接続なし）
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = True
                self._is_authenticated = False

        client = MockDatabaseClient()

        # owner_id 欠落 → OwnerIdRequiredError
        with pytest.raises(OwnerIdRequiredError) as exc_info:
            client._validate_owner_id('Rawdata_FILE_AND_MAIL', {
                'file_name': 'test.pdf',
                'source_id': '12345'
                # owner_id 欠落
            })
        assert 'owner_id' in str(exc_info.value)

    def test_validate_owner_id_service_role_null(self):
        """service_role で owner_id=None 時にエラー"""
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = True
                self._is_authenticated = False

        client = MockDatabaseClient()

        # owner_id=None → OwnerIdRequiredError
        with pytest.raises(OwnerIdRequiredError):
            client._validate_owner_id('Rawdata_FILE_AND_MAIL', {
                'file_name': 'test.pdf',
                'owner_id': None  # 明示的に None
            })

    def test_validate_owner_id_service_role_valid(self):
        """service_role で owner_id あり → 成功"""
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = True
                self._is_authenticated = False

        client = MockDatabaseClient()

        # owner_id あり → 例外なし
        client._validate_owner_id('Rawdata_FILE_AND_MAIL', {
            'file_name': 'test.pdf',
            'owner_id': '11111111-1111-1111-1111-111111111111'
        })
        # ここに到達すれば成功

    def test_validate_owner_id_authenticated_skip(self):
        """authenticated 接続では検証をスキップ（RLS が保護）"""
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = False
                self._is_authenticated = True

        client = MockDatabaseClient()

        # authenticated では owner_id 欠落でも検証スキップ
        # （RLS の WITH CHECK で auth.uid() が強制されるため）
        client._validate_owner_id('Rawdata_FILE_AND_MAIL', {
            'file_name': 'test.pdf'
            # owner_id 欠落でも OK（RLS が保護）
        })
        # ここに到達すれば成功

    def test_validate_owner_id_anon_skip(self):
        """anon 接続では検証をスキップ"""
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = False
                self._is_authenticated = False

        client = MockDatabaseClient()

        # anon では検証スキップ（書き込み権限自体がない）
        client._validate_owner_id('Rawdata_FILE_AND_MAIL', {
            'file_name': 'test.pdf'
        })
        # ここに到達すれば成功

    def test_validate_owner_id_non_required_table(self):
        """必須テーブル以外では検証をスキップ"""
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = True
                self._is_authenticated = False

        client = MockDatabaseClient()

        # 必須テーブル以外 → 検証スキップ
        client._validate_owner_id('some_other_table', {
            'some_field': 'value'
            # owner_id 不要
        })
        # ここに到達すれば成功

    def test_validate_all_required_tables(self):
        """すべての必須テーブルで検証が機能する"""
        class MockDatabaseClient(DatabaseClient):
            def __init__(self):
                self._is_service_role = True
                self._is_authenticated = False

        client = MockDatabaseClient()

        for table, column in OWNER_ID_REQUIRED_TABLES.items():
            # 欠落時 → エラー
            with pytest.raises(OwnerIdRequiredError) as exc_info:
                client._validate_owner_id(table, {'some_field': 'value'})
            assert column in str(exc_info.value)

            # 値あり → 成功
            client._validate_owner_id(table, {
                'some_field': 'value',
                column: '11111111-1111-1111-1111-111111111111'
            })


class TestOwnerIdRequiredError:
    """OwnerIdRequiredError クラスのテスト"""

    def test_error_message(self):
        """エラーメッセージが適切"""
        error = OwnerIdRequiredError("Test error message")
        assert str(error) == "Test error message"

    def test_is_exception(self):
        """Exception を継承している"""
        error = OwnerIdRequiredError("test")
        assert isinstance(error, Exception)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
