"""
InBox自動監視スクリプト (v1.0)

目的: Google Driveの特定のInBoxフォルダをポーリングし、
     新規追加されたPDFファイルを検出、既存の2段階AIパイプラインに渡す。

設計: AUTO_INBOX_COMPLETE_v3.0.md の Phase 2 (Track 3) に準拠
     「受信箱自動監視システム」のアーキテクチャ定義に基づく

実行頻度: GitHub Actions (毎時実行)
"""

import os
import sys
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from loguru import logger
import traceback

# パス設定
sys.path.insert(0, str(Path(__file__).parent.parent))

from dms.common.connectors.google_drive import GoogleDriveConnector
from dms.common.database.client import DatabaseClient
from dms.common.processors.pdf import calculate_content_hash
from dms.pipeline import UnifiedDocumentPipeline

# ログ設定
log_dir = Path('logs')
log_dir.mkdir(exist_ok=True)

logger.add(log_dir / f'inbox_monitor_{datetime.now():%Y%m%d_%H%M%S}.log', rotation="10 MB", level="INFO")
logger.add(sys.stdout, level="INFO")


class InBoxMonitor:
    """InBox自動監視クラス"""

    def __init__(self):
        """初期化"""
        self.drive = GoogleDriveConnector()
        self.db = DatabaseClient()

        # 統合パイプラインを初期化
        self.pipeline = UnifiedDocumentPipeline(db_client=self.db)

        # 一時ディレクトリ
        self.temp_dir = Path("./temp")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # InBoxフォルダIDとArchiveフォルダIDを取得
        self.inbox_folder_id = self.drive.get_inbox_folder_id()
        self.archive_folder_id = self.drive.get_archive_folder_id()

        if not self.inbox_folder_id:
            raise ValueError("INBOX_FOLDER_ID が環境変数に設定されていません")

        logger.info(f"InBox監視システム初期化完了")
        logger.info(f"InBox Folder ID: {self.inbox_folder_id}")
        logger.info(f"Archive Folder ID: {self.archive_folder_id if self.archive_folder_id else 'Not Set'}")

    def get_processed_file_ids(self) -> List[str]:
        """
        既に処理済みのファイルIDをデータベースから取得

        Returns:
            処理済みファイルIDのリスト
        """
        logger.info("📊 データベースから処理済みファイルIDを取得中...")
        processed_ids = self.db.get_processed_file_ids()
        logger.info(f"✅ {len(processed_ids)} 件の処理済みファイルIDを取得")
        return processed_ids

    def scan_inbox_for_new_files(self, processed_file_ids: List[str]) -> List[Dict[str, Any]]:
        """
        InBoxフォルダから新規ファイルをスキャン

        Args:
            processed_file_ids: 既に処理済みのファイルIDリスト

        Returns:
            新規ファイルのメタデータリスト
        """
        logger.info(f"📁 InBoxフォルダ [{self.inbox_folder_id}] をスキャン中...")

        new_files = self.drive.list_inbox_files(
            folder_id=self.inbox_folder_id,
            processed_file_ids=processed_file_ids
        )

        if new_files:
            logger.info(f"🆕 {len(new_files)} 件の新規ファイルを検出:")
            for file in new_files:
                logger.info(f"  - {file['name']} (ID: {file['id'][:8]}...)")
        else:
            logger.info("新規ファイルは見つかりませんでした")

        return new_files

    def check_duplicate_by_hash(self, file_meta: Dict[str, Any]) -> Optional[str]:
        """
        ファイルのcontent_hashを計算し、重複をチェック

        Args:
            file_meta: ファイルメタデータ

        Returns:
            content_hash: 重複していない場合はハッシュ値を返す
            None: 重複している場合はNoneを返す
        """
        file_id = file_meta['id']
        file_name = file_meta['name']

        try:
            # 一時ディレクトリにファイルをダウンロード
            temp_dir = tempfile.gettempdir()
            logger.info(f"🔍 重複チェック: {file_name} をダウンロード中...")

            file_path = self.drive.download_file(file_id, file_name, temp_dir)

            # content_hashを計算
            content_hash = calculate_content_hash(file_path)
            logger.info(f"   計算されたハッシュ: {content_hash[:16]}...")

            # 重複チェック
            is_duplicate = self.db.check_duplicate_hash(content_hash)

            # 一時ファイルを削除
            try:
                Path(file_path).unlink()
            except Exception:
                pass

            if is_duplicate:
                logger.warning(f"⚠️  重複検知: {file_name} は既に処理済みです（AI処理スキップ）")
                return None

            logger.info(f"✅ 重複なし: {file_name} は新規ファイルです")
            return content_hash

        except Exception as e:
            logger.error(f"❌ 重複チェックエラー: {file_name} - {e}")
            logger.error(traceback.format_exc())
            # エラー時は処理を続行（安全側に倒す）
            return "error_skip_hash_check"

    async def process_file(self, file_meta: Dict[str, Any]) -> bool:
        """
        新規ファイルを Stage E-K パイプラインで処理

        Args:
            file_meta: ファイルメタデータ

        Returns:
            処理が成功した場合True
        """
        file_name = file_meta['name']
        file_id = file_meta['id']
        mime_type = file_meta.get('mimeType', 'application/octet-stream')

        logger.info(f"⚙️  ファイル処理開始: {file_name}")

        local_path = None
        try:
            # ファイルをダウンロード
            local_path = self.drive.download_file(file_id, file_name, self.temp_dir)
            logger.info(f"ダウンロード完了: {local_path}")

            # Stage E-K で処理（inbox workspace）
            # doc_type は'other'として扱う（inbox からの自動取り込み）
            result = await self.pipeline.process_document(
                file_path=Path(local_path),
                file_name=file_name,
                doc_type='other',
                workspace='inbox',
                mime_type=mime_type,
                source_id=file_id
            )

            if result and result.get('success'):
                logger.info(f"✅ ファイル処理成功: {file_name}")
                return True
            else:
                error_msg = result.get('error', 'unknown error') if result else 'no result'
                logger.error(f"❌ ファイル処理失敗: {error_msg}")
                return False

        except Exception as e:
            logger.error(f"❌ ファイル処理中に致命的なエラーが発生: {file_name}")
            logger.error(traceback.format_exc())
            return False

        finally:
            # 一時ファイルを削除
            if local_path and Path(local_path).exists():
                Path(local_path).unlink()
                logger.debug(f"一時ファイル削除: {local_path}")

    def move_to_archive(self, file_id: str, file_name: str) -> bool:
        """
        処理済みファイルをArchiveフォルダに移動

        Args:
            file_id: ファイルID
            file_name: ファイル名

        Returns:
            移動が成功した場合True
        """
        if not self.archive_folder_id:
            logger.warning(f"⚠️  ARCHIVE_FOLDER_ID が設定されていないため、{file_name} の移動をスキップします")
            return False

        logger.info(f"📦 ファイルをArchiveに移動中: {file_name}")

        success = self.drive.move_file(file_id, self.archive_folder_id)

        if success:
            logger.info(f"✅ ファイル移動成功: {file_name} -> Archive")
        else:
            logger.error(f"❌ ファイル移動失敗: {file_name}")

        return success

    async def run_monitoring_cycle(self):
        """監視サイクルのメイン実行"""
        logger.info("=" * 70)
        logger.info("🔍 InBox自動監視システム 開始")
        logger.info(f"実行時刻: {datetime.now():%Y-%m-%d %H:%M:%S}")
        logger.info("=" * 70)

        stats = {
            'new_files_detected': 0,
            'duplicates_skipped': 0,
            'processed_success': 0,
            'processed_failed': 0,
            'archived_success': 0,
            'archived_failed': 0
        }

        try:
            # Step 1: 処理済みファイルIDを取得
            processed_file_ids = self.get_processed_file_ids()

            # Step 2: InBoxから新規ファイルを検出
            new_files = self.scan_inbox_for_new_files(processed_file_ids)
            stats['new_files_detected'] = len(new_files)

            # Step 3: 各ファイルを処理
            for file_meta in new_files:
                file_id = file_meta['id']
                file_name = file_meta['name']

                # Step 3-1: 重複チェック（content_hash）
                content_hash = self.check_duplicate_by_hash(file_meta)

                if content_hash is None:
                    # 重複ファイル：AI処理をスキップ
                    stats['duplicates_skipped'] += 1
                    logger.info(f"💰 コスト削減: {file_name} のAI処理をスキップしました")

                    # 重複ファイルもArchiveに移動
                    if self.archive_folder_id:
                        archive_success = self.move_to_archive(file_id, file_name)
                        if archive_success:
                            stats['archived_success'] += 1
                        else:
                            stats['archived_failed'] += 1
                    continue

                # Step 3-2: ファイルを処理（重複なしの場合）
                success = await self.process_file(file_meta)

                if success:
                    stats['processed_success'] += 1

                    # 処理成功後、Archiveに移動
                    if self.archive_folder_id:
                        archive_success = self.move_to_archive(file_id, file_name)
                        if archive_success:
                            stats['archived_success'] += 1
                        else:
                            stats['archived_failed'] += 1
                else:
                    stats['processed_failed'] += 1
                    logger.warning(f"⚠️  処理失敗のため、{file_name} はInBoxに残します")

        except Exception as e:
            logger.error(f"❌ 監視サイクル実行中に致命的なエラーが発生: {e}")
            logger.error(traceback.format_exc())

        # サマリー表示
        logger.info("=" * 70)
        logger.info("📊 InBox自動監視システム 完了サマリー")
        logger.info(f"新規ファイル検出数: {stats['new_files_detected']}")
        logger.info(f"重複によりスキップ: {stats['duplicates_skipped']} 件 💰")
        logger.info(f"AI処理成功数: {stats['processed_success']}")
        logger.info(f"AI処理失敗数: {stats['processed_failed']}")
        logger.info(f"アーカイブ成功数: {stats['archived_success']}")
        logger.info(f"アーカイブ失敗数: {stats['archived_failed']}")
        logger.info("=" * 70)

        return stats


async def main():
    """メイン関数"""
    try:
        monitor = InBoxMonitor()
        stats = await monitor.run_monitoring_cycle()

        # 終了コード決定
        if stats['new_files_detected'] == 0:
            logger.info("✨ 新規ファイルがありませんでした（正常終了）")
            sys.exit(0)

        if stats['processed_failed'] == 0:
            logger.info("✅ すべてのファイルが正常に処理されました")
            sys.exit(0)

        failure_rate = stats['processed_failed'] / stats['new_files_detected']

        if failure_rate >= 0.5:
            logger.error(f"❌ 失敗率が高すぎます ({failure_rate:.1%})。システムレベルの問題の可能性があります。")
            sys.exit(1)
        else:
            logger.warning(f"⚠️  {stats['processed_failed']}件のファイル処理が失敗しました。")
            sys.exit(2)

    except Exception as e:
        logger.error(f"❌ プログラム実行中に致命的なエラーが発生: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
