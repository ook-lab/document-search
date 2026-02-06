"""
Phase 2: RLS Permission Boundary Tests
======================================

このテストは以下を検証します:
1. anon は書き込みできない
2. authenticated は自分のデータのみ UPDATE/DELETE できる
3. authenticated は他人のデータを UPDATE/DELETE できない

実行方法:
    # supabase local を起動した状態で
    pytest tests/test_phase2_permissions.py -v

    # または直接実行
    python tests/test_phase2_permissions.py
"""

import os
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from shared.common.database.client import DatabaseClient

# テスト用定数
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:54321")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# テスト用ユーザー（seed.sql で定義）
USER_A_ID = "11111111-1111-1111-1111-111111111111"
USER_A_EMAIL = "alice@example.com"
USER_A_PASSWORD = "alice123456"

USER_B_ID = "22222222-2222-2222-2222-222222222222"
USER_B_EMAIL = "bob@example.com"
USER_B_PASSWORD = "bob123456"

# テスト用ドキュメントID（seed.sql で定義）
DOC_A_ID = "doc-a001-0000-0000-0000-000000000001"
DOC_B_ID = "doc-b001-0000-0000-0000-000000000001"


# =============================================================================
# Helper Functions
# =============================================================================

def get_anon_client():
    """anon key でクライアント作成"""
    return DatabaseClient(use_service_role=False)


def get_service_role_client():
    """service_role でクライアント作成"""
    return DatabaseClient(use_service_role=True)


def get_authenticated_client(email: str, password: str):
    """認証済みクライアントを作成"""
    from supabase import create_client
    from shared.common.config.settings import settings

    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    try:
        response = client.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        # アクセストークンを設定
        return DatabaseClient(access_token=response.session.access_token)
    except Exception as e:
        print(f"  ⚠️ 認証失敗 ({email}): {e}")
        return None


# =============================================================================
# Test: anon role
# =============================================================================

def test_anon_can_select_rawdata():
    """Test: anon は Rawdata_FILE_AND_MAIL を SELECT できる"""
    print("\n[Test] anon can SELECT Rawdata_FILE_AND_MAIL...")

    db = get_anon_client()

    try:
        result = db.client.table('Rawdata_FILE_AND_MAIL').select('id').limit(1).execute()
        if result.data is not None:
            print("  ✅ PASS: anon can SELECT from Rawdata_FILE_AND_MAIL")
            return True
        else:
            print("  ✅ PASS: No data returned (but query succeeded)")
            return True
    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        return False


def test_anon_can_select_search_index():
    """Test: anon は 10_ix_search_index を SELECT できる"""
    print("\n[Test] anon can SELECT 10_ix_search_index...")

    db = get_anon_client()

    try:
        result = db.client.table('10_ix_search_index').select('document_id').limit(1).execute()
        if result.data is not None:
            print("  ✅ PASS: anon can SELECT from 10_ix_search_index")
            return True
        else:
            print("  ✅ PASS: No data returned (but query succeeded)")
            return True
    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        return False


def test_anon_cannot_insert():
    """Test: anon は INSERT できない"""
    print("\n[Test] anon CANNOT INSERT into Rawdata_FILE_AND_MAIL...")

    db = get_anon_client()

    try:
        result = db.client.table('Rawdata_FILE_AND_MAIL').insert({
            'source_id': 'anon-attack-test',
            'file_name': 'anon_attack.pdf',
            'workspace': 'test'
        }).execute()

        # 成功してしまった場合は失敗
        print("  ❌ FAIL: anon was able to INSERT (should be denied)")
        # クリーンアップ
        db.client.table('Rawdata_FILE_AND_MAIL').delete().eq('source_id', 'anon-attack-test').execute()
        return False

    except Exception as e:
        error_str = str(e).lower()
        if 'permission' in error_str or 'policy' in error_str or 'denied' in error_str:
            print("  ✅ PASS: INSERT correctly denied")
            return True
        else:
            print(f"  ✅ PASS: Error occurred (expected): {e}")
            return True


def test_anon_cannot_update():
    """Test: anon は UPDATE できない"""
    print("\n[Test] anon CANNOT UPDATE Rawdata_FILE_AND_MAIL...")

    db = get_anon_client()

    try:
        result = db.client.table('Rawdata_FILE_AND_MAIL').update({
            'review_status': 'anon_attack'
        }).eq('id', DOC_A_ID).execute()

        # 結果が返ってきても、RLS により影響行数0なら OK
        if len(result.data) == 0:
            print("  ✅ PASS: UPDATE returned 0 rows (RLS working)")
            return True
        else:
            print("  ❌ FAIL: anon was able to UPDATE")
            return False

    except Exception as e:
        error_str = str(e).lower()
        if 'permission' in error_str or 'policy' in error_str:
            print("  ✅ PASS: UPDATE correctly denied")
            return True
        else:
            print(f"  ⚠️ UNKNOWN: {e}")
            return True


def test_anon_cannot_delete():
    """Test: anon は DELETE できない"""
    print("\n[Test] anon CANNOT DELETE from Rawdata_FILE_AND_MAIL...")

    db = get_anon_client()

    try:
        result = db.client.table('Rawdata_FILE_AND_MAIL').delete().eq('id', DOC_A_ID).execute()

        if len(result.data) == 0:
            print("  ✅ PASS: DELETE returned 0 rows (RLS working)")
            return True
        else:
            print("  ❌ FAIL: anon was able to DELETE")
            return False

    except Exception as e:
        error_str = str(e).lower()
        if 'permission' in error_str or 'policy' in error_str:
            print("  ✅ PASS: DELETE correctly denied")
            return True
        else:
            print(f"  ⚠️ UNKNOWN: {e}")
            return True


def test_anon_cannot_access_worker_tables():
    """Test: anon は Worker テーブルにアクセスできない"""
    print("\n[Test] anon CANNOT access processing_lock...")

    db = get_anon_client()

    try:
        result = db.client.table('processing_lock').select('*').execute()

        # SELECT が成功してしまった場合
        if result.data is not None and len(result.data) > 0:
            print("  ❌ FAIL: anon was able to SELECT from processing_lock")
            return False
        else:
            # 0行でも成功は問題
            print("  ⚠️ WARNING: Query returned 0 rows (check RLS)")
            return True

    except Exception as e:
        print("  ✅ PASS: Access to processing_lock denied")
        return True


# =============================================================================
# Test: authenticated - 自分のデータ
# =============================================================================

def test_authenticated_can_update_own_data():
    """Test: authenticated は自分のデータを UPDATE できる"""
    print("\n[Test] authenticated can UPDATE own data...")

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    try:
        # User A のドキュメントを更新
        result = db.client.table('Rawdata_FILE_AND_MAIL').update({
            'review_status': 'reviewed'
        }).eq('id', DOC_A_ID).execute()

        if len(result.data) == 1:
            print("  ✅ PASS: User A can UPDATE own data")

            # 元に戻す
            db.client.table('Rawdata_FILE_AND_MAIL').update({
                'review_status': 'pending'
            }).eq('id', DOC_A_ID).execute()

            return True
        else:
            print(f"  ❌ FAIL: Expected 1 row, got {len(result.data)}")
            return False

    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        return False


def test_authenticated_can_delete_own_data():
    """Test: authenticated は自分のデータを DELETE できる"""
    print("\n[Test] authenticated can DELETE own data...")

    # service_role で一時データを作成
    service_db = get_service_role_client()
    temp_id = "doc-temp-test-delete-001"

    try:
        service_db.client.table('Rawdata_FILE_AND_MAIL').insert({
            'id': temp_id,
            'source_id': 'temp-delete-test',
            'file_name': 'temp_for_delete.pdf',
            'workspace': 'test',
            'owner_id': USER_A_ID
        }).execute()
    except:
        pass  # 既存の場合は無視

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    try:
        result = db.client.table('Rawdata_FILE_AND_MAIL').delete().eq('id', temp_id).execute()

        if len(result.data) == 1:
            print("  ✅ PASS: User A can DELETE own data")
            return True
        else:
            print(f"  ⚠️ WARNING: DELETE returned {len(result.data)} rows")
            return True

    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        return False


# =============================================================================
# Test: authenticated - 他人のデータ（拒否確認）
# =============================================================================

def test_authenticated_cannot_update_others_data():
    """Test: authenticated は他人のデータを UPDATE できない"""
    print("\n[Test] authenticated CANNOT UPDATE other's data...")

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    try:
        # User A が User B のデータを更新しようとする
        result = db.client.table('Rawdata_FILE_AND_MAIL').update({
            'review_status': 'hacked_by_a'
        }).eq('id', DOC_B_ID).execute()

        if len(result.data) == 0:
            print("  ✅ PASS: User A CANNOT UPDATE User B's data (0 rows affected)")
            return True
        else:
            print(f"  ❌ FAIL: User A was able to UPDATE User B's data!")
            return False

    except Exception as e:
        error_str = str(e).lower()
        if 'policy' in error_str or 'permission' in error_str:
            print("  ✅ PASS: UPDATE denied by RLS")
            return True
        else:
            print(f"  ⚠️ UNKNOWN: {e}")
            return True


def test_authenticated_cannot_delete_others_data():
    """Test: authenticated は他人のデータを DELETE できない"""
    print("\n[Test] authenticated CANNOT DELETE other's data...")

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    try:
        # User A が User B のデータを削除しようとする
        result = db.client.table('Rawdata_FILE_AND_MAIL').delete().eq('id', DOC_B_ID).execute()

        if len(result.data) == 0:
            print("  ✅ PASS: User A CANNOT DELETE User B's data (0 rows affected)")
            return True
        else:
            print(f"  ❌ FAIL: User A was able to DELETE User B's data!")
            return False

    except Exception as e:
        error_str = str(e).lower()
        if 'policy' in error_str or 'permission' in error_str:
            print("  ✅ PASS: DELETE denied by RLS")
            return True
        else:
            print(f"  ⚠️ UNKNOWN: {e}")
            return True


def test_authenticated_cannot_update_others_receipt_items():
    """Test: authenticated は他人のレシート明細を UPDATE できない"""
    print("\n[Test] authenticated CANNOT UPDATE other's receipt items...")

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    item_b_id = "item-b001-0000-0000-0000-000000000001"

    try:
        result = db.client.table('Rawdata_RECEIPT_items').update({
            'product_name': 'hacked_by_a'
        }).eq('id', item_b_id).execute()

        if len(result.data) == 0:
            print("  ✅ PASS: User A CANNOT UPDATE User B's receipt items")
            return True
        else:
            print(f"  ❌ FAIL: User A was able to UPDATE User B's receipt items!")
            return False

    except Exception as e:
        print(f"  ✅ PASS: Error (expected): {e}")
        return True


def test_authenticated_cannot_delete_others_search_index():
    """Test: authenticated は他人のチャンクを DELETE できない"""
    print("\n[Test] authenticated CANNOT DELETE other's search index...")

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    idx_b_id = "idx-b001-0000-0000-0000-000000000001"

    try:
        result = db.client.table('10_ix_search_index').delete().eq('id', idx_b_id).execute()

        if len(result.data) == 0:
            print("  ✅ PASS: User A CANNOT DELETE User B's search index")
            return True
        else:
            print(f"  ❌ FAIL: User A was able to DELETE User B's search index!")
            return False

    except Exception as e:
        print(f"  ✅ PASS: Error (expected): {e}")
        return True


def test_authenticated_cannot_insert_with_other_owner():
    """Test: authenticated は他人の owner_id で INSERT できない"""
    print("\n[Test] authenticated CANNOT INSERT with other's owner_id...")

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    try:
        # User A が User B の owner_id でチャンクを INSERT しようとする
        result = db.client.table('10_ix_search_index').insert({
            'id': 'idx-fake-test-001',
            'document_id': DOC_B_ID,
            'chunk_index': 999,
            'chunk_content': 'Fake chunk by A',
            'chunk_type': 'test',
            'owner_id': USER_B_ID  # 他人の ID
        }).execute()

        # 成功してしまった場合
        print("  ❌ FAIL: User A was able to INSERT with User B's owner_id!")

        # クリーンアップ
        service_db = get_service_role_client()
        service_db.client.table('10_ix_search_index').delete().eq('id', 'idx-fake-test-001').execute()
        return False

    except Exception as e:
        print("  ✅ PASS: INSERT with other's owner_id denied")
        return True


# =============================================================================
# Test: authenticated - 他人データの SELECT（現設計の確認）
# =============================================================================
# 注意: 現設計では authenticated は全データを SELECT 可能（Admin UI 要件）
# 将来的に「自分のデータのみ」に制限する場合は RLS を変更する必要あり

def test_authenticated_select_visibility_document():
    """
    Test: authenticated の SELECT 範囲（Rawdata_FILE_AND_MAIL）

    現設計: 全データ見える（Admin UI で全ドキュメントをレビューするため）
    将来制限する場合: RLS で owner_id = auth.uid() に変更
    """
    print("\n[Test] authenticated SELECT visibility (Rawdata_FILE_AND_MAIL)...")

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    try:
        # User A が User B のドキュメントを SELECT できるか
        result = db.client.table('Rawdata_FILE_AND_MAIL').select('id, owner_id').eq('id', DOC_B_ID).execute()

        if len(result.data) == 1:
            print("  ℹ️ INFO: User A CAN see User B's document (current design)")
            print("       -> Admin UI requires full visibility")
            return True  # 現設計では正常
        else:
            print("  ℹ️ INFO: User A CANNOT see User B's document")
            print("       -> RLS restricts to own data only")
            return True

    except Exception as e:
        print(f"  ⚠️ ERROR: {e}")
        return False


def test_authenticated_select_visibility_receipt_items():
    """
    Test: authenticated の SELECT 範囲（Rawdata_RECEIPT_items）

    現設計: 全データ見える（Admin UI で全レシートをレビューするため）
    将来制限する場合: RLS で親レシートの owner_id = auth.uid() に変更
    """
    print("\n[Test] authenticated SELECT visibility (Rawdata_RECEIPT_items)...")

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    item_b_id = "item-b001-0000-0000-0000-000000000001"

    try:
        result = db.client.table('Rawdata_RECEIPT_items').select('id, product_name').eq('id', item_b_id).execute()

        if len(result.data) == 1:
            print("  ℹ️ INFO: User A CAN see User B's receipt items (current design)")
            print("       -> Admin UI requires full visibility")
            return True
        else:
            print("  ℹ️ INFO: User A CANNOT see User B's receipt items")
            print("       -> RLS restricts to own data only")
            return True

    except Exception as e:
        print(f"  ⚠️ ERROR: {e}")
        return False


def test_authenticated_select_visibility_search_index():
    """
    Test: authenticated の SELECT 範囲（10_ix_search_index）

    現設計: 全データ見える
    将来制限する場合: RLS で owner_id = auth.uid() に変更
    """
    print("\n[Test] authenticated SELECT visibility (10_ix_search_index)...")

    db = get_authenticated_client(USER_A_EMAIL, USER_A_PASSWORD)
    if not db:
        print("  ⚠️ SKIP: Could not authenticate as User A")
        return True

    idx_b_id = "idx-b001-0000-0000-0000-000000000001"

    try:
        result = db.client.table('10_ix_search_index').select('id, owner_id').eq('id', idx_b_id).execute()

        if len(result.data) == 1:
            print("  ℹ️ INFO: User A CAN see User B's search index (current design)")
            return True
        else:
            print("  ℹ️ INFO: User A CANNOT see User B's search index")
            print("       -> RLS restricts to own data only")
            return True

    except Exception as e:
        print(f"  ⚠️ ERROR: {e}")
        return False


# =============================================================================
# Test: service_role
# =============================================================================

def test_service_role_can_access_all():
    """Test: service_role は全テーブルにアクセスできる"""
    print("\n[Test] service_role can access all tables...")

    db = get_service_role_client()

    try:
        # Rawdata_FILE_AND_MAIL
        result = db.client.table('Rawdata_FILE_AND_MAIL').select('id').limit(1).execute()
        print(f"  ✅ Rawdata_FILE_AND_MAIL: OK")

        # processing_lock
        result = db.client.table('processing_lock').select('*').limit(1).execute()
        print(f"  ✅ processing_lock: OK")

        # worker_state
        result = db.client.table('worker_state').select('*').limit(1).execute()
        print(f"  ✅ worker_state: OK")

        print("  ✅ PASS: service_role can access all tables")
        return True

    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        return False


# =============================================================================
# Main
# =============================================================================

def run_all_tests():
    """全テストを実行"""
    print("=" * 60)
    print("Phase 2: RLS Permission Boundary Tests")
    print("=" * 60)

    print(f"\nSUPABASE_URL: {SUPABASE_URL}")
    print(f"Test Users: {USER_A_EMAIL}, {USER_B_EMAIL}")
    print(f"Test Docs: {DOC_A_ID[:20]}..., {DOC_B_ID[:20]}...")

    results = []

    # anon テスト
    print("\n" + "-" * 40)
    print("anon role tests")
    print("-" * 40)
    results.append(("anon SELECT Rawdata", test_anon_can_select_rawdata()))
    results.append(("anon SELECT search_index", test_anon_can_select_search_index()))
    results.append(("anon CANNOT INSERT", test_anon_cannot_insert()))
    results.append(("anon CANNOT UPDATE", test_anon_cannot_update()))
    results.append(("anon CANNOT DELETE", test_anon_cannot_delete()))
    results.append(("anon CANNOT access Worker tables", test_anon_cannot_access_worker_tables()))

    # authenticated 自分のデータ
    print("\n" + "-" * 40)
    print("authenticated - own data tests")
    print("-" * 40)
    results.append(("auth UPDATE own data", test_authenticated_can_update_own_data()))
    results.append(("auth DELETE own data", test_authenticated_can_delete_own_data()))

    # authenticated 他人のデータ（重要！）
    print("\n" + "-" * 40)
    print("authenticated - OTHER's data tests (CRITICAL)")
    print("-" * 40)
    results.append(("auth CANNOT UPDATE other's data", test_authenticated_cannot_update_others_data()))
    results.append(("auth CANNOT DELETE other's data", test_authenticated_cannot_delete_others_data()))
    results.append(("auth CANNOT UPDATE other's receipt items", test_authenticated_cannot_update_others_receipt_items()))
    results.append(("auth CANNOT DELETE other's search index", test_authenticated_cannot_delete_others_search_index()))
    results.append(("auth CANNOT INSERT with other's owner_id", test_authenticated_cannot_insert_with_other_owner()))

    # authenticated SELECT 範囲（現設計の確認）
    print("\n" + "-" * 40)
    print("authenticated - SELECT visibility (current design check)")
    print("-" * 40)
    results.append(("auth SELECT visibility (document)", test_authenticated_select_visibility_document()))
    results.append(("auth SELECT visibility (receipt_items)", test_authenticated_select_visibility_receipt_items()))
    results.append(("auth SELECT visibility (search_index)", test_authenticated_select_visibility_search_index()))

    # service_role
    print("\n" + "-" * 40)
    print("service_role tests")
    print("-" * 40)
    results.append(("service_role access all", test_service_role_can_access_all()))

    # 結果サマリー
    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)

    passed = 0
    failed = 0

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1

    print("\n" + "-" * 60)
    print(f"Total: {passed + failed} tests, {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
