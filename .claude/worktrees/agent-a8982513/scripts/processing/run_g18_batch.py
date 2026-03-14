"""
G18 バッチ実行スクリプト

09_unified_documents の全行（または指定条件の行）に対して
G18CandidateExtractor を実行し、10_report_candidates を生成する。

【使い方】
    # dry-run（何件処理するか確認のみ）
    python run_g18_batch.py

    # 全ドキュメントを処理
    python run_g18_batch.py --execute

    # 件数制限
    python run_g18_batch.py --limit 100 --execute

    # 特定 person のみ
    python run_g18_batch.py --person 育哉 --execute

    # 特定 raw_table のみ
    python run_g18_batch.py --raw-table 02_gcal_01_raw --execute

    # 1件だけ（テスト）
    python run_g18_batch.py --limit 1 --execute
"""
import sys
import argparse
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from loguru import logger
from shared.common.database.client import DatabaseClient
from shared.pipeline.stage_g.g18_candidate_extractor import G18CandidateExtractor


def fetch_docs(db, args):
    """09_unified_documents から対象行を取得する"""
    q = (
        db.client
        .table("09_unified_documents")
        .select("id,raw_id,raw_table,person,source,category,title,start_at,end_at,due_date,ui_data")
    )
    if args.person:
        q = q.eq("person", args.person)
    if args.raw_table:
        q = q.eq("raw_table", args.raw_table)
    if args.limit:
        q = q.limit(args.limit)

    result = q.execute()
    return result.data or []


def main():
    parser = argparse.ArgumentParser(description="G18 バッチ処理")
    parser.add_argument("--execute",   action="store_true", help="実際に書き込む（省略時 dry-run）")
    parser.add_argument("--limit",     type=int,   default=None,  help="処理件数上限")
    parser.add_argument("--person",    type=str,   default=None,  help="person フィルター")
    parser.add_argument("--raw-table", type=str,   default=None,  help="raw_table フィルター")
    args = parser.parse_args()

    logger.info("G18 バッチ処理 開始")
    db = DatabaseClient(use_service_role=True)

    docs = fetch_docs(db, args)
    logger.info(f"取得件数: {len(docs)}")

    if not args.execute:
        logger.info("[dry-run] --execute を付けると実際に処理します")
        for d in docs[:10]:
            logger.info(f"  {d['id']} | {d.get('raw_table')} | {d.get('person')} | {d.get('title', '')[:60]}")
        if len(docs) > 10:
            logger.info(f"  ... 残り {len(docs) - 10} 件")
        return

    extractor = G18CandidateExtractor(db)
    result = extractor.process_batch(docs, id_field="id")

    logger.info("=" * 50)
    logger.info(f"処理済み: {result['processed']} / {len(docs)}")
    logger.info(f"挿入合計: {result['total_inserted']}")
    if result["errors"]:
        logger.warning(f"エラー:   {len(result['errors'])} 件")
        for e in result["errors"][:5]:
            logger.warning(f"  {e}")
    logger.info("G18 バッチ処理 完了")


if __name__ == "__main__":
    main()
