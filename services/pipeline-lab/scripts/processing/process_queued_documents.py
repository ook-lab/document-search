"""
CLI Document Processor - Worker の唯一の実行入口

【設計原則】
- 処理実行は CLI からのみ（Web UI は投入のみ）
- --execute が無い場合は dry-run（--doc-id 指定時のみ対象行を表示）
- キュー（dequeue / ack / nack / pending 列挙によるバッチ dequeue）とは連携しない

【使い方】
    # dry-run: pipeline_meta 1件の確認
    python process_queued_documents.py --doc-id <uuid>

    # 単一ジョブ（meta_id = pipeline_meta.id）
    python process_queued_documents.py --doc-id <uuid> --execute

    # 元PDFの1ページ（0始まり index）だけを切り出して処理（metadata に書き込んでから実行）
    python process_queued_documents.py --doc-id <uuid> --pdf-page-index 1 --execute

    # Run Request（payload に doc_id が必須）
    python process_queued_documents.py --run-request <uuid> --execute
"""
import sys
import asyncio
import argparse
from pathlib import Path

_lab_dir = Path(__file__).resolve().parent.parent.parent  # services/pipeline-lab/
if str(_lab_dir) not in sys.path:
    sys.path.insert(0, str(_lab_dir))

from loguru import logger

from dms.pipeline.pipeline_manager import PipelineManager
from dms.common.database.client import DatabaseClient
from dms.logging import setup_master_logging


def print_dry_run_doc(doc_id: str):
    """dry-run: pipeline_meta 1件を表示（実行しない）"""
    logger.info("\n" + "=" * 80)
    logger.info("【DRY-RUN MODE】処理対象の確認（実行されません）")
    logger.info("=" * 80)

    db = DatabaseClient(use_service_role=True)
    result = db.client.table('pipeline_meta').select(
        'id, raw_id, raw_table, person, source, processing_status, metadata'
    ).eq('id', doc_id).execute()

    if not result.data:
        logger.warning(f"pipeline_meta が見つかりません: {doc_id}")
        return

    doc = result.data[0]
    title = ''
    try:
        ud = db.client.table('09_unified_documents').select('title').eq(
            'raw_id', doc['raw_id']
        ).eq('raw_table', doc['raw_table']).execute()
        if ud.data:
            title = ud.data[0].get('title') or ''
    except Exception:
        pass
    logger.info("\n対象:")
    logger.info(f"  ID:        {doc.get('id')}")
    logger.info(f"  タイトル:  {title}")
    logger.info(f"  ステータス: {doc.get('processing_status') or ''}")
    logger.info(f"  Source:    {doc.get('source') or ''} / {doc.get('person') or ''}")
    md = doc.get('metadata') or {}
    if isinstance(md, dict) and md.get('process_pdf_page_index') is not None:
        logger.info(
            f"  PDFページ指定: metadata.process_pdf_page_index = {md.get('process_pdf_page_index')} "
            f"（0始まり・この1ページのみでパイプライン実行）"
        )
    logger.info("\n" + "-" * 80)
    logger.info("実行するには --execute を付与:")
    logger.info(f"  python {sys.argv[0]} --doc-id {doc_id} --execute")
    logger.info("=" * 80 + "\n")


async def process_single_document(processor: PipelineManager, doc_id: str):
    """単一 pipeline_meta 行を処理"""
    logger.info("=" * 80)
    logger.info(f"単一ジョブ処理: {doc_id}")
    logger.info("=" * 80)

    try:
        result = await processor.process_single_document(doc_id)
        if result:
            logger.info(f"[OK] 処理完了: {doc_id}")
        else:
            logger.error(f"[FAILED] 処理失敗: {doc_id}")
        return result
    except Exception as e:
        logger.error(f"[ERROR] 処理エラー: {e}")
        return False


async def process_run_request(processor: PipelineManager, run_request_id: str):
    """
    RUN 要求を処理（Web UI からの要求）

    【設計原則】
    - ops_requests は更新しない（要求SSOT）
    - run_executions にのみ書き込む（Evidence）
    - payload に doc_id（pipeline_meta.id）が必須（キュー一括は廃止）
    """
    import socket
    import os
    from datetime import datetime

    db = DatabaseClient(use_service_role=True)
    logger.info("=" * 80)
    logger.info(f"Run Request 処理: {run_request_id}")
    logger.info("=" * 80)

    try:
        result = db.client.table('ops_requests').select('*').eq(
            'id', run_request_id
        ).eq('request_type', 'RUN').execute()

        if not result.data:
            logger.error(f"RUN 要求が見つかりません: {run_request_id}")
            return False

        request = result.data[0]
        payload = request.get('payload', {}) or {}
        logger.info(f"  payload: {payload}")

    except Exception as e:
        logger.error(f"ops_requests 取得エラー: {e}")
        return False

    worker_id = f"{socket.gethostname()}:{os.getpid()}:{datetime.now().strftime('%Y%m%d%H%M%S')}"
    execution_id = None

    try:
        existing_result = db.client.table('run_executions').select('id, worker_id').eq(
            'ops_request_id', run_request_id
        ).eq('status', 'processing').order('started_at', desc=True).limit(1).execute()

        if existing_result.data:
            execution_id = existing_result.data[0]['id']
            logger.info(f"  既存 execution_id を使用: {execution_id}")
            logger.info(f"  作成元 worker_id: {existing_result.data[0].get('worker_id')}")
        else:
            insert_result = db.client.table('run_executions').insert({
                'ops_request_id': run_request_id,
                'status': 'processing',
                'worker_id': worker_id,
                'hostname': socket.gethostname(),
                'pid': os.getpid(),
                'executed_params': payload,
                'processed_doc_ids': []
            }).execute()

            if insert_result.data:
                execution_id = insert_result.data[0]['id']
                logger.info(f"  新規 execution_id: {execution_id}")

    except Exception as e:
        logger.error(f"run_executions 取得/INSERT エラー: {e}")
        return False

    doc_id = payload.get('doc_id')
    if not doc_id:
        err = 'payload に doc_id（pipeline_meta.id）が必要です（キュー一括実行は廃止）'
        logger.error(err)
        try:
            db.client.table('run_executions').update({
                'status': 'failed',
                'processed_count': 0,
                'failed_count': 1,
                'skipped_count': 0,
                'error_message': err,
                'processed_doc_ids': []
            }).eq('id', execution_id).execute()
        except Exception as ex:
            logger.error(f"run_executions UPDATE エラー: {ex}")
        return False

    logger.info(f"  doc_id: {doc_id}")

    processed_count = 0
    failed_count = 0
    skipped_count = 0
    processed_doc_ids = []
    error_message = None
    final_status = 'completed'

    try:
        success = await processor.process_single_document(doc_id)
        if success:
            processed_count = 1
            processed_doc_ids.append(doc_id)
        else:
            failed_count = 1
    except Exception as e:
        error_message = str(e)
        final_status = 'failed'
        logger.error(f"処理エラー: {e}")

    if failed_count > 0 and processed_count == 0:
        final_status = 'failed'
    elif failed_count > 0:
        final_status = 'completed'
        error_message = f"{failed_count}件の処理が失敗しました"

    try:
        db.client.table('run_executions').update({
            'status': final_status,
            'processed_count': processed_count,
            'failed_count': failed_count,
            'skipped_count': skipped_count,
            'error_message': error_message,
            'processed_doc_ids': processed_doc_ids
        }).eq('id', execution_id).execute()

    except Exception as e:
        logger.error(f"run_executions UPDATE エラー: {e}")

    logger.info("=" * 80)
    logger.info("Run Request 完了")
    logger.info(f"  status: {final_status}")
    logger.info(f"  processed: {processed_count}")
    logger.info(f"  failed: {failed_count}")
    logger.info(f"  skipped: {skipped_count}")
    logger.info("=" * 80)

    return final_status == 'completed'


def setup_file_logging(run_request_id: str = None) -> Path:
    """ログファイル出力を設定"""
    from datetime import datetime

    log_dir = _root_dir / 'logs' / 'processing'
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if run_request_id:
        log_filename = f"run_{run_request_id[:8]}_{timestamp}.log"
    else:
        log_filename = f"process_{timestamp}.log"

    log_path = log_dir / log_filename

    logger.add(
        log_path,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level="DEBUG",
        encoding="utf-8",
        colorize=False,
    )

    logger.info(f"ログファイル: {log_path}")
    return log_path


async def main():
    parser = argparse.ArgumentParser(
        description='ドキュメント処理 CLI Worker（単一 pipeline_meta.id のみ）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python process_queued_documents.py --doc-id <uuid>
  python process_queued_documents.py --doc-id <uuid> --execute
  python process_queued_documents.py --run-request <uuid> --execute
        """
    )

    parser.add_argument('--doc-id', dest='doc_id', help='pipeline_meta.id（必須・単一処理）')
    parser.add_argument(
        '--pdf-page-index',
        dest='pdf_page_index',
        type=int,
        default=None,
        metavar='N',
        help=(
            '--execute 時のみ: pipeline_meta.metadata.process_pdf_page_index を N に更新してから実行 '
            '（0始まり・元PDFのその1ページだけを切り出して A→F に渡す）'
        ),
    )
    parser.add_argument('--execute', action='store_true', help='実際に処理を実行（未指定時は dry-run）')
    parser.add_argument('--run-request', dest='run_request_id', help='ops_requests の RUN 要求ID')

    args = parser.parse_args()

    if args.pdf_page_index is not None and args.run_request_id:
        logger.error('--pdf-page-index は --run-request とは併用できません（--doc-id ジョブ専用）')
        sys.exit(1)

    master_log_path = setup_master_logging(log_dir=_root_dir / 'logs')
    log_path = setup_file_logging(args.run_request_id)
    logger.info(f"マスターログ: {master_log_path}")
    logger.info("Per-Task Logging有効: 各タスクのログは logs/tasks/ に出力されます")

    logger.info("Worker起動: service_role で Supabase に接続します")
    processor = PipelineManager(use_service_role=True)

    if args.run_request_id:
        if not args.execute:
            logger.warning("--run-request は --execute が必要です")
            sys.exit(1)
        ok = await process_run_request(processor, args.run_request_id)
        sys.exit(0 if ok else 1)

    if not args.doc_id:
        logger.error("--doc-id <pipeline_meta.id> を指定するか、--run-request を使用してください。")
        sys.exit(1)

    if not args.execute:
        print_dry_run_doc(args.doc_id)
        return

    if args.pdf_page_index is not None:
        db = DatabaseClient(use_service_role=True)
        try:
            pm = db.client.table('pipeline_meta').select('metadata').eq(
                'id', args.doc_id
            ).single().execute()
            prev = (pm.data or {}).get('metadata') if pm.data else None
            md: dict = dict(prev) if isinstance(prev, dict) else {}
            md['process_pdf_page_index'] = int(args.pdf_page_index)
            db.client.table('pipeline_meta').update({'metadata': md}).eq(
                'id', args.doc_id
            ).execute()
            logger.info(
                f"[PM-CLI] pipeline_meta.metadata.process_pdf_page_index = {args.pdf_page_index} を保存しました"
            )
        except Exception as e:
            logger.error(f"metadata 更新失敗: {e}")
            sys.exit(1)

    ok = await process_single_document(processor, args.doc_id)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    asyncio.run(main())
