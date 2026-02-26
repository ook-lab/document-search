"""
早稲田アカデミーOnlineお知らせ取得パイプライン

HTML → PDF抽出・ダウンロード → Google Drive → Supabase (pending)

処理フロー:
1. HTMLファイル（window.appPropsのJSON）からお知らせ一覧を取得
2. Supabaseで既存データをチェックして新着お知らせを抽出
3. PDFリンクからPDFをダウンロードしてGoogle Driveに保存
4. Supabaseに基本情報を登録（processing_status='pending'）
5. 別途 process_queued_documents.py で処理（PDF抽出、Stage E-K）
"""
import os
import sys
import re
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
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

from shared.common.connectors.google_drive import GoogleDriveConnector
from shared.common.database.client import DatabaseClient
from waseda_academy.browser_automation import WasedaAcademyBrowser


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
        self.session_cookies = session_cookies or {}
        self.base_url = "https://online.waseda-ac.co.jp"

        # Phase 3: owner_id を取得（必須）
        self.owner_id = owner_id or os.getenv('DEFAULT_OWNER_ID')
        if not self.owner_id:
            raise ValueError(
                "owner_id が指定されていません。引数で指定するか、DEFAULT_OWNER_ID を .env に設定してください。"
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
            各ページのHTMLリスト、失敗時は空リスト
        """
        try:
            browser = WasedaAcademyBrowser(headless=True)
            html_pages, _ = await browser.run_automated_session()
            return html_pages if html_pages else []
        except Exception as e:
            logger.error(f"ブラウザ自動化エラー: {e}", exc_info=True)
            return []

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
                logger.error(f"p={i} JSONパースエラー: {e}")

        logger.info(f"お知らせを合計{len(all_notices)}件抽出しました")
        return all_notices

    async def check_existing_notices(self, notice_ids: List[str]) -> set:
        """
        Supabaseで既存のお知らせIDをチェック（file_id カラムで照合）

        Args:
            notice_ids: チェックするお知らせIDのリスト

        Returns:
            既に存在するお知らせIDのセット
        """
        try:
            result = self.db.client.table('Rawdata_FILE_AND_MAIL').select('file_id').in_(
                'file_id', notice_ids
            ).execute()

            existing_ids = {doc['file_id'] for doc in (result.data or []) if doc.get('file_id')}
            logger.info(f"既存のお知らせ: {len(existing_ids)}件")
            return existing_ids

        except Exception as e:
            logger.error(f"Supabase検索エラー: {e}")
            return set()

    async def download_pdfs_with_browser(
        self,
        pdf_info_list: List[Dict[str, str]]
    ) -> Dict[str, bytes]:
        """
        ブラウザ自動化を使用して複数のPDFを一括ダウンロード

        Args:
            pdf_info_list: [{'notice_id': 'xxx', 'pdf_url': '/notice/xxx/pdf/0', 'pdf_title': 'タイトル'}, ...]

        Returns:
            {notice_id: pdf_data}の辞書
        """
        try:
            browser = WasedaAcademyBrowser(headless=True)
            pdfs = await browser.download_pdfs_batch(pdf_info_list)
            return pdfs
        except Exception as e:
            logger.error(f"PDFバッチダウンロードエラー: {e}", exc_info=True)
            return {}

    def save_pdf_to_drive(
        self,
        pdf_data: bytes,
        pdf_title: str,
        notice_id: str
    ) -> Optional[str]:
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
        pdf_data_dict: Dict[str, bytes]
    ) -> Dict[str, Any]:
        """
        1件のお知らせを処理（PDFのみ）

        Args:
            notice: お知らせデータ
            pdf_data_dict: {notice_id: pdf_data}の辞書（事前ダウンロード済み）

        Returns:
            処理結果の辞書
        """
        result = {
            'notice_id': notice.get('id'),
            'success': False,
            'pdf_file_ids': [],
            'document_ids': [],
            'error': None
        }

        try:
            notice_id = notice.get('id')
            title = notice.get('title', 'タイトルなし')
            date = notice.get('date', '')
            message = notice.get('message', '')
            source = notice.get('source', {})
            category = notice.get('category', {})

            logger.info(f"お知らせ処理開始: {title}")

            # 日付を datetime に変換（フォーマット: 2025.12.16）
            sent_at = None
            if date:
                try:
                    sent_at = datetime.strptime(date, '%Y.%m.%d').isoformat()
                except ValueError:
                    logger.warning(f"日付のパースに失敗: {date}")

            # PDFリンクがない場合はテキストのみレコードとして保存
            pdfs = notice.get('pdfs', [])
            if not pdfs:
                logger.info(f"PDFリンクなし、テキストのみで登録: {title}")
                metadata = {
                    'notice_title': title,
                    'notice_date': date,
                    'notice_source': source.get('label', '不明'),
                    'notice_category': category.get('label', 'その他'),
                    'notice_message': message,
                }
                doc_data = {
                    'file_id': notice_id,
                    'doc_type': '早稲アカオンライン',
                    'workspace': 'waseda_academy',
                    'person': ['育哉'],
                    'organization': ['早稲田アカデミー'],
                    'metadata': metadata,
                    'processing_status': 'pending',
                    'display_subject': title,
                    'display_sent_at': sent_at,
                    'display_sender': source.get('label', '不明'),
                    'display_post_text': message,
                    'owner_id': self.owner_id
                }
                try:
                    doc_result = await self.db.insert_document('Rawdata_FILE_AND_MAIL', doc_data)
                    if doc_result:
                        result['document_ids'].append(doc_result.get('id'))
                        logger.info(f"テキストのみ保存完了: {title}")
                except Exception as db_error:
                    logger.error(f"Supabase保存エラー（テキストのみ）: {db_error}")
                    result['error'] = str(db_error)
                result['success'] = True
                return result

            # 各PDFを処理
            for pdf in pdfs:
                pdf_title = pdf.get('title', 'untitled')
                pdf_url = pdf.get('url', '')

                if not pdf_url:
                    logger.warning(f"PDFのURLが空: {pdf_title}")
                    continue

                # 1. 事前ダウンロード済みのPDFデータを取得
                pdf_data = pdf_data_dict.get(notice_id)
                if not pdf_data:
                    logger.warning(f"PDFデータが見つかりません（スキップ）: {pdf_title}")
                    continue

                # 2. PDFをGoogle Driveに保存
                file_id, actual_file_name = self.save_pdf_to_drive(pdf_data, pdf_title, notice_id)
                if not file_id:
                    logger.error(f"PDFの保存に失敗: {pdf_title}")
                    continue

                result['pdf_file_ids'].append(file_id)

                # 3. メタデータ準備
                # 完全なPDF URLを構築
                if pdf_url.startswith('http'):
                    full_pdf_url = pdf_url
                elif pdf_url.startswith('/'):
                    full_pdf_url = f"{self.base_url}{pdf_url}"
                else:
                    full_pdf_url = f"{self.base_url}/{pdf_url}"

                metadata = {
                    'notice_title': title,
                    'notice_date': date,
                    'notice_source': source.get('label', '不明'),
                    'notice_category': category.get('label', 'その他'),
                    'notice_message': message,
                    'pdf_url': full_pdf_url,
                    'pdf_title': pdf_title
                }

                # 5. Supabaseに基本情報のみ保存
                doc_data = {
                    'file_url': f"https://drive.google.com/file/d/{file_id}/view",
                    'file_id': notice_id,
                    'file_name': actual_file_name,
                    'doc_type': '早稲アカオンライン',  # 固定値
                    'workspace': 'waseda_academy',
                    'person': ['育哉'],  # 担当者（配列形式）
                    'organization': ['早稲田アカデミー'],  # 組織（配列形式）
                    'metadata': metadata,
                    'processing_status': 'pending',
                    # 表示用フィールド
                    'display_subject': title,
                    'display_sent_at': sent_at,
                    'display_sender': source.get('label', '不明'),
                    'display_post_text': message,
                    # Phase 3: owner_id 必須
                    'owner_id': self.owner_id
                }

                try:
                    # Supabaseに保存
                    doc_result = await self.db.insert_document('Rawdata_FILE_AND_MAIL', doc_data)
                    if doc_result:
                        doc_id = doc_result.get('id')
                        result['document_ids'].append(doc_id)
                        logger.info(f"Supabase保存完了（pending状態）: {doc_id}")
                        logger.info(f"  → process_queued_documents.py で処理してください")

                except Exception as db_error:
                    logger.error(f"Supabase保存エラー: {db_error}")
                    result['error'] = str(db_error)

            result['success'] = True
            logger.info(f"お知らせ処理完了: {title} ({len(result['pdf_file_ids'])} PDFs)")

        except Exception as e:
            logger.error(f"お知らせ処理エラー: {e}", exc_info=True)
            result['error'] = str(e)

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
        temp_html_file = Path(__file__).parent.parent.parent / "waseda_notice_page.html"
        with open(temp_html_file, 'w', encoding='utf-8') as f:
            f.write(html_content[0])
        logger.info(f"取得したHTMLを保存（1ページ目）: {temp_html_file}")
    else:
        # ローカルHTMLファイルから読み込み（デバッグ用）
        html_file = Path(__file__).parent.parent.parent / "pasted_content.txt"

        if not html_file.exists():
            logger.error(f"HTMLファイルが見つかりません: {html_file}")
            logger.info("ヒント: --browser オプションでブラウザ自動化を使用できます")
            logger.info("  python -m B_ingestion.waseda_academy.notice_ingestion --browser")
            return

        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()

    # お知らせデータを抽出
    current_notices = pipeline.extract_notice_data(html_content)
    if not current_notices:
        logger.warning("お知らせが抽出できませんでした")
        return

    # 既存のお知らせIDをSupabaseから取得
    notice_ids = [n.get('id') for n in current_notices if n.get('id')]
    existing_ids = await pipeline.check_existing_notices(notice_ids)

    # 新着お知らせを抽出
    new_notices = [n for n in current_notices if n.get('id') not in existing_ids]

    logger.info(f"現在のお知らせ: {len(current_notices)}件")
    logger.info(f"既存のお知らせ: {len(existing_ids)}件")
    logger.info(f"新着お知らせ: {len(new_notices)}件")

    if not new_notices:
        logger.info("新着お知らせはありません")
        return

    # 新着お知らせからPDF情報を収集
    pdf_info_list = []
    for notice in new_notices:
        notice_id = notice.get('id')
        pdfs = notice.get('pdfs', [])
        for pdf in pdfs:
            pdf_title = pdf.get('title', 'untitled')
            pdf_url = pdf.get('url', '')
            if pdf_url:
                pdf_info_list.append({
                    'notice_id': notice_id,
                    'pdf_url': pdf_url,
                    'pdf_title': pdf_title
                })

    logger.info(f"ダウンロード対象のPDF: {len(pdf_info_list)}件")

    # PDFを一括ダウンロード（ブラウザ自動化）
    pdf_data_dict = {}
    if pdf_info_list:
        logger.info("ブラウザ自動化でPDFを一括ダウンロード中...")
        pdf_data_dict = await pipeline.download_pdfs_with_browser(pdf_info_list)
        logger.info(f"ダウンロード完了: {len(pdf_data_dict)}/{len(pdf_info_list)}件")

    # 新着お知らせを処理（PDFデータは既にダウンロード済み）
    results = []
    for i, notice in enumerate(new_notices, 1):
        logger.info(f"[{i}/{len(new_notices)}] 処理中...")
        result = await pipeline.process_single_notice(notice, pdf_data_dict)
        results.append(result)

    # サマリー
    success_count = sum(1 for r in results if r['success'])
    total_pdfs = sum(len(r['pdf_file_ids']) for r in results)
    total_docs = sum(len(r['document_ids']) for r in results)

    logger.info("=" * 60)
    logger.info("処理完了")
    logger.info(f"  成功: {success_count}/{len(results)}")
    logger.info(f"  失敗: {len(results) - success_count}/{len(results)}")
    logger.info(f"  処理したPDF: {total_pdfs}件")
    logger.info(f"  登録したドキュメント: {total_docs}件（pending状態）")
    logger.info("=" * 60)
    logger.info("")
    logger.info("次のステップ:")
    logger.info("  python process_queued_documents.py --workspace=waseda_academy")
    logger.info("=" * 60)

    # 結果を表示
    print("\n" + "=" * 80)
    print("📢 早稲田アカデミーお知らせ取得結果")
    print("=" * 80)

    for result in results:
        print(f"\nNotice ID: {result['notice_id']}")
        print(f"  Success: {result['success']}")
        print(f"  PDFs: {len(result['pdf_file_ids'])}")
        for file_id in result['pdf_file_ids']:
            print(f"    - https://drive.google.com/file/d/{file_id}/view")
        print(f"  Documents: {len(result['document_ids'])} (pending)")
        if result['error']:
            print(f"  ❌ Error: {result['error']}")

    print("\n" + "=" * 80)
    print("次のステップ:")
    print("  python process_queued_documents.py --workspace=waseda_academy")
    print("=" * 80)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
