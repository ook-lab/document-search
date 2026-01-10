"""
titleが空の行をpendingに戻すスクリプト（全workspace対象）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.common.database.client import DatabaseClient
from loguru import logger

def reset_empty_title_to_pending():
    """titleが空（NULLまたは空文字列）の行をpendingに更新"""
    db = DatabaseClient()

    logger.info("titleが空の行を検索中...")

    # 全workspace で title が空の行を取得
    result = db.client.table('Rawdata_FILE_AND_MAIL').select(
        'id, file_name, title, processing_status, workspace'
    ).execute()

    # titleが空の行をフィルタリング
    empty_title_rows = []
    for row in result.data:
        title = row.get('title')
        if not title or title.strip() == '':
            empty_title_rows.append(row)

    logger.info(f"titleが空の行: {len(empty_title_rows)}件")

    if not empty_title_rows:
        logger.info("更新対象なし")
        return

    # 確認
    print("\n更新対象:")
    for i, row in enumerate(empty_title_rows[:10], 1):
        print(f"  {i}. {row['file_name']} (processing_status: {row.get('processing_status')}, workspace: {row['workspace']})")
    if len(empty_title_rows) > 10:
        print(f"  ... 他 {len(empty_title_rows) - 10}件")

    confirm = input(f"\n{len(empty_title_rows)}件を pending に更新しますか？ (yes/no): ")
    if confirm.lower() != 'yes':
        logger.info("キャンセルしました")
        return

    # 一括更新
    logger.info("更新中...")
    updated_count = 0
    for row in empty_title_rows:
        try:
            db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_status': 'pending'
            }).eq('id', row['id']).execute()
            updated_count += 1

            if updated_count % 10 == 0:
                logger.info(f"進捗: {updated_count}/{len(empty_title_rows)}")
        except Exception as e:
            logger.error(f"更新失敗 (id={row['id']}): {e}")

    logger.info(f"✅ 完了: {updated_count}件を pending に更新しました")

if __name__ == '__main__':
    reset_empty_title_to_pending()
