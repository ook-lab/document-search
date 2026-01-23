"""
CLI Document Processor - Worker の唯一の実行入口

【設計原則】
- 処理実行は CLI からのみ（Web UI は投入のみ）
- --execute フラグが無い場合は dry-run（何を処理するか表示のみ）
- 3環境（Cloud Run / localhost / ターミナル）で挙動差を出さない
- バッチ1回実行（常駐禁止）- Cloud Run Jobs / ローカル両用

【使い方】
    # dry-run: 処理対象を確認（実行しない）
    python process_queued_documents.py --limit=10

    # 単一ドキュメント処理
    python process_queued_documents.py --doc-id <uuid> --execute

    # バッチ処理（100件）
    python process_queued_documents.py --limit=100 --execute

    # 1件だけ処理
    python process_queued_documents.py --once --execute

    # 特定のワークスペースのみ
    python process_queued_documents.py --workspace=ema_classroom --limit=20 --execute

    # 統計情報のみ表示
    python process_queued_documents.py --stats

    # Run Request 実行（Web UI からの要求を処理）
    python process_queued_documents.py --run-request <uuid> --execute
"""
import sys
import asyncio
import argparse
from pathlib import Path

# プロジェクトルートへのパスを追加（スクリプト実行時用）
_root_dir = Path(__file__).resolve().parent.parent.parent
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))

from loguru import logger

# shared モジュールからインポート
from shared.processing import DocumentProcessor
from shared.common.database.client import DatabaseClient
from shared.logging import setup_master_logging


def print_stats(processor: DocumentProcessor, workspace: str):
    """統計情報を表示"""
    stats = processor.get_queue_stats(workspace)

    if not stats:
        logger.info("統計情報の取得に失敗しました")
        return

    logger.info("\n" + "="*80)
    if workspace == 'all':
        logger.info("全体統計")
    else:
        logger.info(f"統計 (workspace: {workspace})")
    logger.info("="*80)
    logger.info(f"待機中 (pending):      {stats.get('pending', 0):>5}件")
    logger.info(f"処理中 (processing):   {stats.get('processing', 0):>5}件")
    logger.info(f"完了   (completed):    {stats.get('completed', 0):>5}件")
    logger.info(f"失敗   (failed):       {stats.get('failed', 0):>5}件")
    logger.info(f"未処理 (null):         {stats.get('null', 0):>5}件")
    logger.info("-" * 80)
    logger.info(f"合計:                  {stats.get('total', 0):>5}件")
    logger.info(f"成功率:                {stats.get('success_rate', 0):>5.1f}%")
    logger.info("="*80 + "\n")


def print_dry_run_targets(processor: DocumentProcessor, workspace: str, limit: int, doc_id: str = None):
    """dry-run: 処理対象を表示（実行しない）"""
    logger.info("\n" + "="*80)
    logger.info("【DRY-RUN MODE】処理対象の確認（実行されません）")
    logger.info("="*80)

    if doc_id:
        # 単一ドキュメント
        from shared.common.database.client import DatabaseClient
        db = DatabaseClient(use_service_role=True)  # RLSバイパスのためService Role使用
        result = db.client.table('Rawdata_FILE_AND_MAIL')\
            .select('id, file_name, title, processing_status, workspace')\
            .eq('id', doc_id)\
            .execute()

        if not result.data:
            logger.warning(f"ドキュメントが見つかりません: {doc_id}")
            return

        doc = result.data[0]
        logger.info(f"\n対象ドキュメント:")
        logger.info(f"  ID:        {doc.get('id')}")
        logger.info(f"  タイトル:  {doc.get('title', doc.get('file_name', '(不明)'))}")
        logger.info(f"  ステータス: {doc.get('processing_status', '(不明)')}")
        logger.info(f"  Workspace: {doc.get('workspace', '(不明)')}")
    else:
        # バッチ
        docs = processor.get_pending_documents(workspace, limit)

        if not docs:
            logger.info("\n処理対象のドキュメントがありません")
            return

        logger.info(f"\n処理対象: {len(docs)}件")
        logger.info("-" * 80)

        for i, doc in enumerate(docs[:20]):
            title = doc.get('title', doc.get('file_name', '(不明)'))
            ws = doc.get('workspace', '不明')
            logger.info(f"  {i+1:>3}. [{ws}] {title}")

        if len(docs) > 20:
            logger.info(f"  ... 他 {len(docs) - 20}件")

    logger.info("\n" + "-"*80)
    logger.info("実行するには --execute フラグを追加してください:")
    if doc_id:
        logger.info(f"  python {sys.argv[0]} --doc-id {doc_id} --execute")
    else:
        logger.info(f"  python {sys.argv[0]} --workspace={workspace} --limit={limit} --execute")
    logger.info("="*80 + "\n")


async def process_single_document(processor: DocumentProcessor, doc_id: str):
    """単一ドキュメントを処理"""
    logger.info("="*80)
    logger.info(f"単一ドキュメント処理: {doc_id}")
    logger.info("="*80)

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


async def process_run_request(processor: DocumentProcessor, run_request_id: str):
    """
    RUN 要求を処理（Web UI からの要求）

    【設計原則】
    - ops_requests は更新しない（要求SSOT）
    - run_executions にのみ書き込む（Evidence）
    - 同一 run_request_id に対して複数回実行可能（リトライ対応）
    """
    import socket
    import os
    from datetime import datetime

    db = DatabaseClient(use_service_role=True)  # RLSバイパスのためService Role使用
    logger.info("="*80)
    logger.info(f"Run Request 処理: {run_request_id}")
    logger.info("="*80)

    # 1. ops_requests から RUN 要求を取得
    try:
        result = db.client.table('ops_requests')\
            .select('*')\
            .eq('id', run_request_id)\
            .eq('request_type', 'RUN')\
            .execute()

        if not result.data:
            logger.error(f"RUN 要求が見つかりません: {run_request_id}")
            return False

        request = result.data[0]
        payload = request.get('payload', {}) or {}
        logger.info(f"  payload: {payload}")

    except Exception as e:
        logger.error(f"ops_requests 取得エラー: {e}")
        return False

    # 2. run_executions のレコードを取得または作成
    # Runner API 経由の場合は既に processing レコードが存在する
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{datetime.now().strftime('%Y%m%d%H%M%S')}"
    execution_id = None

    try:
        # まず既存の processing レコードがあるか確認
        existing_result = db.client.table('run_executions')\
            .select('id, worker_id')\
            .eq('ops_request_id', run_request_id)\
            .eq('status', 'processing')\
            .order('started_at', desc=True)\
            .limit(1)\
            .execute()

        if existing_result.data:
            # Runner API 経由で既に作成済み
            execution_id = existing_result.data[0]['id']
            logger.info(f"  既存 execution_id を使用: {execution_id}")
            logger.info(f"  作成元 worker_id: {existing_result.data[0].get('worker_id')}")
        else:
            # CLI 直接実行の場合は新規作成
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

    # 3. payload からパラメータを取得
    max_items = payload.get('max_items', 5)
    workspace = payload.get('workspace', 'all')
    doc_id = payload.get('doc_id')

    logger.info(f"  max_items: {max_items}")
    logger.info(f"  workspace: {workspace}")
    if doc_id:
        logger.info(f"  doc_id: {doc_id}")

    # 4. 処理実行
    processed_count = 0
    failed_count = 0
    skipped_count = 0
    processed_doc_ids = []
    error_message = None
    final_status = 'completed'

    try:
        if doc_id:
            # 単一ドキュメント処理
            success = await processor.process_single_document(doc_id)
            if success:
                processed_count = 1
                processed_doc_ids.append(doc_id)
            else:
                failed_count = 1
        else:
            # バッチ処理
            docs = processor.get_pending_documents(workspace, max_items)

            if not docs:
                logger.info("処理対象のドキュメントがありません")
                skipped_count = 0
            else:
                logger.info(f"処理対象: {len(docs)}件")

                for doc in docs:
                    doc_id_current = doc['id']
                    title = doc.get('title', doc.get('file_name', '(不明)'))

                    try:
                        success = await processor.process_single_document(doc_id_current)
                        if success:
                            processed_count += 1
                            processed_doc_ids.append(doc_id_current)
                            logger.info(f"[OK] {title}")
                        else:
                            failed_count += 1
                            logger.error(f"[FAILED] {title}")
                    except Exception as e:
                        failed_count += 1
                        logger.error(f"[ERROR] {title}: {e}")

    except Exception as e:
        error_message = str(e)
        final_status = 'failed'
        logger.error(f"処理エラー: {e}")

    # 5. run_executions を更新（完了状態）
    if failed_count > 0 and processed_count == 0:
        final_status = 'failed'
    elif failed_count > 0:
        # 部分成功
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

    # 6. 結果サマリー
    logger.info("="*80)
    logger.info("Run Request 完了")
    logger.info(f"  status: {final_status}")
    logger.info(f"  processed: {processed_count}")
    logger.info(f"  failed: {failed_count}")
    logger.info(f"  skipped: {skipped_count}")
    logger.info("="*80)

    return final_status == 'completed'


def setup_file_logging(run_request_id: str = None) -> Path:
    """ログファイル出力を設定

    Args:
        run_request_id: Run Request ID（指定時はファイル名に含める）

    Returns:
        ログファイルのパス
    """
    from datetime import datetime
    import os

    # ログディレクトリ作成
    log_dir = _root_dir / 'logs' / 'processing'
    log_dir.mkdir(parents=True, exist_ok=True)

    # ファイル名生成
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if run_request_id:
        log_filename = f"run_{run_request_id[:8]}_{timestamp}.log"
    else:
        log_filename = f"process_{timestamp}.log"

    log_path = log_dir / log_filename

    # loguru にファイル出力を追加
    logger.add(
        log_path,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level="DEBUG",
        encoding="utf-8"
    )

    logger.info(f"ログファイル: {log_path}")
    return log_path


async def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description='ドキュメント処理 CLI Worker',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # dry-run（処理対象を確認）
  python process_queued_documents.py --limit=10

  # 単一ドキュメント処理
  python process_queued_documents.py --doc-id <uuid> --execute

  # バッチ処理
  python process_queued_documents.py --limit=100 --execute

  # 1件だけ処理
  python process_queued_documents.py --once --execute

  # 統計情報
  python process_queued_documents.py --stats

  # Run Request 実行（Web UI からの要求）
  python process_queued_documents.py --run-request <uuid> --execute
        """
    )

    # 対象指定
    parser.add_argument('--workspace', default='all',
                        help='対象ワークスペース (デフォルト: all)')
    parser.add_argument('--limit', type=int, default=100,
                        help='処理する最大件数 (デフォルト: 100)')
    parser.add_argument('--doc-id', dest='doc_id',
                        help='特定のドキュメントIDを処理')
    parser.add_argument('--once', action='store_true',
                        help='1件だけ処理して終了 (--limit 1 のエイリアス)')

    # 実行制御
    parser.add_argument('--execute', action='store_true',
                        help='実際に処理を実行（未指定時は dry-run）')
    parser.add_argument('--no-preserve-workspace', action='store_true',
                        help='workspaceを保持しない')

    # 情報表示
    parser.add_argument('--stats', action='store_true',
                        help='統計情報のみを表示')

    # Run Request モード（Web UI からの要求を処理）
    parser.add_argument('--run-request', dest='run_request_id',
                        help='ops_requests の RUN 要求IDを指定して処理')

    args = parser.parse_args()

    # ログファイル出力設定（--stats 以外の場合）
    if not args.stats:
        log_path = setup_file_logging(args.run_request_id)
        # Per-Task Logging: マスターログを設定（システム全体のイベント用）
        master_log_path = setup_master_logging(log_dir=_root_dir / 'logs')
        logger.info(f"マスターログ: {master_log_path}")
        logger.info("Per-Task Logging有効: 各タスクのログは logs/tasks/ に出力されます")

    # Worker実行時は service_role を使用（RLSバイパス）
    # この時点で SUPABASE_SERVICE_ROLE_KEY が無ければ例外で停止（fail-fast）
    logger.info("Worker起動: service_role で Supabase に接続します")
    processor = DocumentProcessor(use_service_role=True)

    # --once は --limit 1 のエイリアス
    if args.once:
        args.limit = 1

    # 統計情報のみ表示
    if args.stats:
        print_stats(processor, args.workspace)
        return

    # Run Request モード（Web UI からの要求を処理）
    if args.run_request_id:
        if not args.execute:
            logger.warning("="*80)
            logger.warning("【DRY-RUN】--run-request モードは --execute が必要です")
            logger.warning(f"  実行するには: python process_queued_documents.py --run-request {args.run_request_id} --execute")
            logger.warning("="*80)
            return
        await process_run_request(processor, args.run_request_id)
        return

    # dry-run モード（--execute なし）
    if not args.execute:
        print_dry_run_targets(processor, args.workspace, args.limit, args.doc_id)
        return

    # ========== 実行モード ==========
    logger.info("="*80)
    logger.info("ドキュメント処理 Worker 起動")
    logger.info("="*80)

    # 単一ドキュメント処理
    if args.doc_id:
        await process_single_document(processor, args.doc_id)
        return

    # バッチ処理
    await processor.run_batch(
        workspace=args.workspace,
        limit=args.limit,
        preserve_workspace=not args.no_preserve_workspace
    )


if __name__ == '__main__':
    asyncio.run(main())
