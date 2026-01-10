"""
Stage E～Kで生成された全データを削除して、全件をpendingに戻すスクリプト
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.common.database.client import DatabaseClient
from loguru import logger

def reset_all_to_pending():
    """Stage E～Kで生成されたデータをクリアして全件をpendingに"""
    db = DatabaseClient()

    # 1. 全ドキュメント数を確認
    logger.info("全ドキュメント数を確認中...")
    result = db.client.table('Rawdata_FILE_AND_MAIL').select('id, file_name, workspace').execute()
    total_count = len(result.data)
    logger.info(f"対象ドキュメント数: {total_count}件")

    if total_count == 0:
        logger.info("対象ドキュメントなし")
        return

    # 確認
    print(f"\n⚠️  警告: {total_count}件のドキュメントから以下のデータを削除します:")
    print("  - attachment_text (Stage E)")
    print("  - summary (Stage I)")
    print("  - tags (Stage I)")
    print("  - title (生成されたタイトル)")
    print("  - chunk_count (Stage K)")
    print("  - search_index の全レコード")
    print("\nそして、全件を processing_status='pending' に戻します。")

    confirm = input(f"\n本当に実行しますか？ (yes/no): ")
    if confirm.lower() != 'yes':
        logger.info("キャンセルしました")
        return

    # 2. search_index の全レコードを削除
    logger.info("search_index の全レコードを削除中...")
    try:
        # document_id のリストを取得
        doc_ids = [row['id'] for row in result.data]

        deleted_count = 0
        for doc_id in doc_ids:
            try:
                db.client.table('10_ix_search_index').delete().eq('document_id', doc_id).execute()
                deleted_count += 1

                if deleted_count % 50 == 0:
                    logger.info(f"search_index 削除進捗: {deleted_count}/{total_count}")
            except Exception as e:
                logger.warning(f"search_index 削除失敗 (document_id={doc_id}): {e}")

        logger.info(f"✅ search_index 削除完了: {deleted_count}件")
    except Exception as e:
        logger.error(f"search_index 削除エラー: {e}")

    # 3. Rawdata_FILE_AND_MAIL の生成データをクリア & pending に
    logger.info("Rawdata_FILE_AND_MAIL の生成データをクリア中...")

    try:
        # 一括更新（全ステージのカラムを削除）
        db.client.table('Rawdata_FILE_AND_MAIL').update({
            # Stage E
            'stage_e1_text': None,
            'stage_e2_text': None,
            'stage_e3_text': None,
            'stage_e4_text': None,
            'stage_e5_text': None,
            'attachment_text': None,
            # Stage F
            'stage_f_text_ocr': None,
            'stage_f_layout_ocr': None,
            'stage_f_visual_elements': None,
            # Stage H
            'stage_h_normalized': None,
            # Stage I
            'stage_i_structured': None,
            'summary': None,
            'tags': None,
            'title': None,
            # Stage J
            'stage_j_chunks_json': None,
            # Stage K
            'chunk_count': 0,
            # Processing status
            'processing_status': 'pending'
        }).neq('id', '00000000-0000-0000-0000-000000000000').execute()  # 全件対象（ダミー条件）

        logger.info(f"✅ Rawdata_FILE_AND_MAIL 更新完了: {total_count}件")
    except Exception as e:
        logger.error(f"Rawdata_FILE_AND_MAIL 更新エラー: {e}")
        return

    # 4. 確認
    logger.info("\n更新後の状態を確認中...")
    check_result = db.client.table('Rawdata_FILE_AND_MAIL').select(
        'processing_status'
    ).eq('processing_status', 'pending').execute()

    pending_count = len(check_result.data)
    logger.info(f"✅ 完了: {pending_count}件が pending になりました")

if __name__ == '__main__':
    reset_all_to_pending()
