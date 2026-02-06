"""
Flaskアプリの起動テスト
環境変数の読み込みと依存関係をチェック
"""
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

print("=" * 70)
print("🔍 Flask アプリ起動テスト")
print("=" * 70)

# Step 1: 環境変数の読み込みテスト
print("\n[Step 1] 環境変数の読み込みテスト...")
try:
    from shared.common.config.settings import settings
    print(f"  ✓ SUPABASE_URL: {settings.SUPABASE_URL[:30]}..." if settings.SUPABASE_URL else "  ✗ SUPABASE_URL: 未設定")
    print(f"  ✓ SUPABASE_KEY: {'設定済み' if settings.SUPABASE_KEY else '未設定'}")
    print(f"  ✓ OPENAI_API_KEY: {'設定済み' if settings.OPENAI_API_KEY else '未設定'}")
    print(f"  ✓ GOOGLE_AI_API_KEY: {'設定済み' if settings.GOOGLE_AI_API_KEY else '未設定'}")

    if not all([settings.SUPABASE_URL, settings.SUPABASE_KEY, settings.OPENAI_API_KEY, settings.GOOGLE_AI_API_KEY]):
        print("\n  ⚠️ 一部の環境変数が未設定です")
    else:
        print("\n  ✅ 全ての環境変数が設定されています")
except Exception as e:
    print(f"  ❌ エラー: {e}")
    sys.exit(1)

# Step 2: 依存モジュールのインポートテスト
print("\n[Step 2] 依存モジュールのインポートテスト...")
modules_to_test = [
    "shared.common.database.client",
    "shared.ai.llm_client.llm_client",
    "shared.common.utils.query_expansion",
    "shared.common.utils.context_extractor",
    "shared.common.config.yaml_loader",
    "shared.common.config.model_tiers",
]

all_ok = True
for module_name in modules_to_test:
    try:
        __import__(module_name)
        print(f"  ✓ {module_name}")
    except Exception as e:
        print(f"  ❌ {module_name}: {e}")
        all_ok = False

if not all_ok:
    print("\n  ⚠️ 一部のモジュールのインポートに失敗しました")
else:
    print("\n  ✅ 全てのモジュールのインポートに成功しました")

# Step 3: データベース接続テスト
print("\n[Step 3] データベース接続テスト...")
try:
    from shared.common.database.client import DatabaseClient
    db_client = DatabaseClient()

    # 簡単なクエリを実行
    response = db_client.client.table('Rawdata_FILE_AND_MAIL').select('id').limit(1).execute()
    print(f"  ✓ データベース接続成功")
    print(f"  ✓ テーブルアクセス成功")
except Exception as e:
    print(f"  ❌ データベース接続エラー: {e}")
    all_ok = False

# Step 4: Flaskアプリのインポートテスト
print("\n[Step 4] Flaskアプリのインポートテスト...")
try:
    # services/doc-search をパスに追加
    doc_search_path = project_root / "services" / "doc-search"
    sys.path.insert(0, str(doc_search_path))

    # appモジュールをインポート
    import app as flask_app
    print(f"  ✓ Flaskアプリのインポート成功")
    print(f"  ✓ エンドポイント数: {len(flask_app.app.url_map._rules)}")
except Exception as e:
    print(f"  ❌ Flaskアプリのインポートエラー: {e}")
    import traceback
    traceback.print_exc()
    all_ok = False

# 最終結果
print("\n" + "=" * 70)
if all_ok:
    print("✅ 全てのテストに成功しました!")
    print("\nFlaskアプリを起動するには:")
    print("  cd C:\\Users\\ookub\\document-management-system")
    print("  python services\\doc-search\\app.py")
else:
    print("❌ 一部のテストに失敗しました。上記のエラーを確認してください。")
print("=" * 70)
