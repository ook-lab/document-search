#!/usr/bin/env python3
"""
状態遷移ガード実装のためのスキーマ分析
- Rawdataテーブルの状態関連カラムを確認
- 既存の制約を確認
- processing_statusの値分布を確認
- search_indexとの整合性を確認
"""
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
client = create_client(url, key)

print("=" * 70)
print("Step A: Rawdataテーブルのスキーマ分析")
print("=" * 70)

# 1. 状態関連カラムの確認
print("\n[1] Rawdata_FILE_AND_MAIL の状態関連カラム:")
try:
    response = client.table('Rawdata_FILE_AND_MAIL').select('*').limit(1).execute()
    if response.data:
        row = response.data[0]
        status_cols = [k for k in row.keys() if any(x in k.lower() for x in ['status', 'error', 'stage', 'failed'])]
        print(f"  状態関連カラム: {status_cols}")
        for col in status_cols:
            print(f"    - {col}: {row.get(col)}")
except Exception as e:
    print(f"  エラー: {e}")

# 2. processing_status の値分布
print("\n[2] processing_status の値分布:")
try:
    # 全件取得してPython側で集計
    response = client.table('Rawdata_FILE_AND_MAIL').select('processing_status').execute()
    if response.data:
        from collections import Counter
        statuses = [r.get('processing_status') for r in response.data]
        dist = Counter(statuses)
        total = len(statuses)
        print(f"  総件数: {total}")
        for status, count in dist.most_common():
            pct = count / total * 100
            print(f"    {status}: {count} ({pct:.1f}%)")
except Exception as e:
    print(f"  エラー: {e}")

# 3. completed なのに search_index にチャンクが無い行を探す
print("\n[3] 問題のある行の検出 (completed なのにチャンク0件):")
try:
    # completedの行を取得
    completed = client.table('Rawdata_FILE_AND_MAIL').select('id, file_name, processing_status').eq('processing_status', 'completed').execute()

    if completed.data:
        problem_rows = []
        for row in completed.data[:50]:  # 最初の50件をチェック
            doc_id = row['id']
            # search_indexでこのdoc_idのチャンク数を確認
            chunks = client.table('10_ix_search_index').select('id', count='exact').eq('document_id', doc_id).execute()
            chunk_count = chunks.count if chunks.count else 0
            if chunk_count == 0:
                problem_rows.append({
                    'id': doc_id,
                    'file_name': row.get('file_name', 'Unknown'),
                    'chunk_count': chunk_count
                })

        if problem_rows:
            print(f"  問題のある行: {len(problem_rows)}件 (最初の50件中)")
            for pr in problem_rows[:5]:
                print(f"    id={pr['id'][:8]}..., file={pr['file_name'][:30]}, chunks={pr['chunk_count']}")
        else:
            print("  問題のある行: 0件 (チェックした50件中)")
    else:
        print("  completed の行が存在しません")
except Exception as e:
    print(f"  エラー: {e}")

# 4. error_message / processing_error の存在確認
print("\n[4] エラー関連カラムの確認:")
try:
    response = client.table('Rawdata_FILE_AND_MAIL').select('*').limit(1).execute()
    if response.data:
        row = response.data[0]
        error_cols = ['error_message', 'processing_error', 'failed_stage', 'failed_at']
        for col in error_cols:
            exists = col in row
            print(f"  {col}: {'存在' if exists else '存在しない'}")
except Exception as e:
    print(f"  エラー: {e}")

# 5. 主キー型の確認
print("\n[5] 主キー型の整合性:")
try:
    # Rawdataのidサンプル
    raw = client.table('Rawdata_FILE_AND_MAIL').select('id').limit(1).execute()
    # search_indexのdoc_idサンプル
    idx = client.table('search_index').select('doc_id').limit(1).execute()

    if raw.data and idx.data:
        raw_id = raw.data[0]['id']
        idx_id = idx.data[0]['doc_id']
        print(f"  Rawdata.id サンプル: {raw_id} (type: {type(raw_id).__name__})")
        print(f"  search_index.doc_id サンプル: {idx_id} (type: {type(idx_id).__name__})")
        print(f"  型整合: {'OK' if type(raw_id) == type(idx_id) else 'MISMATCH'}")
except Exception as e:
    print(f"  エラー: {e}")

print("\n" + "=" * 70)
print("分析完了")
print("=" * 70)
