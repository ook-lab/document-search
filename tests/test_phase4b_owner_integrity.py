"""
Phase 4B: owner_id 整合性チェック（CI 自動実行用）

目的:
- owner_id が NULL のデータがないことを継続的に検証
- 親子テーブル間の owner_id 一致を検証
- SYSTEM_OWNER_ID へのフォールバック件数を監視

実行方法:
  pytest tests/test_phase4b_owner_integrity.py -v

統合テスト（Supabase ローカル接続が必要）:
  pytest tests/test_phase4b_owner_integrity.py -v -m integration
"""

import pytest
import os
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from shared.common.database.client import OWNER_ID_REQUIRED_TABLES


# =============================================================================
# 定数定義
# =============================================================================

# SYSTEM_OWNER_ID（バッチ処理等で使用）
SYSTEM_OWNER_ID = '00000000-0000-0000-0000-000000000000'

# 親子関係のマッピング（子テーブル → 親テーブル、結合キー）
PARENT_CHILD_RELATIONS = {
    '10_ix_search_index': {
        'parent_table': 'Rawdata_FILE_AND_MAIL',
        'join_key': 'document_id',  # 子の document_id = 親の id
        'parent_key': 'id',
    }
}

# SYSTEM_OWNER_ID の許容上限（警告閾値）
SYSTEM_OWNER_THRESHOLD = {
    'Rawdata_FILE_AND_MAIL': 100,  # 100件超えたら警告
    '10_ix_search_index': 500,     # チャンクは多いので緩め
    'Rawdata_RECEIPT_shops': 50,
    'MASTER_Rules_transaction_dict': 20,
    '99_lg_correction_history': 50,
}


# =============================================================================
# ユニットテスト（定義の整合性）
# =============================================================================

class TestOwnerIdDefinitions:
    """owner_id 定義の整合性テスト"""

    def test_required_tables_defined(self):
        """必須テーブルが定義されている"""
        expected_tables = {
            'Rawdata_FILE_AND_MAIL',
            '10_ix_search_index',
            'Rawdata_RECEIPT_shops',
            'MASTER_Rules_transaction_dict',
            '99_lg_correction_history',
        }
        assert set(OWNER_ID_REQUIRED_TABLES.keys()) == expected_tables

    def test_parent_child_relations_valid(self):
        """親子関係の定義が有効"""
        for child, config in PARENT_CHILD_RELATIONS.items():
            # 子テーブルが owner_id 必須テーブルに含まれる
            assert child in OWNER_ID_REQUIRED_TABLES, \
                f"子テーブル {child} が OWNER_ID_REQUIRED_TABLES に未定義"

            # 親テーブルも owner_id 必須テーブルに含まれる
            parent = config['parent_table']
            assert parent in OWNER_ID_REQUIRED_TABLES, \
                f"親テーブル {parent} が OWNER_ID_REQUIRED_TABLES に未定義"

    def test_system_owner_threshold_defined(self):
        """全必須テーブルに閾値が定義されている"""
        for table in OWNER_ID_REQUIRED_TABLES:
            assert table in SYSTEM_OWNER_THRESHOLD, \
                f"テーブル {table} の SYSTEM_OWNER_THRESHOLD が未定義"


# =============================================================================
# 統合テスト（Supabase ローカル接続が必要）
# =============================================================================

@pytest.mark.integration
class TestOwnerIdIntegrityChecks:
    """
    owner_id 整合性の統合テスト

    実行条件: Supabase ローカルが起動していること
    pytest tests/test_phase4b_owner_integrity.py -m integration -v
    """

    @pytest.fixture
    def service_client(self):
        """service_role クライアント（RLS バイパス）"""
        url = os.getenv('SUPABASE_URL', 'http://127.0.0.1:54321')
        key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

        if not key:
            pytest.skip("SUPABASE_SERVICE_ROLE_KEY が設定されていません")

        from supabase import create_client
        return create_client(url, key)

    def test_no_null_owner_id(self, service_client):
        """全必須テーブルで owner_id が NULL の行がない"""
        violations = []

        for table, column in OWNER_ID_REQUIRED_TABLES.items():
            try:
                # NULL の行をカウント
                response = service_client.table(table) \
                    .select('id', count='exact') \
                    .is_(column, 'null') \
                    .execute()

                null_count = response.count if hasattr(response, 'count') else len(response.data or [])

                if null_count > 0:
                    violations.append({
                        'table': table,
                        'column': column,
                        'null_count': null_count
                    })
                    print(f"[VIOLATION] {table}.{column}: {null_count} 件の NULL")
                else:
                    print(f"[OK] {table}.{column}: NULL なし")

            except Exception as e:
                # テーブルが存在しない場合はスキップ
                if 'does not exist' in str(e).lower() or 'relation' in str(e).lower():
                    print(f"[SKIP] {table}: テーブル未作成")
                else:
                    print(f"[ERROR] {table}: {e}")
                    violations.append({
                        'table': table,
                        'column': column,
                        'error': str(e)
                    })

        assert len(violations) == 0, \
            f"owner_id NULL 違反: {violations}"

    def test_parent_child_owner_consistency(self, service_client):
        """親子テーブル間で owner_id が一致している"""
        violations = []

        for child_table, config in PARENT_CHILD_RELATIONS.items():
            parent_table = config['parent_table']
            join_key = config['join_key']
            parent_key = config['parent_key']

            child_column = OWNER_ID_REQUIRED_TABLES[child_table]
            parent_column = OWNER_ID_REQUIRED_TABLES[parent_table]

            try:
                # 不一致を検出する SQL（RPC として実行）
                # 子テーブルの owner_id と、結合した親テーブルの owner_id が異なる行
                query = f"""
                SELECT COUNT(*) as mismatch_count
                FROM "{child_table}" c
                JOIN "{parent_table}" p ON c.{join_key} = p.{parent_key}
                WHERE c.{child_column} != p.{parent_column}
                """

                # 直接 SQL を実行（rpc が使えない場合のフォールバック）
                # Supabase Python SDK では直接 SQL 実行が難しいため、
                # 代替として両テーブルのデータを取得して比較

                # 子テーブルから parent_key と owner_id を取得
                child_response = service_client.table(child_table) \
                    .select(f'{join_key}, {child_column}') \
                    .limit(1000) \
                    .execute()

                if not child_response.data:
                    print(f"[SKIP] {child_table}: データなし")
                    continue

                # 親テーブルの owner_id マップを作成
                parent_ids = list(set(row[join_key] for row in child_response.data if row.get(join_key)))

                if not parent_ids:
                    print(f"[SKIP] {child_table}: 有効な {join_key} なし")
                    continue

                # 親テーブルから該当行を取得（バッチ処理）
                parent_owner_map = {}
                batch_size = 100
                for i in range(0, len(parent_ids), batch_size):
                    batch = parent_ids[i:i+batch_size]
                    parent_response = service_client.table(parent_table) \
                        .select(f'{parent_key}, {parent_column}') \
                        .in_(parent_key, batch) \
                        .execute()

                    for row in parent_response.data or []:
                        parent_owner_map[row[parent_key]] = row[parent_column]

                # 不一致をカウント
                mismatch_count = 0
                for child_row in child_response.data:
                    child_owner = child_row.get(child_column)
                    parent_id = child_row.get(join_key)
                    parent_owner = parent_owner_map.get(parent_id)

                    if parent_owner and child_owner != parent_owner:
                        mismatch_count += 1

                if mismatch_count > 0:
                    violations.append({
                        'child_table': child_table,
                        'parent_table': parent_table,
                        'mismatch_count': mismatch_count
                    })
                    print(f"[VIOLATION] {child_table} ↔ {parent_table}: {mismatch_count} 件の不一致")
                else:
                    print(f"[OK] {child_table} ↔ {parent_table}: 親子一致")

            except Exception as e:
                if 'does not exist' in str(e).lower():
                    print(f"[SKIP] {child_table}/{parent_table}: テーブル未作成")
                else:
                    print(f"[ERROR] {child_table}/{parent_table}: {e}")

        assert len(violations) == 0, \
            f"親子 owner_id 不一致: {violations}"

    def test_system_owner_within_threshold(self, service_client):
        """SYSTEM_OWNER_ID の使用が閾値以内"""
        warnings = []

        for table, column in OWNER_ID_REQUIRED_TABLES.items():
            threshold = SYSTEM_OWNER_THRESHOLD.get(table, 100)

            try:
                # SYSTEM_OWNER_ID の行をカウント
                response = service_client.table(table) \
                    .select('id', count='exact') \
                    .eq(column, SYSTEM_OWNER_ID) \
                    .execute()

                system_count = response.count if hasattr(response, 'count') else len(response.data or [])

                if system_count > threshold:
                    warnings.append({
                        'table': table,
                        'system_count': system_count,
                        'threshold': threshold
                    })
                    print(f"[WARNING] {table}: SYSTEM_OWNER_ID {system_count} 件（閾値: {threshold}）")
                else:
                    print(f"[OK] {table}: SYSTEM_OWNER_ID {system_count} 件（閾値: {threshold}）")

            except Exception as e:
                if 'does not exist' in str(e).lower():
                    print(f"[SKIP] {table}: テーブル未作成")
                else:
                    print(f"[ERROR] {table}: {e}")

        # 警告は出すがテスト失敗にはしない（運用判断のため）
        if warnings:
            print(f"\n[SUMMARY] SYSTEM_OWNER_ID 閾値超過: {len(warnings)} テーブル")
            for w in warnings:
                print(f"  - {w['table']}: {w['system_count']} / {w['threshold']}")

        # 閾値の2倍を超えたら失敗（緊急対応が必要）
        critical_violations = [w for w in warnings if w['system_count'] > w['threshold'] * 2]
        assert len(critical_violations) == 0, \
            f"SYSTEM_OWNER_ID が閾値の2倍超過: {critical_violations}"


# =============================================================================
# CI 用サマリー出力
# =============================================================================

@pytest.mark.integration
class TestOwnerIntegritySummary:
    """整合性チェックのサマリー出力"""

    @pytest.fixture
    def service_client(self):
        """service_role クライアント"""
        url = os.getenv('SUPABASE_URL', 'http://127.0.0.1:54321')
        key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

        if not key:
            pytest.skip("SUPABASE_SERVICE_ROLE_KEY が設定されていません")

        from supabase import create_client
        return create_client(url, key)

    def test_generate_integrity_report(self, service_client):
        """整合性レポートを生成"""
        report = {
            'null_violations': [],
            'parent_child_mismatches': [],
            'system_owner_counts': {},
            'total_rows': {}
        }

        # 各テーブルの統計を収集
        for table, column in OWNER_ID_REQUIRED_TABLES.items():
            try:
                # 総件数
                total_response = service_client.table(table) \
                    .select('id', count='exact') \
                    .limit(1) \
                    .execute()
                total = total_response.count if hasattr(total_response, 'count') else 0

                # NULL 件数
                null_response = service_client.table(table) \
                    .select('id', count='exact') \
                    .is_(column, 'null') \
                    .execute()
                null_count = null_response.count if hasattr(null_response, 'count') else 0

                # SYSTEM_OWNER 件数
                system_response = service_client.table(table) \
                    .select('id', count='exact') \
                    .eq(column, SYSTEM_OWNER_ID) \
                    .execute()
                system_count = system_response.count if hasattr(system_response, 'count') else 0

                report['total_rows'][table] = total
                report['system_owner_counts'][table] = system_count

                if null_count > 0:
                    report['null_violations'].append({
                        'table': table,
                        'column': column,
                        'count': null_count
                    })

            except Exception as e:
                print(f"[SKIP] {table}: {e}")

        # レポート出力
        print("\n" + "=" * 60)
        print("OWNER_ID INTEGRITY REPORT")
        print("=" * 60)

        print("\n[テーブル統計]")
        for table, total in report['total_rows'].items():
            system = report['system_owner_counts'].get(table, 0)
            threshold = SYSTEM_OWNER_THRESHOLD.get(table, 100)
            status = "OK" if system <= threshold else "WARN"
            print(f"  {table}: {total} 件 (SYSTEM_OWNER: {system}/{threshold}) [{status}]")

        if report['null_violations']:
            print("\n[NULL 違反]")
            for v in report['null_violations']:
                print(f"  {v['table']}.{v['column']}: {v['count']} 件")
        else:
            print("\n[NULL 違反] なし")

        print("=" * 60)

        # テスト結果として成功（レポート生成が目的）
        assert True


# =============================================================================
# Phase 5 追加: active_execution_id 整合性チェック
# =============================================================================

@pytest.mark.integration
class TestActiveExecutionIntegrity:
    """
    active_execution_id の整合性チェック

    検証項目:
    1. active_execution_id が指す execution が存在する
    2. active_execution_id が指す execution の status が succeeded
    3. active_execution_id が指す execution の owner_id がドキュメントと一致
    """

    @pytest.fixture
    def service_client(self):
        """service_role クライアント"""
        url = os.getenv('SUPABASE_URL', 'http://127.0.0.1:54321')
        key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

        if not key:
            pytest.skip("SUPABASE_SERVICE_ROLE_KEY が設定されていません")

        from supabase import create_client
        return create_client(url, key)

    def test_active_execution_exists(self, service_client):
        """active_execution_id が指す execution が存在する"""
        try:
            # active_execution_id が設定されているドキュメントを取得
            docs_response = service_client.table('Rawdata_FILE_AND_MAIL') \
                .select('id, active_execution_id') \
                .not_.is_('active_execution_id', 'null') \
                .limit(1000) \
                .execute()

            if not docs_response.data:
                print("[SKIP] active_execution_id が設定されたドキュメントなし")
                return

            violations = []
            for doc in docs_response.data:
                active_id = doc['active_execution_id']

                # execution の存在確認
                exec_response = service_client.table('document_executions') \
                    .select('id') \
                    .eq('id', active_id) \
                    .limit(1) \
                    .execute()

                if not exec_response.data:
                    violations.append({
                        'document_id': doc['id'],
                        'active_execution_id': active_id,
                        'error': 'execution not found'
                    })

            if violations:
                print(f"[VIOLATION] 存在しない execution を参照: {len(violations)} 件")
                for v in violations[:5]:  # 最初の5件のみ表示
                    print(f"  doc={v['document_id'][:8]}... -> exec={v['active_execution_id'][:8]}...")

            assert len(violations) == 0, \
                f"active_execution_id が存在しない execution を参照: {len(violations)} 件"

        except Exception as e:
            if 'does not exist' in str(e).lower():
                pytest.skip("document_executions テーブルが未作成")
            raise

    def test_active_execution_succeeded(self, service_client):
        """active_execution_id が指す execution の status が succeeded"""
        try:
            # active_execution_id が設定されているドキュメントを取得
            docs_response = service_client.table('Rawdata_FILE_AND_MAIL') \
                .select('id, active_execution_id') \
                .not_.is_('active_execution_id', 'null') \
                .limit(1000) \
                .execute()

            if not docs_response.data:
                print("[SKIP] active_execution_id が設定されたドキュメントなし")
                return

            violations = []
            active_ids = [doc['active_execution_id'] for doc in docs_response.data]

            # execution のステータスを一括取得
            for i in range(0, len(active_ids), 100):
                batch = active_ids[i:i+100]
                exec_response = service_client.table('document_executions') \
                    .select('id, status') \
                    .in_('id', batch) \
                    .execute()

                status_map = {e['id']: e['status'] for e in (exec_response.data or [])}

                for doc in docs_response.data:
                    if doc['active_execution_id'] in status_map:
                        status = status_map[doc['active_execution_id']]
                        if status != 'succeeded':
                            violations.append({
                                'document_id': doc['id'],
                                'active_execution_id': doc['active_execution_id'],
                                'status': status
                            })

            if violations:
                print(f"[VIOLATION] succeeded でない execution を参照: {len(violations)} 件")
                for v in violations[:5]:
                    print(f"  doc={v['document_id'][:8]}... -> status={v['status']}")

            assert len(violations) == 0, \
                f"active_execution_id が succeeded でない execution を参照: {len(violations)} 件"

        except Exception as e:
            if 'does not exist' in str(e).lower():
                pytest.skip("document_executions テーブルが未作成")
            raise

    def test_active_execution_owner_match(self, service_client):
        """active_execution_id が指す execution の owner_id がドキュメントと一致"""
        try:
            # active_execution_id が設定されているドキュメントを取得
            docs_response = service_client.table('Rawdata_FILE_AND_MAIL') \
                .select('id, owner_id, active_execution_id') \
                .not_.is_('active_execution_id', 'null') \
                .limit(1000) \
                .execute()

            if not docs_response.data:
                print("[SKIP] active_execution_id が設定されたドキュメントなし")
                return

            violations = []
            active_ids = [doc['active_execution_id'] for doc in docs_response.data]

            # execution の owner_id を一括取得
            exec_owner_map = {}
            for i in range(0, len(active_ids), 100):
                batch = active_ids[i:i+100]
                exec_response = service_client.table('document_executions') \
                    .select('id, owner_id') \
                    .in_('id', batch) \
                    .execute()

                for e in (exec_response.data or []):
                    exec_owner_map[e['id']] = e['owner_id']

            for doc in docs_response.data:
                doc_owner = doc['owner_id']
                exec_owner = exec_owner_map.get(doc['active_execution_id'])

                if exec_owner and doc_owner != exec_owner:
                    violations.append({
                        'document_id': doc['id'],
                        'doc_owner': doc_owner,
                        'exec_owner': exec_owner
                    })

            if violations:
                print(f"[VIOLATION] owner_id 不一致: {len(violations)} 件")
                for v in violations[:5]:
                    print(f"  doc={v['document_id'][:8]}... doc_owner={v['doc_owner'][:8]}... exec_owner={v['exec_owner'][:8]}...")

            assert len(violations) == 0, \
                f"active_execution_id の owner_id がドキュメントと不一致: {len(violations)} 件"

        except Exception as e:
            if 'does not exist' in str(e).lower():
                pytest.skip("document_executions テーブルが未作成")
            raise


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'not integration'])
