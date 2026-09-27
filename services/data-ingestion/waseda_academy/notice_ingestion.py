"""
早稲田アカデミーOnlineお知らせ取得パイプライン

HTML → PDF抽出・ダウンロード → Google Drive → Supabase (pending)

処理フロー:
1. HTMLファイル（window.appPropsのJSON）からお知らせ一覧を取得
2. オプション: --reingest-all-scraped / --reingest-post-ids=… で raw と Drive を整理してから既存チェック
3. Supabaseで既存データをチェックして新着お知らせを抽出
4. PDFリンクからPDFをダウンロードしてGoogle Driveに保存
5. Supabaseに基本情報を登録（processing_status='pending'）
6. 別途 process_queued_documents.py で処理（PDF抽出、Stage E-K）
"""
import os
import sys
import re
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set
from datetime import datetime
from loguru import logger

# プロジェクトルートと data-ingestion をパスに追加
root_dir = Path(__file__).parent.parent.parent.parent
ingestion_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(ingestion_dir))

# .envファイルを読み込む
from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

from dms.common.connectors.google_drive import GoogleDriveConnector
from dms.common.database.client import DatabaseClient
from waseda_academy.browser_automation import WasedaAcademyBrowser


def parse_reingest_cli(argv: List[str]) -> Tuple[bool, List[str]]:
    """
    --reingest-all-scraped … 今回のHTMLに出た全 post_id を再取り込み対象にする
    --reingest-post-ids=id1,id2 … 指定した post_id だけ
    """
    reingest_all = '--reingest-all-scraped' in argv
    post_ids: List[str] = []
    for arg in argv:
        if arg.startswith('--reingest-post-ids='):
            rest = arg.split('=', 1)[1]
            post_ids.extend([p.strip() for p in rest.split(',') if p.strip()])
    dedup: List[str] = list(dict.fromkeys(post_ids))
    return reingest_all, dedup


class WasedaNoticeIngestionPipeline:
    """早稲田アカデミーOnlineお知らせ取得パイプライン"""

    def __init__(
        self,
        pdf_folder_id: Optional[str] = None,
        session_cookies: Optional[Dict[str, str]] = None,
        owner_id: Optional[str] = None
    ):
        """
        Args:
            pdf_folder_id: PDF保存先のDriveフォルダID（Noneの場合は環境変数から取得）
            session_cookies: 早稲田アカデミーOnlineのセッションクッキー（PDF取得用）
            owner_id: オーナーID（Supabase Auth ユーザーID、省略時は環境変数から取得）
        """
        self.pdf_folder_id = pdf_folder_id or os.getenv("WASEDA_PDF_FOLDER_ID")
        if not self.pdf_folder_id:
            raise ValueError(
                "pdf_folder_id が指定されていません。引数で指定するか、WASEDA_PDF_FOLDER_ID を .env に設定してください。"
            )
        self.session_cookies = session_cookies or {}
        self.base_url = "https://online.waseda-ac.co.jp"

        # Phase 3: owner_id を取得（必須）
        self.owner_id = owner_id or os.getenv('SUPABASE_ADMIN_USER_ID')
        if not self.owner_id:
            raise ValueError(
                "owner_id が指定されていません。引数で指定するか、SUPABASE_ADMIN_USER_ID を .env に設定してください。"
            )

        # コネクタの初期化
        self.drive = GoogleDriveConnector()
        self.db = DatabaseClient(use_service_role=True)

        logger.info(f"WasedaNoticeIngestionPipeline初期化完了")
        logger.info(f"  - PDF folder: {self.pdf_folder_id}")

    async def fetch_html_with_browser(self) -> List[str]:
        """
        ブラウザ自動化を使用して全ページのHTMLを取得

        Returns:
            各ページのHTMLリスト
        """
        try:
            browser = WasedaAcademyBrowser(headless=True)
            html_pages, _ = await browser.run_automated_session()
            return html_pages if html_pages else []
        except Exception as e:
            logger.error(f"ブラウザ自動化エラー: {e}", exc_info=True)
            raise

    def extract_notice_data(self, html_content) -> List[Dict[str, Any]]:
        """
        HTMLコンテンツからお知らせデータを抽出する
        データはwindow.appPropsというJavaScript変数内のJSONとして埋め込まれている

        Args:
            html_content: HTMLコンテンツ全体

        Returns:
            お知らせデータのリスト
        """
        # リスト（複数ページ）でも単一文字列でも受け付ける
        pages = html_content if isinstance(html_content, list) else [html_content]

        all_notices = []
        seen_ids = set()
        for i, page_html in enumerate(pages, 1):
            match = re.search(r'window\.appProps\s*=\s*(\{.*?\});', page_html, re.DOTALL)
            if not match:
                logger.warning(f"p={i}: window.appPropsが見つかりませんでした")
                continue
            try:
                app_props = json.loads(match.group(1))
                notices = app_props['page']['noticeList']['_0']['notices']
                new_count = 0
                for n in notices:
                    nid = n.get('id')
                    if nid and nid not in seen_ids:
                        seen_ids.add(nid)
                        all_notices.append(n)
                        new_count += 1
                logger.info(f"  [p={i}] {new_count}件")
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"p={i} JSONパースエラーまたはキー不整合: {e}")
                raise

        logger.info(f"お知らせを合計{len(all_notices)}件抽出しました")
        return all_notices

    async def check_existing_notices(self, notice_ids: List[str]) -> Set[Tuple[str, Optional[int]]]:
        """
        Supabaseで既存の (post_id, pdf_slot) をチェック
        （05_ikuya_waseaca_01_raw.post_id と pdf_slot で照合）

        Args:
            notice_ids: チェックするお知らせIDのリスト

        Returns:
            既に存在する (post_id, pdf_slot) のセット。既存行で pdf_slot が NULL の場合は (post_id, None) となる。
        """
        try:
            result = self.db.client.table('05_ikuya_waseaca_01_raw').select('post_id, pdf_slot').in_(
                'post_id', notice_ids
            ).execute()

            existing_slots: Set[Tuple[str, Optional[int]]] = {
                (doc['post_id'], doc.get('pdf_slot'))
                for doc in (result.data or [])
                if doc.get('post_id')
            }
            logger.info(f"既存の取込済みスロット: {len(existing_slots)}件")
            return existing_slots

        except Exception as e:
            logger.error(f"Supabase検索エラー: {e}")
            raise

    @staticmethod
    def _drive_file_id_from_file_url(file_url: Optional[str]) -> Optional[str]:
        if not file_url:
            return None
        m = re.search(r'/file/d/([a-zA-Z0-9_-]+)', file_url)
        return m.group(1) if m else None

    def prepare_post_reingest(self, post_ids: List[str]) -> int:
        """
        指定 post_id の 05 raw を削除する（DB トリガーで pipeline_meta / 09 も連動削除）。
        file_url がある行は、先に対応する Drive ファイルをゴミ箱へ移す。
        """
        if not post_ids:
            return 0
        post_ids = list(dict.fromkeys(post_ids))
        try:
            sel = self.db.client.table('05_ikuya_waseaca_01_raw').select(
                'id', 'file_url', 'post_id'
            ).in_('post_id', post_ids).execute()
            rows = sel.data or []
        except Exception as e:
            logger.error(f"再取り込み前の raw 取得に失敗: {e}")
            raise

        trashed_drive: Set[str] = set()
        for row in rows:
            fid = self._drive_file_id_from_file_url(row.get('file_url'))
            if not fid or fid in trashed_drive:
                continue
            trashed_drive.add(fid)
            if self.drive.trash_file(fid):
                logger.info(f"Drive をゴミ箱へ: {fid}")
            else:
                logger.warning(f"Drive ゴミ箱移動に失敗（続行）: {fid}")

        try:
            self.db.client.table('05_ikuya_waseaca_01_raw').delete().in_(
                'post_id', post_ids
            ).execute()
        except Exception as e:
            logger.error(f"05_ikuya_waseaca_01_raw の削除に失敗: {e}")
            raise

        logger.info(
            f"再取り込み準備: post_id {len(post_ids)} 種 / raw {len(rows)} 行を削除 "
            f"（Drive ゴミ箱試行 {len(trashed_drive)} 件）"
        )
        return len(rows)

    async def download_pdfs_with_browser(
        self,
        pdf_info_list: List[Dict[str, Any]]
    ) -> Dict[Tuple[str, int], bytes]:
        """
        ブラウザ自動化を使用して複数のPDFを一括ダウンロード

        Args:
            pdf_info_list: [{'notice_id', 'pdf_url', 'pdf_title', 'pdf_slot'}, ...]
                pdf_slot は同一お知らせ内の添付順（0始まり）。url が共通の添付を区別する。

        Returns:
            {(notice_id, pdf_slot): pdf_data} の辞書
        """
        try:
            browser = WasedaAcademyBrowser(headless=True)
            pdfs = await browser.download_pdfs_batch(pdf_info_list)
            return pdfs
        except Exception as e:
            logger.error(f"PDFバッチダウンロードエラー: {e}", exc_info=True)
            raise

    def save_pdf_to_drive(
        self,
        pdf_data: bytes,
        pdf_title: str,
        notice_id: str
    ) -> Tuple[Optional[str], str]:
        """
        PDFをGoogle Driveに保存

        Args:
            pdf_data: PDFのバイトデータ
            pdf_title: PDFのタイトル
            notice_id: お知らせID

        Returns:
            DriveのファイルID、失敗時はNone
        """
        file_name = f"{pdf_title}.pdf"

        # Driveにアップロード
        file_id = self.drive.upload_file(
            file_content=pdf_data,
            file_name=file_name,
            mime_type='application/pdf',
            folder_id=self.pdf_folder_id
        )

        if file_id:
            logger.info(f"PDFをDriveに保存: {file_name}")
        else:
            logger.error(f"PDFの保存に失敗: {file_name}")

        return file_id, file_name

    async def process_single_notice(
        self,
        notice: Dict[str, Any],
        pdf_data_dict: Dict[Tuple[str, int], bytes],
        existing_slots: Set[Tuple[str, Optional[int]]],
    ) -> Dict[str, Any]:
        """
        1件のお知らせを処理（PDFのみ）

        Args:
            notice: お知らせデータ
            pdf_data_dict: {(notice_id, pdf_slot): pdf_data} の辞書（事前ダウンロード済み）
            existing_slots: 既にDBに存在する (post_id, pdf_slot) のセット

        Returns:
            処理結果の辞書
        """
        result = {
            'notice_id': notice.get('id'),
            'success': False,
            'skipped': False,
            'pdf_file_ids': [],
            'document_ids': [],
            'error': None
        }

        existing_set = existing_slots

        try:
            notice_id = notice.get('id')
            if not notice_id:
                raise ValueError("お知らせID (id) が欠損しています")

            title = notice.get('title')
            if not title:
                raise ValueError(f"お知らせタイトル (title) が欠損しています (post_id={notice_id})")

            date = notice.get('date')
            message = notice.get('message')
            source = notice.get('source')
            category = notice.get('category')

            logger.info(f"お知らせ処理開始: {title}")

            # 日付を datetime に変換（フォーマット: 2025.12.16）
            sent_at = None
            if date:
                try:
                    sent_at = datetime.strptime(date, '%Y.%m.%d').isoformat()
                except ValueError as e:
                    raise ValueError(f"日付のパースに失敗: date={date} (post_id={notice_id}): {e}")

            # カテゴリ名・発信元名（欠損時は推測値で埋めず None）
            category_name = category.get('label') if isinstance(category, dict) else None
            creator_name = source.get('label') if isinstance(source, dict) else None

            # 【計画修正】post_id に pdf_slot が NULL の行が1件でもあるお知らせは旧形式取込済み(またはテキストのみ取込済み)として全体をスキップ
            if (notice_id, None) in existing_set:
                logger.info(
                    f"旧形式またはテキストのみで取込済みのため全体をスキップ: "
                    f"{title} (post_id={notice_id}, pdf_slotがNULLの行が存在)"
                )
                result['skipped'] = True
                result['success'] = False
                return result

            # PDFリンクがない場合はテキストのみレコードとして保存（新規テキストのみ投稿は pdf_slot NULL で保存）
            pdfs = notice.get('pdfs')
            if not pdfs:
                logger.info(f"PDFリンクなし、テキストのみで登録: {title}")
                raw_row = {
                    'person': '育哉',
                    'source': '早稲アカオンライン',
                    'category': category_name,
                    'post_id': notice_id,
                    'post_type': 'notice',
                    'title': title,
                    'description': message,
                    'creator_name': creator_name,
                    'created_at': sent_at,
                }
                raw_result = self.db.client.table('05_ikuya_waseaca_01_raw').insert(raw_row).execute()
                raw_id = raw_result.data[0]['id'] if raw_result.data else None
                if not raw_id:
                    raise RuntimeError(f"05_ikuya_waseaca_01_raw INSERT 失敗（データ空）: {title}")

                self.db.client.table('pipeline_meta').insert({
                    'raw_id': raw_id,
                    'raw_table': '05_ikuya_waseaca_01_raw',
                    'person': '育哉',
                    'source': '早稲アカオンライン',
                    'processing_status': 'pending',
                    'owner_id': self.owner_id,
                }).execute()
                result['document_ids'].append(raw_id)
                result['success'] = True
                logger.info(f"テキストのみ保存完了: {title} → raw_id={raw_id}")
                return result

            # 各PDFを処理（pdf_slot で区別: API 上で url が同一の添付が複数ある）
            processed_any_pdf = False
            for pdf_slot, pdf in enumerate(pdfs):
                if (notice_id, pdf_slot) in existing_set:
                    logger.info(f"既に取込済みのためスキップ: {title} (post_id={notice_id}, slot={pdf_slot})")
                    continue

                pdf_title = pdf.get('title')
                if not pdf_title:
                    raise ValueError(f"PDFタイトルが欠損しています: post_id={notice_id}, slot={pdf_slot}")

                pdf_url = pdf.get('url')
                if not pdf_url:
                    raise ValueError(f"PDFのURLが欠損しています: post_id={notice_id}, slot={pdf_slot}")

                # 1. 事前ダウンロード済みのPDFデータを取得
                pdf_data = pdf_data_dict.get((notice_id, pdf_slot))
                if not pdf_data:
                    raise RuntimeError(f"PDFデータが見つかりません（ダウンロード失敗または欠損）: {pdf_title} (post_id={notice_id}, slot={pdf_slot})")

                # 2. PDFをGoogle Driveに保存
                file_id, actual_file_name = self.save_pdf_to_drive(pdf_data, pdf_title, notice_id)
                if not file_id:
                    raise RuntimeError(f"PDFのDrive保存に失敗: {pdf_title} (post_id={notice_id}, slot={pdf_slot})")

                result['pdf_file_ids'].append(file_id)

                # 5. Supabaseに基本情報のみ保存（05_ikuya_waseaca_01_raw + pipeline_meta）
                raw_row = {
                    'person': '育哉',
                    'source': '早稲アカオンライン',
                    'category': category_name,
                    'post_id': notice_id,
                    'post_type': 'notice',
                    'title': title,
                    'description': message,
                    'creator_name': creator_name,
                    'created_at': sent_at,
                    'file_url': f"https://drive.google.com/file/d/{file_id}/view",
                    'file_name': actual_file_name,
                    'pdf_slot': pdf_slot,
                }

                raw_result = self.db.client.table('05_ikuya_waseaca_01_raw').insert(raw_row).execute()
                raw_id = raw_result.data[0]['id'] if raw_result.data else None
                if not raw_id:
                    raise RuntimeError(f"05_ikuya_waseaca_01_raw INSERT 失敗（データ空）: {title} (slot={pdf_slot})")

                self.db.client.table('pipeline_meta').insert({
                    'raw_id': raw_id,
                    'raw_table': '05_ikuya_waseaca_01_raw',
                    'person': '育哉',
                    'source': '早稲アカオンライン',
                    'processing_status': 'pending',
                    'owner_id': self.owner_id,
                }).execute()
                result['document_ids'].append(raw_id)
                processed_any_pdf = True
                logger.info(f"Supabase保存完了（pending状態）: raw_id={raw_id} (slot={pdf_slot})")
                logger.info(f"  → pipeline_meta.id を確認のうえ scripts/processing/process_queued_documents.py --doc-id <uuid> --execute で処理してください")

            if processed_any_pdf:
                result['success'] = True
                logger.info(f"お知らせ処理完了: {title} ({len(result['pdf_file_ids'])} PDFs)")
            else:
                result['skipped'] = True
                result['success'] = False
                logger.info(f"全添付PDFが取込済みのため処理をスキップしました: {title}")

        except Exception as e:
            logger.error(f"お知らせ処理エラー: {e}", exc_info=True)
            result['error'] = str(e)
            result['success'] = False

        return result



async def main():
    """メインエントリーポイント"""
    import sys

    # パイプラインの初期化
    pipeline = WasedaNoticeIngestionPipeline()

    # コマンドライン引数でモードを選択
    use_browser = "--browser" in sys.argv or "--auto" in sys.argv

    html_content = None

    if use_browser:
        # ブラウザ自動化で全ページHTMLを取得
        logger.info("ブラウザ自動化モード: ログイン → 全ページHTML取得")
        html_content = await pipeline.fetch_html_with_browser()

        if not html_content:
            logger.error("HTMLの取得に失敗しました")
            return

        # 1ページ目をデバッグ用に保存
        temp_html_file = Path(__file__).parent / "waseda_notice_page.html"
        with open(temp_html_file, 'w', encoding='utf-8') as f:
            f.write(html_content[0])
        logger.info(f"取得したHTMLを保存（1ページ目）: {temp_html_file}")
    else:
        # ローカルHTMLファイルから読み込み（デバッグ用）
        html_file = Path(__file__).parent / "pasted_content.txt"

        if not html_file.exists():
            logger.error(f"HTMLファイルが見つかりません: {html_file}")
            logger.info("ヒント: --browser オプションでブラウザ自動化を使用できます")
            logger.info(
                "  再取り込み: --browser --reingest-all-scraped "
                "または --browser --reingest-post-ids=id1,id2"
            )
            return

        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()

    # お知らせデータを抽出
    current_notices = pipeline.extract_notice_data(html_content)
    if not current_notices:
        logger.warning("お知らせが抽出できませんでした")
        return

    notice_ids = [n.get('id') for n in current_notices if n.get('id')]

    reingest_all, reingest_post_ids_arg = parse_reingest_cli(sys.argv)
    if reingest_all:
        logger.warning(
            "再取り込み（全件）: 今回のHTMLに出ている全 post_id について "
            "05_ikuya_waseaca_01_raw と関連を削除し、Drive の添付PDFをゴミ箱へ移します。"
        )
        pipeline.prepare_post_reingest(notice_ids)
    elif reingest_post_ids_arg:
        logger.warning(
            f"再取り込み（指定 {len(reingest_post_ids_arg)} 件）: "
            "該当 post_id の raw と Drive 添付を整理します。"
        )
        pipeline.prepare_post_reingest(reingest_post_ids_arg)

    # 既存の (post_id, pdf_slot) をSupabaseから取得
    existing_slots = await pipeline.check_existing_notices(notice_ids)

    # 処理対象のお知らせを抽出
    notices_to_process = []
    for n in current_notices:
        nid = n.get('id')
        if not nid:
            continue

        # 【計画への修正】post_id に pdf_slot が NULL の行が1件でもあるお知らせは
        # 旧形式取込済み(またはテキストのみ取込済み)として、そのお知らせ全体をスキップし、ログに明示
        if (nid, None) in existing_slots:
            logger.info(
                f"旧形式またはテキストのみで取込済みのため全体をスキップ: "
                f"{n.get('title')} (post_id={nid}, pdf_slotがNULLの行が存在)"
            )
            continue

        pdfs = n.get('pdfs')
        if not pdfs:
            # 新規テキストのみ投稿（(nid, None) が無いので未取込）
            notices_to_process.append(n)
        else:
            # 未取込の pdf_slot が1つでもあるかチェック
            unprocessed_slots = [slot for slot in range(len(pdfs)) if (nid, slot) not in existing_slots]
            if unprocessed_slots:
                notices_to_process.append(n)
            else:
                logger.info(f"全添付PDFが取込済みのためスキップ: {n.get('title')} (post_id={nid})")

    logger.info(f"現在のお知らせ: {len(current_notices)}件")
    logger.info(f"既存の取込済みスロット: {len(existing_slots)}件")
    logger.info(f"処理対象のお知らせ: {len(notices_to_process)}件")

    if not notices_to_process:
        logger.info("新着・未取込のお知らせはありません")
        return

    # 未取込のPDFのみをダウンロード対象として収集
    pdf_info_list = []
    for notice in notices_to_process:
        notice_id = notice.get('id')
        if not notice_id:
            logger.error(f"お知らせIDが欠損しています: {notice}")
            raise ValueError(f"お知らせIDが欠損しています: {notice}")
        pdfs = notice.get('pdfs')
        if pdfs:
            for pdf_slot, pdf in enumerate(pdfs):
                if (notice_id, pdf_slot) in existing_slots:
                    continue
                pdf_title = pdf.get('title')
                if not pdf_title:
                    logger.error(f"PDFタイトルが欠損しています: notice_id={notice_id}, slot={pdf_slot}")
                    raise ValueError(f"PDFタイトルが欠損しています: notice_id={notice_id}, slot={pdf_slot}")
                pdf_url = pdf.get('url')
                if not pdf_url:
                    logger.error(f"PDFのURLが欠損しています: notice_id={notice_id}, slot={pdf_slot}")
                    raise ValueError(f"PDFのURLが欠損しています: notice_id={notice_id}, slot={pdf_slot}")
                pdf_info_list.append({
                    'notice_id': notice_id,
                    'pdf_url': pdf_url,
                    'pdf_title': pdf_title,
                    'pdf_slot': pdf_slot,
                })

    logger.info(f"ダウンロード対象のPDF: {len(pdf_info_list)}件")

    # PDFを一括ダウンロード（ブラウザ自動化）
    pdf_data_dict = {}
    if pdf_info_list:
        logger.info("ブラウザ自動化でPDFを一括ダウンロード中...")
        pdf_data_dict = await pipeline.download_pdfs_with_browser(pdf_info_list)
        logger.info(f"ダウンロード完了: {len(pdf_data_dict)}/{len(pdf_info_list)}件")
        if len(pdf_data_dict) != len(pdf_info_list):
            missing_count = len(pdf_info_list) - len(pdf_data_dict)
            logger.error(f"PDFダウンロードで未取得が発生しました: {missing_count}件欠損（不足分のお知らせは個別処理内で失敗として記録されます）")

    # お知らせを処理（PDFデータは既にダウンロード済み）
    results = []
    for i, notice in enumerate(notices_to_process, 1):
        logger.info(f"[{i}/{len(notices_to_process)}] 処理中...")
        result = await pipeline.process_single_notice(notice, pdf_data_dict, existing_slots=existing_slots)
        results.append(result)

    # サマリー
    success_count = sum(1 for r in results if r['success'])
    skipped_count = sum(1 for r in results if r.get('skipped'))
    failure_count = sum(1 for r in results if not r['success'] and not r.get('skipped'))
    total_pdfs = sum(len(r['pdf_file_ids']) for r in results)
    total_docs = sum(len(r['document_ids']) for r in results)

    logger.info("=" * 60)
    logger.info("処理完了")
    logger.info(f"  成功: {success_count}/{len(results)}")
    logger.info(f"  スキップ: {skipped_count}/{len(results)}")
    logger.info(f"  失敗: {failure_count}/{len(results)}")
    logger.info(f"  処理したPDF: {total_pdfs}件")
    logger.info(f"  登録したドキュメント: {total_docs}件（pending状態）")
    logger.info("=" * 60)
    logger.info("")
    logger.info("次のステップ:")
    logger.info("  python scripts/processing/process_queued_documents.py --doc-id <pipeline_meta.id> --execute")
    logger.info("=" * 60)

    # 結果を表示
    print("\n" + "=" * 80)
    print("📢 早稲田アカデミーお知らせ取得結果")
    print("=" * 80)

    for result in results:
        print(f"\nNotice ID: {result['notice_id']}")
        status_str = "Success" if result['success'] else ("Skipped" if result.get('skipped') else "Failed")
        print(f"  Status: {status_str}")
        print(f"  PDFs: {len(result['pdf_file_ids'])}")
        for file_id in result['pdf_file_ids']:
            print(f"    - https://drive.google.com/file/d/{file_id}/view")
        print(f"  Documents: {len(result['document_ids'])} (pending)")
        if result['error']:
            print(f"  ❌ Error: {result['error']}")

    print("\n" + "=" * 80)
    print("次のステップ:")
    print("  python scripts/processing/process_queued_documents.py --doc-id <pipeline_meta.id> --execute")
    print("=" * 80)

    if failure_count > 0:
        logger.error(f"処理完了（失敗あり）: {failure_count}件のお知らせの処理に失敗しました")
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
