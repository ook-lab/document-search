"""
waseda_academy ワークスペースの既存データを修正
person と organization を文字列から配列に変換

実行方法:
    python3 fix_waseda_person_organization.py
"""
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from shared.common.database.client import DatabaseClient

def fix_waseda_data():
    """waseda_academy ワークスペースの person/organization を配列に変換"""

    db = DatabaseClient()

    # waseda_academy のレコードを取得
    print("waseda_academy ワークスペースのレコードを取得中...")
    result = db.client.table('Rawdata_FILE_AND_MAIL').select('*').eq(
        'workspace', 'waseda_academy'
    ).execute()

    if not result.data:
        print("対象レコードが見つかりませんでした")
        return

    print(f"対象レコード数: {len(result.data)}件")

    fixed_count = 0
    skipped_count = 0
    error_count = 0

    # waseda_academy の正しい値
    CORRECT_PERSON = ['育哉']
    CORRECT_ORGANIZATION = ['早稲田アカデミー']

    for record in result.data:
        record_id = record['id']
        person = record.get('person')
        organization = record.get('organization')

        needs_update = False
        update_data = {}

        # person を強制的に正しい値に設定
        if person != CORRECT_PERSON:
            update_data['person'] = CORRECT_PERSON
            needs_update = True
            print(f"  ID {record_id}: person {person} → {CORRECT_PERSON}")

        # organization を強制的に正しい値に設定
        if organization != CORRECT_ORGANIZATION:
            update_data['organization'] = CORRECT_ORGANIZATION
            needs_update = True
            print(f"  ID {record_id}: organization {organization} → {CORRECT_ORGANIZATION}")

        if needs_update:
            try:
                db.client.table('Rawdata_FILE_AND_MAIL').update(
                    update_data
                ).eq('id', record_id).execute()

                fixed_count += 1
                print(f"  ✅ ID {record_id} 修正完了")
            except Exception as e:
                error_count += 1
                print(f"  ❌ ID {record_id} 修正エラー: {e}")
        else:
            skipped_count += 1
            # print(f"  ⏭️  ID {record_id} スキップ（既に正しい値）")

    # サマリー
    print("\n" + "="*80)
    print("修正完了")
    print("="*80)
    print(f"修正: {fixed_count}件")
    print(f"スキップ: {skipped_count}件")
    print(f"エラー: {error_count}件")
    print(f"合計: {len(result.data)}件")
    print("="*80)

if __name__ == '__main__':
    fix_waseda_data()
