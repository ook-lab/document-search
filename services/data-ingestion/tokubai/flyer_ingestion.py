"""
トクバイチラシ取得パイプライン

トクバイのウェブサイトからチラシ画像を取得し、Google DriveとSupabaseに登録する。

処理フロー:
1. トクバイの店舗ページからチラシ一覧を取得
2. Supabaseで既存データをチェックして新着チラシを抽出
3. チラシ画像をダウンロードしてGoogle Driveに保存
4. Supabaseに基本情報を登録（processing_status='pending'）
5. 別途 process_queued_documents.py で処理（画像抽出、Stage A/B/C）
"""
import os
import sys
import hashlib
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from loguru import logger

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))

# .envファイルを読み込む
from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

from shared.common.connectors.google_drive import GoogleDriveConnector
from shared.common.database.client import DatabaseClient
from B_ingestion.tokubai.tokubai_scraper import TokubaiScraper


class TokubaiFlyerIngestionPipeline:
    """トクバイチラシ取得パイプライン"""

    def __init__(
        self,
        store_url: Optional[str] = None,
        flyer_folder_id: Optional[str] = None,
        store_name: Optional[str] = None
    ):
        """
        Args:
            store_url: トクバイの店舗URL（Noneの場合は環境変数から取得）
            flyer_folder_id: チラシ保存先のDriveフォルダID（Noneの場合は環境変数から取得）
            store_name: 店舗名（Noneの場合は環境変数から取得）
        """
        self.store_url = store_url or os.getenv("TOKUBAI_STORE_URL")
        self.flyer_folder_id = flyer_folder_id or os.getenv("TOKUBAI_FLYER_FOLDER_ID")
        self.store_name = store_name or os.getenv("TOKUBAI_STORE_NAME", "トクバイ")

        if not self.store_url:
            raise ValueError("店舗URLが指定されていません。環境変数 TOKUBAI_STORE_URL を設定してください。")

        if not self.flyer_folder_id:
            raise ValueError("フォルダIDが指定されていません。環境変数 TOKUBAI_FLYER_FOLDER_ID を設定してください。")

        # コネクタの初期化
        self.scraper = TokubaiScraper(self.store_url)
        self.drive = GoogleDriveConnector()
        self.db = DatabaseClient()

        logger.info(f"TokubaiFlyerIngestionPipeline初期化完了")
        logger.info(f"  - Store name: {self.store_name}")
        logger.info(f"  - Store URL: {self.store_url}")
        logger.info(f"  - Flyer folder: {self.flyer_folder_id}")

    async def check_existing_flyers(self, flyer_ids: List[str]) -> set:
        """
        Supabaseで既存のチラシIDをチェック

        Args:
            flyer_ids: チェックするチラシIDのリスト

        Returns:
            既に存在するチラシIDのセット
        """
        try:
            # Rawdata_FLYER_shops テーブルで既存のチラシIDを取得
            result = self.db.client.table('Rawdata_FLYER_shops').select('flyer_id').in_(
                'flyer_id', flyer_ids
            ).execute()

            # flyer_id を抽出
            existing_ids = set()
            if result.data:
                for doc in result.data:
                    flyer_id = doc.get('flyer_id')
                    if flyer_id:
                        existing_ids.add(flyer_id)

            logger.info(f"既存のチラシ: {len(existing_ids)}件")
            return existing_ids

        except Exception as e:
            logger.error(f"Supabase検索エラー: {e}")
            return set()

    def save_image_to_drive(
        self,
        image_data: bytes,
        flyer_id: str,
        page_num: int,
        flyer_title: str
    ) -> Optional[tuple]:
        """
        チラシ画像をGoogle Driveに保存

        Args:
            image_data: 画像のバイトデータ
            flyer_id: チラシID
            page_num: ページ番号
            flyer_title: チラシのタイトル

        Returns:
            (DriveのファイルID, ファイル名)のタプル、失敗時はNone
        """
        # 安全なファイル名を生成
        safe_title = "".join(c for c in flyer_title if c.isalnum() or c in (' ', '-', '_', '　')).strip()
        if not safe_title:
            safe_title = "tokubai_flyer"

        # ファイル名生成
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{timestamp}_{safe_title}_{flyer_id}_p{page_num}.webp"

        # 画像形式を判定（簡易版）
        # 実際のContent-Typeやファイルヘッダーから判定する方が正確
        if image_data.startswith(b'\xff\xd8\xff'):
            file_name = file_name.replace('.webp', '.jpg')
            mime_type = 'image/jpeg'
        elif image_data.startswith(b'\x89PNG'):
            file_name = file_name.replace('.webp', '.png')
            mime_type = 'image/png'
        elif image_data.startswith(b'RIFF') and b'WEBP' in image_data[:20]:
            mime_type = 'image/webp'
        else:
            # デフォルトはwebp
            mime_type = 'image/webp'

        # Driveにアップロード
        file_id = self.drive.upload_file(
            file_content=image_data,
            file_name=file_name,
            mime_type=mime_type,
            folder_id=self.flyer_folder_id
        )

        if file_id:
            logger.info(f"チラシ画像をDriveに保存: {file_name}")
        else:
            logger.error(f"チラシ画像の保存に失敗: {file_name}")

        return (file_id, file_name) if file_id else None

    async def process_single_flyer(
        self,
        flyer_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        1件のチラシを処理（画像ダウンロード→Drive保存→Supabase登録）

        Args:
            flyer_info: チラシ情報
                {'title': 'タイトル', 'url': '/チラシURL', 'flyer_id': 'xxx', 'period': '期間'}

        Returns:
            処理結果の辞書
        """
        result = {
            'flyer_id': flyer_info.get('flyer_id'),
            'success': False,
            'image_file_ids': [],
            'document_ids': [],
            'error': None
        }

        try:
            flyer_id = flyer_info['flyer_id']
            title = flyer_info.get('title', 'タイトルなし')
            period = flyer_info.get('period', '')
            flyer_url = flyer_info.get('url', '')

            logger.info(f"チラシ処理開始: {title} (ID: {flyer_id})")

            # 1. チラシページから画像URLを取得
            images = self.scraper.get_flyer_images(flyer_info)

            if not images:
                logger.warning(f"画像が見つかりません、スキップ: {title}")
                result['success'] = True
                return result

            logger.info(f"画像を{len(images)}件取得しました")

            # 2. 各画像を処理
            for img_info in images:
                img_url = img_info['url']
                page_num = img_info['page']

                # 画像をダウンロード
                image_data = self.scraper.download_image(img_url)
                if not image_data:
                    logger.warning(f"画像のダウンロードに失敗（スキップ）: {img_url}")
                    continue

                # Google Driveに保存
                drive_result = self.save_image_to_drive(image_data, flyer_id, page_num, title)
                if not drive_result:
                    logger.error(f"画像の保存に失敗: page {page_num}")
                    continue

                file_id, actual_file_name = drive_result
                result['image_file_ids'].append(file_id)

                # 3. メタデータ準備
                full_flyer_url = f"https://tokubai.co.jp{flyer_url}" if flyer_url.startswith('/') else flyer_url

                # 4. Supabaseに基本情報のみ保存（Rawdata_FLYER_shopsテーブル）
                doc_data = {
                    # 基本情報
                    'source_type': 'flyer',
                    'workspace': 'shopping',
                    'doc_type': 'physical shop',
                    'organization': self.store_name,  # 店舗名

                    # チラシ固有情報
                    'flyer_id': f"{flyer_id}_p{page_num}",  # ページごとにユニークなID
                    'flyer_title': title,
                    'flyer_period': period,
                    'flyer_url': full_flyer_url,
                    'page_number': page_num,

                    # ファイル情報
                    'source_id': file_id,
                    'source_url': f"https://drive.google.com/file/d/{file_id}/view",
                    'file_name': actual_file_name,
                    'file_type': 'image',
                    'content_hash': hashlib.sha256(image_data).hexdigest(),

                    # OCR・テキスト情報（後で処理）
                    'attachment_text': '',
                    'summary': '',

                    # 分類・タグ
                    'tags': ['チラシ', '買い物'],

                    # 日付
                    'document_date': datetime.now().date().isoformat(),

                    # 処理ステータス
                    'processing_status': 'pending',  # 画像処理待ち
                    'processing_stage': 'tokubai_flyer_downloaded',

                    # 表示用フィールド
                    'display_subject': f"{title} (ページ {page_num})",
                    'display_sent_at': datetime.now().isoformat(),
                    'display_sender': 'トクバイ',
                    'display_post_text': period,

                    # メタデータ
                    'metadata': {
                        'image_url': img_url,
                        'store_url': self.store_url,
                        'original_flyer_id': flyer_id
                    },

                    # その他
                    'person': '共有'
                }

                try:
                    # Supabaseに保存
                    doc_result = await self.db.insert_document('Rawdata_FLYER_shops', doc_data)
                    if doc_result:
                        doc_id = doc_result.get('id')
                        result['document_ids'].append(doc_id)
                        logger.info(f"Supabase保存完了（pending状態）: {doc_id}")

                except Exception as db_error:
                    logger.error(f"Supabase保存エラー: {db_error}")
                    result['error'] = str(db_error)

            result['success'] = True
            logger.info(f"チラシ処理完了: {title} ({len(result['image_file_ids'])} images)")

        except Exception as e:
            logger.error(f"チラシ処理エラー: {e}", exc_info=True)
            result['error'] = str(e)

        return result


def load_stores_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    店舗設定ファイルを読み込む

    Args:
        config_path: 設定ファイルのパス（Noneの場合はデフォルトパス）

    Returns:
        設定データ
    """
    if config_path is None:
        config_path = Path(__file__).parent / "stores_config.json"

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info(f"店舗設定ファイルを読み込みました: {config_path}")
        return config
    except Exception as e:
        logger.error(f"店舗設定ファイルの読み込みエラー: {e}")
        return {"stores": [], "default_folder_id": None}


async def process_store(store_config: Dict[str, str], folder_id: str) -> Dict[str, Any]:
    """
    1店舗のチラシを処理

    Args:
        store_config: 店舗設定 {'name': '店舗名', 'url': 'URL', 'enabled': True}
        folder_id: Google DriveフォルダID

    Returns:
        処理結果のサマリー
    """
    store_name = store_config['name']
    store_url = store_config['url']

    logger.info("=" * 60)
    logger.info(f"店舗処理開始: {store_name}")
    logger.info("=" * 60)

    # パイプラインの初期化
    try:
        pipeline = TokubaiFlyerIngestionPipeline(
            store_url=store_url,
            flyer_folder_id=folder_id,
            store_name=store_name
        )
    except ValueError as e:
        logger.error(f"初期化エラー ({store_name}): {e}")
        return {
            'store_name': store_name,
            'success': False,
            'error': str(e),
            'results': []
        }

    # 1. 店舗ページから全チラシ情報を取得
    logger.info("店舗ページからチラシ一覧を取得中...")
    all_flyers = pipeline.scraper.get_all_flyers()

    if not all_flyers:
        logger.warning("チラシが取得できませんでした")
        return {
            'store_name': store_name,
            'success': True,
            'new_flyers': 0,
            'results': []
        }

    logger.info(f"チラシを{len(all_flyers)}件取得しました")

    # 2. 既存のチラシIDをSupabaseから取得
    flyer_ids = [f.get('flyer_id') for f in all_flyers if f.get('flyer_id')]
    existing_ids = await pipeline.check_existing_flyers(flyer_ids)

    # 3. 新着チラシを抽出
    new_flyers = [f for f in all_flyers if f.get('flyer_id') not in existing_ids]

    logger.info(f"現在のチラシ: {len(all_flyers)}件")
    logger.info(f"既存のチラシ: {len(existing_ids)}件")
    logger.info(f"新着チラシ: {len(new_flyers)}件")

    if not new_flyers:
        logger.info("新着チラシはありません")
        return {
            'store_name': store_name,
            'success': True,
            'new_flyers': 0,
            'results': []
        }

    # 4. 新着チラシを処理
    results = []
    for i, flyer in enumerate(new_flyers, 1):
        logger.info(f"[{i}/{len(new_flyers)}] 処理中: {flyer.get('title', '無題')}")
        result = await pipeline.process_single_flyer(flyer)
        results.append(result)

    # 5. サマリー
    success_count = sum(1 for r in results if r['success'])
    total_images = sum(len(r['image_file_ids']) for r in results)
    total_docs = sum(len(r['document_ids']) for r in results)

    logger.info("=" * 60)
    logger.info(f"{store_name} の処理完了")
    logger.info(f"  成功: {success_count}/{len(results)}")
    logger.info(f"  失敗: {len(results) - success_count}/{len(results)}")
    logger.info(f"  処理した画像: {total_images}件")
    logger.info(f"  登録したドキュメント: {total_docs}件（pending状態）")
    logger.info("=" * 60)

    return {
        'store_name': store_name,
        'success': True,
        'new_flyers': len(new_flyers),
        'success_count': success_count,
        'total_images': total_images,
        'total_docs': total_docs,
        'results': results
    }


async def main():
    """メインエントリーポイント"""
    logger.info("=" * 60)
    logger.info("トクバイチラシ取得パイプライン開始（複数店舗対応）")
    logger.info("=" * 60)

    # 設定ファイルを読み込む
    config = load_stores_config()

    if not config.get('stores'):
        logger.error("店舗設定が見つかりません")
        logger.info("stores_config.json を確認してください")
        return

    # 有効な店舗のみを処理
    enabled_stores = [s for s in config['stores'] if s.get('enabled', True)]
    logger.info(f"処理対象店舗: {len(enabled_stores)}件")

    # フォルダIDを取得（環境変数または設定ファイルから）
    folder_id = os.getenv("TOKUBAI_FLYER_FOLDER_ID") or config.get('default_folder_id')

    if not folder_id:
        logger.error("フォルダIDが設定されていません")
        logger.info("環境変数 TOKUBAI_FLYER_FOLDER_ID または stores_config.json で設定してください")
        return

    # 各店舗を処理
    all_store_results = []
    for i, store in enumerate(enabled_stores, 1):
        logger.info(f"\n[{i}/{len(enabled_stores)}] 店舗処理開始: {store['name']}")
        store_result = await process_store(store, folder_id)
        all_store_results.append(store_result)

    # 全体のサマリー
    logger.info("\n" + "=" * 60)
    logger.info("全店舗の処理完了")
    logger.info("=" * 60)

    total_new_flyers = sum(r.get('new_flyers', 0) for r in all_store_results)
    total_images = sum(r.get('total_images', 0) for r in all_store_results)
    total_docs = sum(r.get('total_docs', 0) for r in all_store_results)

    logger.info(f"  処理した店舗: {len(all_store_results)}件")
    logger.info(f"  新着チラシ: {total_new_flyers}件")
    logger.info(f"  処理した画像: {total_images}件")
    logger.info(f"  登録したドキュメント: {total_docs}件（pending状態）")
    logger.info("=" * 60)

    # 結果を表示
    print("\n" + "=" * 80)
    print("🛒 トクバイチラシ取得結果（複数店舗）")
    print("=" * 80)

    for store_result in all_store_results:
        print(f"\n店舗: {store_result['store_name']}")
        print(f"  新着チラシ: {store_result.get('new_flyers', 0)}件")
        if store_result.get('results'):
            print(f"  成功: {store_result.get('success_count', 0)}")
            print(f"  画像: {store_result.get('total_images', 0)}件")
            print(f"  ドキュメント: {store_result.get('total_docs', 0)}件")

    print("\n" + "=" * 80)
    print(f"合計: 新着チラシ {total_new_flyers}件、画像 {total_images}件、ドキュメント {total_docs}件")
    print("=" * 80)
    print("\n次のステップ:")
    print("  python process_queued_documents.py --workspace=household")
    print("=" * 80)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
