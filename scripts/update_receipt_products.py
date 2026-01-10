"""
レシート商品のsource_typeとdoc_typeを更新
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Windows環境でのUnicode出力設定
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

load_dotenv(root_dir / ".env")

from shared.common.database.client import DatabaseClient

db = DatabaseClient(use_service_role=True)

print("="*80)
print("レシート商品更新")
print("="*80)

# レシート商品のsource_typeとdoc_typeを更新
result = db.client.table('Rawdata_NETSUPER_items').update({
    'source_type': 'physical_store',
    'doc_type': 'Receipt',
    'workspace': 'shopping'
}).eq('organization', 'レシート').execute()

print(f"\n✅ {len(result.data)}件のレシート商品を更新しました")
print("   source_type: physical_store")
print("   doc_type: Receipt")
print("   workspace: shopping")
print("   organization: レシート（元データなしのため保持）")

print("\n" + "="*80)
