"""
Stage G テストスクリプト（UIデリバリー構造化）

使用例:
    # Stage F の結果ファイルを指定
    python scripts/debug/test_stage_g.py "path/to/stage_f_result.json"
"""

import sys
import json
import argparse
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from shared.pipeline.stage_g import G1Controller

# DB保存用
import sys
doc_review_path = project_root / "services" / "doc-review"
if str(doc_review_path) not in sys.path:
    sys.path.insert(0, str(doc_review_path))

try:
    from services.document_service import update_stage_g_result
    from shared.common.database.client import DatabaseClient
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logger.warning("DB機能が利用できません（document_service/DatabaseClient）")


def main():
    """Stage G テスト実行"""
    parser = argparse.ArgumentParser(description='Stage G UIデリバリー構造化テスト')
    parser.add_argument('stage_f_result', type=str, help='Stage F 結果ファイル')
    parser.add_argument('--output', type=str, help='出力ディレクトリ（オプション）')
    parser.add_argument('--save-to-db', action='store_true', help='DB に保存する')
    parser.add_argument('--document-id', type=str, help='ドキュメントID（DB保存時）')

    args = parser.parse_args()

    # Stage F の結果を読み込み
    stage_f_path = Path(args.stage_f_result)
    if not stage_f_path.exists():
        logger.error(f"ファイルが存在しません: {stage_f_path}")
        sys.exit(1)

    with open(stage_f_path, 'r', encoding='utf-8') as f:
        stage_f_result = json.load(f)

    logger.info("=" * 60)
    logger.info("Stage G UIデリバリー構造化テスト開始")
    logger.info("=" * 60)

    # Stage G 実行
    controller = G1Controller()
    result = controller.process(stage_f_result)

    # 結果を表示
    logger.info("\n[Stage G] 実行結果:")
    logger.info(f"  成功: {result.get('success')}")

    if result.get('success'):
        ui_data = result.get('ui_data', {})

        # ドキュメント情報
        doc_info = ui_data.get('document_info', {})
        logger.info("\n  ドキュメント情報:")
        logger.info(f"    ├─ タイプ: {doc_info.get('document_type')}")
        logger.info(f"    └─ 年度: {doc_info.get('year_context')}")

        # セクション
        sections = ui_data.get('sections', [])
        if sections:
            logger.info(f"\n  セクション: {len(sections)}個")
            for i, section in enumerate(sections[:5]):
                logger.info(f"    [{i+1}] {section.get('type')}: {section.get('label')}")
            if len(sections) > 5:
                logger.info(f"    ... 他 {len(sections) - 5}個")

        # 表
        tables = ui_data.get('tables', [])
        if tables:
            logger.info(f"\n  表: {len(tables)}個")
            for i, table in enumerate(tables):
                table_id = table.get('table_id', f'T{i+1}')
                row_count = table.get('row_count', 0)
                col_count = table.get('col_count', 0)
                logger.info(f"    [{i+1}] {table_id}: {row_count}行 × {col_count}列")

                # カラム名のプレビュー
                columns = table.get('columns', [])
                if columns:
                    logger.info(f"        カラム: {', '.join(columns[:5])}")
                    if len(columns) > 5:
                        logger.info(f"        ... 他 {len(columns) - 5}列")

        # タイムライン（イベント）
        timeline = ui_data.get('timeline', [])
        if timeline:
            logger.info(f"\n  タイムライン: {len(timeline)}件")
            for i, event in enumerate(timeline[:3]):
                date = event.get('date', 'N/A')
                event_name = event.get('event', 'N/A')
                logger.info(f"    [{i+1}] {date}: {event_name}")
            if len(timeline) > 3:
                logger.info(f"    ... 他 {len(timeline) - 3}件")

        # アクション（タスク）
        actions = ui_data.get('actions', [])
        if actions:
            logger.info(f"\n  アクション: {len(actions)}件")
            for i, action in enumerate(actions[:3]):
                item = action.get('item', 'N/A')
                deadline = action.get('deadline', '')
                logger.info(f"    [{i+1}] {item} (期限: {deadline})")
            if len(actions) > 3:
                logger.info(f"    ... 他 {len(actions) - 3}件")

        # 注意事項
        notices = ui_data.get('notices', [])
        if notices:
            logger.info(f"\n  注意事項: {len(notices)}件")
            for i, notice in enumerate(notices[:3]):
                category = notice.get('category', '')
                content = notice.get('content', '')
                logger.info(f"    [{i+1}] {category}: {content[:50]}...")
            if len(notices) > 3:
                logger.info(f"    ... 他 {len(notices) - 3}件")

        # メタデータ
        metadata = ui_data.get('metadata', {})
        logger.info(f"\n  メタデータ:")
        logger.info(f"    ├─ セクション数: {metadata.get('section_count', 0)}")
        logger.info(f"    ├─ 表数: {metadata.get('table_count', 0)}")
        logger.info(f"    ├─ イベント数: {metadata.get('event_count', 0)}")
        logger.info(f"    ├─ タスク数: {metadata.get('task_count', 0)}")
        logger.info(f"    └─ 注意事項数: {metadata.get('notice_count', 0)}")

        # JSON形式で保存
        output_dir = Path(args.output) if args.output else stage_f_path.parent
        output_path = output_dir / "stage_g_result.json"

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"\n  結果を保存: {output_path}")

        # UI用データのみを別ファイルで保存
        ui_data_path = output_dir / "ui_data.json"
        with open(ui_data_path, 'w', encoding='utf-8') as f:
            json.dump(ui_data, f, ensure_ascii=False, indent=2)
        logger.info(f"  UI用データを保存: {ui_data_path}")

        # DB保存
        if args.save_to_db:
            if not DB_AVAILABLE:
                logger.error("  DB保存が要求されましたが、DB機能が利用できません")
            elif not args.document_id:
                logger.error("  DB保存には --document-id が必要です")
            else:
                logger.info(f"\n[DB保存] ドキュメントID: {args.document_id}")
                try:
                    db_client = DatabaseClient()
                    success = update_stage_g_result(
                        db_client=db_client,
                        document_id=args.document_id,
                        ui_data=ui_data
                    )

                    if success:
                        logger.info("  ✓ DB保存成功")
                    else:
                        logger.error("  ✗ DB保存失敗")

                except Exception as e:
                    logger.error(f"  ✗ DB保存エラー: {e}", exc_info=True)

    else:
        logger.error(f"  エラー: {result.get('error')}")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
