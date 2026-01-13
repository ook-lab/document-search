"""
CLI Document Processor

shared.processing モジュールを使用したバッチ処理スクリプト
ロジックは全て shared/processing/ に委譲

使い方:
    # 全ワークスペースを処理
    python process_queued_documents.py --limit=100

    # 特定のワークスペースのみ
    python process_queued_documents.py --workspace=ema_classroom --limit=20

    # 統計情報のみ表示
    python process_queued_documents.py --stats

    # 継続処理ループ
    python process_queued_documents.py --loop
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
from shared.processing import DocumentProcessor, continuous_processing_loop


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


async def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(description='ドキュメント処理スクリプト')
    parser.add_argument('--workspace', default='all', help='対象ワークスペース (デフォルト: all)')
    parser.add_argument('--limit', type=int, default=100, help='処理する最大件数 (デフォルト: 100)')
    parser.add_argument('--no-preserve-workspace', action='store_true', help='workspaceを保持しない')
    parser.add_argument('--stats', action='store_true', help='統計情報のみを表示')

    args = parser.parse_args()

    processor = DocumentProcessor()

    # 統計情報のみ表示
    if args.stats:
        print_stats(processor, args.workspace)
        return

    # 通常の処理
    logger.info("="*80)
    logger.info("ドキュメント処理スクリプト")
    logger.info("="*80)

    await processor.run_batch(
        workspace=args.workspace,
        limit=args.limit,
        preserve_workspace=not args.no_preserve_workspace
    )


if __name__ == '__main__':
    # --loop フラグがある場合は継続ループモード
    if '--loop' in sys.argv:
        logger.info("継続処理ループモードで起動します")
        asyncio.run(continuous_processing_loop())
    else:
        # 通常モード（1回実行）
        asyncio.run(main())
